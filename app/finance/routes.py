"""Pagine delle finanze: la panoramica è l'unica pagina (card portafogli,
movimento nuovo, sintesi del mese, TUTTI i movimenti)."""
import json
from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from shared.templating import templates
from shared.parsing import to_float, to_datetime
from shared import ai, settings_store, tempo
from finance import service
from finance.models import TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO, TIPO_GIRO

router = APIRouter()


def _oggi_local():
    """L'ora con cui si precompila il modulo: quella del fuso SCELTO.

    Prima era l'orologio della macchina. Andava bene finché la macchina era una
    sola; con il telefono all'estero e il server in UTC dava tre orari diversi
    per lo stesso movimento (vedi shared/tempo.py).
    """
    return tempo.adesso().strftime("%Y-%m-%dT%H:%M")


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


def _per_giorno(movimenti: list) -> list:
    """I movimenti raggruppati per giorno, per la lista del telefono.

    Il raggruppamento sta QUI e non nel template perché con un giorno serve
    anche il suo totale, e sommare dentro Jinja vuol dire inventarsi variabili
    che sopravvivono al ciclo. I movimenti arrivano già in ordine di data
    decrescente, quindi basta guardare se il giorno è cambiato.

    Nel totale del giorno **i trasferimenti non entrano**: spostare soldi da un
    conto all'altro non è un giorno in cui hai speso, e contarli farebbe
    apparire −500 il giorno in cui hai messo da parte 500.
    """
    giorni = []
    for m in movimenti:
        g = m["t"].data.date()
        if not giorni or giorni[-1]["giorno"] != g:
            giorni.append({"giorno": g, "etichetta": tempo.etichetta_giorno(g),
                           "righe": [], "totale": 0.0})
        blocco = giorni[-1]
        blocco["righe"].append(m)
        tt = m["t"].tipo
        if tt == TIPO_ENTRATA:
            blocco["totale"] += m["t"].importo or 0.0
        elif tt == TIPO_USCITA:
            blocco["totale"] -= m["t"].importo or 0.0
        elif tt == TIPO_GIRO:
            blocco["totale"] += m["t"].giro_importo_display or 0.0
    return giorni


def _dati_effetto(saldi, riep, now) -> dict:
    """Materiale del riquadro «che cosa cambia»: saldi dei conti e uscite già
    registrate per categoria, letti dal JS (static/movement-preview.js).

    Sta in una funzione sua perché lo usano DUE contesti — la panoramica e il
    pannello del «＋» — e due copie che divergono vorrebbero dire un saldo
    diverso a seconda di dove hai aperto il modulo.
    """
    return {
        "wallets": {str(r["w"].id): {
            "nome": r["w"].nome, "saldo": r["saldo"],
            "derivato": bool(r.get("derivato")),
            "bloccato": bool(r.get("bloccato")),
            # regole della carta: servono al JS per proporre arrotondamento e
            # saveback mentre scrivi. Il calcolo è lo stesso del server, e il
            # server lo rifà comunque al salvataggio: qui è solo anteprima.
            "carta": ({"arr": bool(r["w"].arrotonda),
                       "pct": r["w"].saveback_pct or 0.0,
                       "tetto": r["w"].saveback_tetto or 0.0}
                      if (r["w"].arrotonda or (r["w"].saveback_pct or 0.0)) else None),
        } for r in saldi["righe"]},
        "mese": {"entrate": riep["entrate"], "uscite": riep["uscite"]},
        "cat": service.uscite_per_categoria_mese(now.year, now.month),
        "salvadanaio": service.NOME_WALLET_NASCOSTI,
        "sav_gia": service.saveback_maturato(now.year, now.month),
    }


def _ctx_panoramica() -> dict:
    now = tempo.adesso()
    saldi = service.saldi()
    riep = service.riepilogo_mese(now.year, now.month)
    movimenti = service.lista_movimenti()        # TUTTI, data desc
    return {
        # Gli STESSI movimenti, raggruppati per giorno: è la forma che serve
        # alla lista del telefono. Non è una seconda lettura del database —
        # è la stessa lista guardata in un altro modo.
        "per_giorno": _per_giorno(movimenti),
        "active": "finanze",
        "saldi": saldi,
        "riep": riep,
        "calendario": service.calendario_spese(now.year, now.month),
        "destinazioni": service.destinazioni_mese(now.year, now.month),
        "effetto": _dati_effetto(saldi, riep, now),
        "movimenti": movimenti,
        "wallets": service.wallets(),
        "categorie": service.categorie(),
        "tipi": (TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO, TIPO_GIRO),
        "giri_aperti": service.giri_aperti(),        # riquadro "In attesa di rimborso"
        "controparti": service.controparti(),        # suggerimenti "da chi"
        "oggi": _oggi_local(),
        "ai_on": ai.is_configured(),
        "lettura_ai": _lettura_ai_salvata(),
    }


def _torna_a(request) -> str:
    """Da quale pagina è stato premuto il «＋», per tornarci dopo il salvataggio.

    Lo dice l'intestazione `Referer`, che è **roba del browser**: può contenere
    qualunque cosa, compreso l'indirizzo di un altro sito. Se lo passassimo così
    com'è al campo `next` del modulo, il salvataggio finirebbe con un rimbalzo
    là fuori — un redirect aperto, cioè un pezzo di casa nostra che porta gente
    dove vuole chi ha scritto il link.

    Quindi: si accettano solo percorsi di questo sito, e si torna sempre una
    stringa che comincia per «/». Nel dubbio, `/finanze`.
    """
    from urllib.parse import urlparse

    grezzo = (request.headers.get("referer") or "").strip()
    if not grezzo:
        return ""

    p = urlparse(grezzo)
    # Un indirizzo con un host diverso dal nostro non è una nostra pagina. E
    # nemmeno uno con uno schema strano (`javascript:`, `data:`): quelli non
    # hanno un percorso di cui fidarsi.
    if p.scheme and p.scheme not in ("http", "https"):
        return ""
    try:
        nostro = request.url.netloc
    except Exception:
        nostro = ""
    if p.netloc and p.netloc != nostro:
        return ""

    percorso = p.path or ""
    # «//evil.com» è un percorso per urlparse solo quando manca lo schema, e per
    # il browser è un indirizzo assoluto: non deve passare.
    if not percorso.startswith("/") or percorso.startswith("//"):
        return ""
    # Tornare al modulo stesso vorrebbe dire riaprirlo vuoto dopo aver salvato.
    if percorso.startswith("/finanze/nuovo"):
        return ""
    return percorso


def _ctx_modulo(next_url: str = "") -> dict:
    """Solo quello che serve al MODULO: campi, suggerimenti e «che cosa cambia».

    Non è `_ctx_panoramica()` alleggerito per gusto: quella carica tutti i
    movimenti, il calendario del mese e le destinazioni, cioè il grosso della
    pagina. Il «＋» del telefono si preme dieci volte al giorno da qualunque
    schermata, e farlo pagare come l'apertura di Finanze si sentirebbe.
    """
    now = tempo.adesso()
    saldi = service.saldi()
    riep = service.riepilogo_mese(now.year, now.month)
    return {
        "active": "finanze",
        "wallets": service.wallets(),
        "categorie": service.categorie(),
        "controparti": service.controparti(),
        "tipi": (TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO, TIPO_GIRO),
        "oggi": _oggi_local(),
        "ai_on": ai.is_configured(),
        # stesso materiale del riquadro «che cosa cambia» della panoramica: se
        # qui fosse diverso, il conto mostrato dipenderebbe da dove hai aperto
        # il modulo, ed è esattamente il genere di bugia che nessuno scopre.
        "effetto": _dati_effetto(saldi, riep, now),
        # Dove si torna dopo il salvataggio: la pagina da cui è stato premuto il
        # «＋», non «/finanze». Aprire il modulo dalla Home e ritrovarsi altrove
        # è il modo più veloce per far sembrare l'app un sito di pagine slegate.
        "next_url": next_url or "/finanze",
    }


@router.get("/finanze/nuovo", response_class=HTMLResponse)
def nuovo_movimento(request: Request, panel: int = 0):
    """Il modulo da solo, per il «＋» della barra del telefono.

    Prima il «＋» era un'ancora a `/finanze#aggiungi`: portava in mezzo a una
    pagina lunga diciotto schermate, e per registrare un caffè bisognava
    caricare tutto il registro. Adesso sale un pannello dal fondo, da qualunque
    schermata, e alla fine si torna dov'eri.
    """
    ctx = _ctx_modulo(_torna_a(request))
    if panel:
        return templates.TemplateResponse(request, "finance_movement_panel.html", ctx)
    # Senza JS (o aprendo l'indirizzo a mano) resta una pagina vera, non un
    # frammento nudo: stessa forma, dentro il guscio dell'app.
    ctx["pagina_intera"] = True
    return templates.TemplateResponse(request, "finance_new.html", ctx)


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


def _zip_spese(importi, wallets, categorie, descrizioni, date, arr=None, sav=None):
    """Le liste del form, riga per riga. `arr`/`sav` sono gli importi generati
    dalla carta: vuoti = li calcola l'app, scritti = valgono quelli. Il modulo li
    manda per OGNI riga (anche vuoti) così gli indici restano allineati."""
    arr, sav = arr or [], sav or []
    out = []
    for i, imp in enumerate(importi):
        amount = to_float(imp, 0.0) or 0.0
        w = wallets[i] if i < len(wallets) else ""
        if amount > 0 and (w or "").strip().isdigit():
            out.append({
                "importo": amount, "wallet_id": int(w),
                "categoria": categorie[i] if i < len(categorie) else "",
                "descrizione": descrizioni[i] if i < len(descrizioni) else "",
                "data": to_datetime(date[i]) if i < len(date) and date[i] else None,
                "arr": to_float(arr[i], None) if i < len(arr) else None,
                "sav": to_float(sav[i], None) if i < len(sav) else None})
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
    spesa_arr: list[str] = Form([]),
    spesa_sav: list[str] = Form([]),
    rientro_importo: list[str] = Form([]),
    rientro_wallet: list[str] = Form([]),
    rientro_chi: list[str] = Form([]),
    rientro_data: list[str] = Form([]),
    # carta con arrotondamento: vuoti = li calcola l'app, scritti = valgono i tuoi
    extra_arr: str = Form(""),
    extra_sav: str = Form(""),
    next: str = Form("/finanze"),
):
    if tipo in (TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO):
        wid = int(wallet_id) if (wallet_id or "").strip().isdigit() else None
        wto = int(wallet_to_id) if (wallet_to_id or "").strip().isdigit() else None
        imp = to_float(importo, 0.0) or 0.0
        if wid and tipo == TIPO_USCITA and service.regole_carta(wid):
            # solo le USCITE: un trasferimento (il PAC parte proprio da questa
            # carta) non è un pagamento e non deve arrotondare niente
            service.crea_uscita_carta(
                data=to_datetime(data), importo=imp, wallet_id=wid,
                categoria_nome=categoria, descrizione=descrizione,
                arr=to_float(extra_arr, None), sav=to_float(extra_sav, None))
        elif wid:
            service.crea_movimento(
                tipo=tipo, data=to_datetime(data), importo=imp,
                wallet_id=wid, wallet_to_id=wto, categoria_nome=categoria,
                descrizione=descrizione)
    elif tipo == TIPO_GIRO:
        service.crea_giro(
            spese=_zip_spese(spesa_importo, spesa_wallet, spesa_categoria,
                             spesa_descrizione, spesa_data, spesa_arr, spesa_sav),
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


@router.get("/finanze/movimenti/{tid}/dettaglio", response_class=HTMLResponse)
def dettaglio_movimento(request: Request, tid: int):
    """Cosa c'è dentro un movimento: il pannello che scivola da destra, lo stesso
    dei titoli. Serve alle spese con la carta che arrotonda, dove la riga del
    registro (7,60 €) e l'addebito sul conto (8,00 €) sono due numeri diversi e
    tutti e due veri. Senza JS si atterra qui e si vede lo stesso frammento."""
    m = service.movimento(tid)
    if not m:
        return RedirectResponse("/finanze", status_code=303)
    return templates.TemplateResponse(
        request, "finance_movement_detail_panel.html",
        {"m": m, "salvadanaio": service.NOME_WALLET_NASCOSTI})


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
    extra_arr: str = Form(""),
    extra_sav: str = Form(""),
    next: str = Form("/finanze"),
):
    """Salva le modifiche a un movimento normale (in-place, stesso record)."""
    if tipo in (TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO):
        wid = int(wallet_id) if (wallet_id or "").strip().isdigit() else None
        wto = int(wallet_to_id) if (wallet_to_id or "").strip().isdigit() else None
        if wid:
            prima = service.importo_movimento(tid)      # serve dopo, per le figlie
            service.aggiorna_movimento(
                tid, tipo=tipo, data=to_datetime(data),
                importo=to_float(importo, 0.0) or 0.0, wallet_id=wid,
                wallet_to_id=wto, categoria_nome=categoria, descrizione=descrizione)
            arr, sav = to_float(extra_arr, None), to_float(extra_sav, None)
            if tipo == TIPO_USCITA and service.regole_carta(wid) \
                    and (arr is not None or sav is not None):
                # i due numeri erano nel modulo sotto i tuoi occhi: valgono quelli
                service.imposta_figlie(tid, arr or 0.0, sav or 0.0)
            else:
                service.risincronizza_figlie(tid, prima)
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
    spesa_arr: list[str] = Form([]),
    spesa_sav: list[str] = Form([]),
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
                         spesa_descrizione, spesa_data, spesa_arr, spesa_sav),
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
    now = tempo.adesso()
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
            "when": tempo.adesso().isoformat(timespec="minutes")}))


@router.post("/finanze/ai/analisi")
def ai_analisi():
    """Analisi descrittiva del mese (dati aggregati e anonimi): la genera,
    la SALVA (resta visibile come 'Lettura AI') e torna in panoramica."""
    _genera_lettura_finanze()
    return RedirectResponse("/finanze", status_code=303)
