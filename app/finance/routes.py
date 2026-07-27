"""Pagine delle finanze: la panoramica è l'unica pagina (card portafogli,
movimento nuovo, sintesi del mese, TUTTI i movimenti)."""
import json
from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from shared.templating import templates
from shared.parsing import to_float, to_datetime
from shared import ai, settings_store
from finance import service
from finance.models import TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO, TIPO_GIRO

router = APIRouter()


def _oggi_local():
    # ora LOCALE del PC (l'app è locale): utcnow() precompilava il form 1-2 ore indietro
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _lettura_ai_salvata():
    """L'ultima 'lettura AI' delle finanze, se generata (persistente)."""
    raw = settings_store.get_setting("fin_ai", "")
    if not raw:
        return None
    try:
        saved = json.loads(raw)
        return {"text": saved.get("text", ""), "conf": saved.get("conf", "media")}
    except json.JSONDecodeError:
        return None


def _ctx_panoramica() -> dict:
    now = datetime.now()
    return {
        "active": "finanze",
        "saldi": service.saldi(),
        "riep": service.riepilogo_mese(now.year, now.month),
        "movimenti": service.lista_movimenti(),      # TUTTI, data desc
        "wallets": service.wallets(),
        "categorie": service.categorie(),
        "tipi": (TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO, TIPO_GIRO),
        "giri_aperti": service.giri_aperti(),        # riquadro "In attesa di rimborso"
        "controparti": service.controparti(),        # suggerimenti "da chi"
        "oggi": _oggi_local(),
        "ai_on": ai.is_configured(),
        "lettura_ai": _lettura_ai_salvata(),
    }


# ------------------------------ panoramica ------------------------------
@router.get("/finanze", response_class=HTMLResponse)
def panoramica(request: Request):
    # modalità Proattiva: la lettura si rinfresca da sola in background
    ai.forse_rigenera("fin_ai", _genera_lettura_finanze)
    ctx = _ctx_panoramica()
    ctx["ai_proattivo"] = ai.proattivo_attivo()
    return templates.TemplateResponse(request, "finance_overview.html", ctx)


def _aggiorna_grafico_patrimonio():
    """Un movimento cambia la liquidità: rigenera (in background) la cache del
    grafico del patrimonio così si aggiorna senza aspettare il riavvio."""
    try:
        from portfolio import wealth
        wealth.rebuild_async()
    except Exception:
        pass  # il grafico è un extra: mai far fallire il salvataggio


def _zip_spese(importi, wallets, categorie, descrizioni, date):
    out = []
    for i, imp in enumerate(importi):
        amount = to_float(imp, 0.0) or 0.0
        w = wallets[i] if i < len(wallets) else ""
        if amount > 0 and (w or "").strip().isdigit():
            out.append({
                "importo": amount, "wallet_id": int(w),
                "categoria": categorie[i] if i < len(categorie) else "",
                "descrizione": descrizioni[i] if i < len(descrizioni) else "",
                "data": to_datetime(date[i]) if i < len(date) and date[i] else None})
    return out


def _zip_rientri(importi, wallets, chi, date):
    out = []
    for i, imp in enumerate(importi):
        amount = to_float(imp, 0.0) or 0.0
        w = wallets[i] if i < len(wallets) else ""
        if amount > 0 and (w or "").strip().isdigit():
            out.append({
                "importo": amount, "wallet_id": int(w),
                "controparte": chi[i] if i < len(chi) else "",
                "data": to_datetime(date[i]) if i < len(date) and date[i] else None})
    return out


@router.post("/finanze/movimenti/salva")
def salva_movimento(
    tipo: str = Form(...),
    data: str = Form(""),
    importo: str = Form("0"),
    wallet_id: str = Form(""),
    wallet_to_id: str = Form(""),
    categoria: str = Form(""),
    descrizione: str = Form(""),
    # --- partita di giro: spese e rientri sono liste (più operazioni) ---
    giro_dopo: str = Form(""),             # checkbox: il rimborso arriverà dopo
    spesa_importo: list[str] = Form([]),
    spesa_wallet: list[str] = Form([]),
    spesa_categoria: list[str] = Form([]),
    spesa_descrizione: list[str] = Form([]),
    spesa_data: list[str] = Form([]),
    rientro_importo: list[str] = Form([]),
    rientro_wallet: list[str] = Form([]),
    rientro_chi: list[str] = Form([]),
    rientro_data: list[str] = Form([]),
    next: str = Form("/finanze"),
):
    if tipo in (TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO):
        wid = int(wallet_id) if (wallet_id or "").strip().isdigit() else None
        wto = int(wallet_to_id) if (wallet_to_id or "").strip().isdigit() else None
        if wid:
            service.crea_movimento(
                tipo=tipo, data=to_datetime(data), importo=to_float(importo, 0.0) or 0.0,
                wallet_id=wid, wallet_to_id=wto, categoria_nome=categoria,
                descrizione=descrizione)
    elif tipo == TIPO_GIRO:
        service.crea_giro(
            spese=_zip_spese(spesa_importo, spesa_wallet, spesa_categoria,
                             spesa_descrizione, spesa_data),
            rientri=_zip_rientri(rientro_importo, rientro_wallet, rientro_chi, rientro_data),
            aperta=bool(giro_dopo))
    _aggiorna_grafico_patrimonio()
    dest = next if next.startswith("/finanze") else "/finanze"
    return RedirectResponse(dest, status_code=303)


@router.post("/finanze/movimenti/{tid}/elimina")
def elimina_movimento(tid: int, next: str = Form("/finanze")):
    service.elimina_movimento(tid)
    _aggiorna_grafico_patrimonio()
    dest = next if next.startswith("/finanze") else "/finanze"
    return RedirectResponse(dest, status_code=303)


@router.get("/finanze/movimenti/{tid}/modifica", response_class=HTMLResponse)
def modifica_movimento(request: Request, tid: int):
    """Riapre la panoramica col modulo PRECOMPILATO sul movimento scelto.
    Per una partita di giro precompila TUTTE le gambe (si modifica insieme)."""
    edit = service.dati_modifica(tid)
    if not edit:
        return RedirectResponse("/finanze", status_code=303)
    ctx = _ctx_panoramica()
    ctx["edit"] = edit
    ctx["next_url"] = "/finanze"          # dopo il salvataggio si torna alla panoramica
    return templates.TemplateResponse(request, "finance_overview.html", ctx)


@router.post("/finanze/movimenti/{tid}/aggiorna")
def aggiorna_movimento(
    tid: int,
    tipo: str = Form(...),
    data: str = Form(""),
    importo: str = Form("0"),
    wallet_id: str = Form(""),
    wallet_to_id: str = Form(""),
    categoria: str = Form(""),
    descrizione: str = Form(""),
    next: str = Form("/finanze"),
):
    """Salva le modifiche a un movimento normale (in-place, stesso record)."""
    if tipo in (TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO):
        wid = int(wallet_id) if (wallet_id or "").strip().isdigit() else None
        wto = int(wallet_to_id) if (wallet_to_id or "").strip().isdigit() else None
        if wid:
            service.aggiorna_movimento(
                tid, tipo=tipo, data=to_datetime(data),
                importo=to_float(importo, 0.0) or 0.0, wallet_id=wid,
                wallet_to_id=wto, categoria_nome=categoria, descrizione=descrizione)
    _aggiorna_grafico_patrimonio()
    dest = next if next.startswith("/finanze") else "/finanze"
    return RedirectResponse(dest, status_code=303)


# ------------------------------ partite di giro ------------------------------
@router.post("/finanze/giro/{gid}/rientro")
def giro_rientro(
    gid: str,
    importo_ricevuto: str = Form("0"),
    data_ricevuto: str = Form(""),
    wallet_ricevuto_id: str = Form(""),
    controparte: str = Form(""),
    next: str = Form("/finanze"),
):
    """Registra un rimborso su una partita e la lascia APERTA (per i rimborsi
    che arrivano in più volte)."""
    w = int(wallet_ricevuto_id) if (wallet_ricevuto_id or "").strip().isdigit() else None
    service.aggiungi_rientro(gid, importo=to_float(importo_ricevuto, 0.0) or 0.0,
                             wallet_id=w, controparte=controparte,
                             data=to_datetime(data_ricevuto))
    _aggiorna_grafico_patrimonio()
    dest = next if next.startswith("/finanze") else "/finanze"
    return RedirectResponse(dest, status_code=303)


@router.post("/finanze/giro/{gid}/chiudi")
def giro_chiudi(
    gid: str,
    importo_ricevuto: str = Form(""),
    data_ricevuto: str = Form(""),
    wallet_ricevuto_id: str = Form(""),
    controparte: str = Form(""),
    next: str = Form("/finanze"),
):
    """Chiude la partita: da qui il netto entra nelle statistiche. Se sono passati
    importo+portafoglio, registra prima un ultimo rimborso ('aggiungi e chiudi')."""
    imp = to_float(importo_ricevuto, 0.0) or 0.0
    w = int(wallet_ricevuto_id) if (wallet_ricevuto_id or "").strip().isdigit() else None
    aggiungi = imp > 0 and w is not None
    service.chiudi_giro(gid, importo=imp if aggiungi else None,
                        wallet_id=w if aggiungi else None,
                        controparte=controparte, data=to_datetime(data_ricevuto))
    _aggiorna_grafico_patrimonio()
    dest = next if next.startswith("/finanze") else "/finanze"
    return RedirectResponse(dest, status_code=303)


@router.post("/finanze/giro/{gid}/converti")
def giro_converti(gid: str, next: str = Form("/finanze")):
    """'Non me li ridaranno': le spese della partita diventano normali uscite."""
    service.converti_giro_in_uscita(gid)
    _aggiorna_grafico_patrimonio()
    dest = next if next.startswith("/finanze") else "/finanze"
    return RedirectResponse(dest, status_code=303)


@router.post("/finanze/giro/{gid}/aggiorna")
def giro_aggiorna(
    gid: str,
    giro_dopo: str = Form(""),
    spesa_importo: list[str] = Form([]),
    spesa_wallet: list[str] = Form([]),
    spesa_categoria: list[str] = Form([]),
    spesa_descrizione: list[str] = Form([]),
    spesa_data: list[str] = Form([]),
    rientro_importo: list[str] = Form([]),
    rientro_wallet: list[str] = Form([]),
    rientro_chi: list[str] = Form([]),
    rientro_data: list[str] = Form([]),
    next: str = Form("/finanze"),
):
    """Salva le modifiche a un'INTERA partita di giro (tutte le gambe insieme)."""
    service.aggiorna_giro(
        gid,
        spese=_zip_spese(spesa_importo, spesa_wallet, spesa_categoria,
                         spesa_descrizione, spesa_data),
        rientri=_zip_rientri(rientro_importo, rientro_wallet, rientro_chi, rientro_data),
        aperta=bool(giro_dopo))
    _aggiorna_grafico_patrimonio()
    dest = next if next.startswith("/finanze") else "/finanze"
    return RedirectResponse(dest, status_code=303)


# ------------------------------ agente AI (Fase 4) ------------------------------
@router.post("/finanze/ai/parse", response_class=HTMLResponse)
def ai_parse(request: Request, testo: str = Form(""), next: str = Form("/finanze")):
    """Interpreta una frase ('ieri 20€ di benzina con la carta') e mostra il modulo
    movimenti PRECOMPILATO. Non salva nulla: la conferma resta all'utente."""
    ctx = _ctx_panoramica()
    ctx["proposta"] = ai.parse_movimento(testo, ctx["wallets"], ctx["categorie"])
    # il modulo precompilato deve tornare a una pagina GET reale dopo il salvataggio
    # (cur_path qui sarebbe /finanze/ai/parse, che non ha una GET)
    ctx["next_url"] = "/finanze"
    return templates.TemplateResponse(request, "finance_overview.html", ctx)


def _mesi_indietro(now, k):
    y, m = now.year, now.month - k
    while m <= 0:
        m += 12
        y -= 1
    return y, m


def _contesto_finanze() -> str:
    """Riassunto AGGREGATO e anonimo degli ultimi 3 mesi per l'analisi AI.
    Niente nomi/carte/IBAN: solo totali e categorie.

    I mesi PRECEDENTI all'inizio del tracking non si passano: un mese in cui
    l'app non esisteva non è "un mese senza spese", e presentarlo come tale
    invita l'agente a confronti falsi ("le uscite sono cresciute rispetto a
    maggio", quando a maggio semplicemente non registravamo niente)."""
    now = datetime.now()
    inizio = service.data_inizio()
    righe = []
    for k in (2, 1, 0):
        y, m = _mesi_indietro(now, k)
        # il mese conta solo se è cominciato dopo l'inizio del tracking
        if datetime(y, m, 1) < datetime(inizio.year, inizio.month, 1):
            continue
        r = service.riepilogo_mese(y, m)
        cat = "; ".join(f"{c['nome']}: {c['tot']:.0f}€" for c in r["spese_categoria"][:6]) or "nessuna"
        righe.append(
            f"Mese {y}-{m:02d}: entrate {r['entrate']:.0f}€, uscite {r['uscite']:.0f}€, "
            f"saldo {r['saldo']:.0f}€. Spese principali per categoria: {cat}.")
    sal = service.saldi()
    righe.append(f"Patrimonio totale attuale: {sal['totale']:.0f}€ distribuito su "
                 f"{len(sal['righe'])} portafogli.")
    return "\n".join(righe)


def _genera_lettura_finanze() -> None:
    res = ai.analizza_finanze(_contesto_finanze())
    if res.get("ok"):
        settings_store.set_setting("fin_ai", json.dumps({
            "text": res["text"], "conf": res.get("conf", "media"),
            "when": datetime.now().isoformat(timespec="minutes")}))


@router.post("/finanze/ai/analisi")
def ai_analisi():
    """Analisi descrittiva del mese (dati aggregati e anonimi): la genera,
    la SALVA (resta visibile come 'Lettura AI') e torna in panoramica."""
    _genera_lettura_finanze()
    return RedirectResponse("/finanze", status_code=303)
