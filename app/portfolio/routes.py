"""Pagine del portafoglio: elenco posizioni, aggiungi/modifica/elimina, PAC.

Tutto modificabile dall'interfaccia, MAI da codice (come da requisiti).
"""
import json
import urllib.parse
from datetime import date, datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse

from shared.db import SessionLocal
from shared.templating import templates
from shared.parsing import to_float, to_date
from shared.charts import chart_points
from shared import ai, settings_store, tempo
from portfolio.models import Position, TIPO_ETF, TIPO_AZIONE
from portfolio import service, market, analytics, versamenti
from finance import service as fin_service

router = APIRouter()


@router.get("/analisi", response_class=HTMLResponse)
def analisi(request: Request):
    """L'analisi si apre SUBITO: legge solo la cache locale dei fondamentali.

    Prima chiamava `look_through()` e `analisi_completa()` senza `cached_only`:
    se i dati avevano più di 24 ore partivano 37 richieste HTTP in linea, con
    la pagina ferma ad aspettarle. Ora l'aggiornamento gira dietro le quinte e
    si vede al giro dopo."""
    from shared import storico

    market.refresh_fondamentali_async()
    lt = analytics.look_through(cached_only=True)
    an = analytics.analisi_completa(cached_only=True)
    risk = analytics.get_cached_risk()
    # l'archivio giornaliero: il TUO risultato nel tempo, non il prezzo dei titoli
    serie = storico.serie(180)
    valori = [g["risultato_eur"] for g in serie if g["risultato_eur"] is not None]
    punti, sale = chart_points(valori, w=560, h=110) if len(valori) >= 2 else ("", True)
    return templates.TemplateResponse(request, "analisi.html", {
        "active": "analisi",
        "lt": lt, "an": an, "risk": risk,
        "storico": {"serie": serie, "punti": punti, "sale": sale,
                    "n": len(valori), "min": min(valori) if valori else None,
                    "max": max(valori) if valori else None,
                    "dal": serie[0]["data"] if serie else None},
        "risk_scaduto": analytics.risk_scaduto(),
        # le schede vanno IN PAGINA, non solo all'agente: sono calcolate in
        # Python e restano vere anche a chiave AI spenta
        "schede": schede_metriche(an, lt, risk),
        "ai_on": ai.is_configured(),
    })


def schede_metriche(an: dict, lt: dict, risk: dict | None) -> dict:
    """La CARTA D'IDENTITÀ di ogni metrica: cosa misura, su quali dati/periodo/
    copertura è calcolato QUESTO valore, e il suo limite.

    Serve a due lettori diversi. All'agente, per togliergli il bisogno di
    indovinare — la causa più frequente delle sue risposte sbagliate su questa
    pagina. E all'utente: sono frasi calcolate, non generate, quindi la pagina
    può mostrarle anche senza AI configurata."""
    def _cop(v):
        return f"{v}% del valore" if v is not None else "una parte del valore"
    if risk:
        riskbase = (f"{risk['n']} titoli, {risk['weeks']} settimane di prezzi "
                    f"settimanali riportati in euro, pesati sul "
                    f"{'valore reale' if risk.get('base') == 'valore' else 'peso target'} "
                    f"(calcolato il {risk['when']})")
    else:
        riskbase = "dati settimanali"
    risklimite = ("misura la storia di MERCATO dei titoli, non i pochi giorni in "
                  "cui l'utente li possiede")
    return {
        "valore": {
            "cosa": "la somma del valore attuale (quantità × prezzo di oggi) di tutte le posizioni con prezzo noto",
            "dati": "prezzi live da Yahoo Finance, convertiti in euro",
            "limite": "esclude i titoli di cui manca il prezzo, quindi può essere leggermente sottostimato"},
        "risultato": {
            "cosa": "quanto vale oggi il portafoglio rispetto a quanto l'utente ci ha versato",
            "dati": f"valore di oggi {an.get('valore_totale')}€ contro {an.get('versato_totale')}€ versati",
            "limite": "è il risultato REALE dell'utente sui SUOI soldi; non c'entra con la performance storica di mercato dei titoli"},
        "mkt12m": {
            "cosa": "la variazione media (pesata) del prezzo dei titoli negli ultimi ~12 mesi",
            "dati": f"chiusure settimanali da Yahoo, su {_cop(an.get('perf12m_cop'))}",
            "limite": "è la storia del titolo sul MERCATO, avvenuta PRIMA che l'utente comprasse (lui possiede da pochi giorni): NON è il suo guadagno. Ed è dominata da pochi titoli con storia estrema"},
        "divyield": {
            "cosa": "il rendimento da dividendo medio dei soli titoli che lo dichiarano",
            "dati": f"media pesata su {_cop(an.get('div_coverage'))} (gli ETF non pubblicano il dato)",
            "limite": "è una stima prospettica basata sull'ultimo dato noto, non su incassi realizzati"},
        "divincome": {
            "cosa": "il reddito annuo STIMATO in euro dai dividendi (rendimento × valore), sui titoli che li pagano",
            "dati": f"stessa base del rendimento: {_cop(an.get('div_coverage'))}",
            "limite": "è una proiezione annua, non denaro già incassato"},
        "ter": {
            "cosa": "il costo di gestione annuo medio degli ETF, pesato",
            "dati": f"{an.get('ter_n_con')} ETF su {an.get('ter_n_etf')} pubblicano il TER",
            "limite": "riguarda solo gli ETF e non include le commissioni di transazione"},
        "eff": {
            "cosa": "quanti titoli a peso uguale darebbero la stessa concentrazione che ha oggi il portafoglio",
            "dati": f"indice di concentrazione (HHI) su {an.get('n_titoli')} posizioni",
            "limite": "guarda solo la distribuzione dei pesi, non la diversificazione per settore o paese"},
        "tech": {
            "cosa": "quanto pesa il settore tecnologico nel portafoglio scomposto (ETF nei loro settori + azioni)",
            "dati": f"sul {lt.get('coperto_pct')}% del portafoglio di cui si conosce il settore",
            "limite": "i settori dentro gli ETF vengono dai «top sectors» di Yahoo, possono non sommare esattamente a 100"
                      + (f"; non pubblicano il settore: {', '.join(lt['senza_settore'])}"
                         if lt.get("senza_settore") else "")},
        "top5": {
            "cosa": "quanto pesano insieme le 5 posizioni più grandi",
            "dati": "pesato sul valore reale delle posizioni",
            "limite": "descrittivo: alta concentrazione non è di per sé un bene o un male"},
        "top1": {
            "cosa": f"quanto pesa la posizione più grande ({an.get('top1_tk')})",
            "dati": "pesato sul valore reale",
            "limite": "descrittivo"},
        "nsett": {
            "cosa": "quanti settori distinti sono rappresentati nel portafoglio",
            "dati": f"sul {lt.get('coperto_pct')}% coperto",
            "limite": "conta i settori presenti, non quanto sono bilanciati"},
        "vol": {"cosa": "quanto oscilla il valore del portafoglio su base annua", "dati": riskbase,
                "limite": risklimite},
        "mdd": {"cosa": "la peggior caduta dal picco nel periodo osservato", "dati": riskbase,
                "limite": risklimite},
        "sharpe": {"cosa": "il rendimento ottenuto per ogni unità di rischio (oscillazione)", "dati": riskbase,
                   "limite": risklimite + "; premia i periodi fortunati, non prevede il futuro"},
        "beta": {"cosa": "quanto il portafoglio amplifica o attenua i movimenti del mercato globale (MSCI World)",
                 "dati": riskbase, "limite": risklimite + "; il confronto è fra due serie entrambe in euro"},
        "var": {"cosa": "la perdita mensile massima attesa nel 95% dei casi (stima parametrica)", "dati": riskbase,
                "limite": risklimite + "; nel 5% dei casi la perdita può essere maggiore"},
        "r2": {"cosa": "quanto dei movimenti del portafoglio è spiegato dal mercato globale", "dati": riskbase,
               "limite": risklimite},
    }


def _scheda_metrica(metric: str, an: dict, lt: dict, risk: dict | None) -> dict | None:
    return schede_metriche(an, lt, risk).get(metric)


# Per quanto vale una spiegazione già scritta. La metrica cambia di poco ogni
# giorno ma il SIGNIFICATO no: rigenerarla a ogni click era una chiamata a
# pagamento per riottenere lo stesso testo.
GIORNI_CACHE_AI = 14


def _cache_ai_metrica(metric: str, valore: str):
    raw = settings_store.get_setting(f"ai_metrica_{metric}", "") if metric else ""
    if not raw:
        return None
    try:
        d = json.loads(raw)
        quando = datetime.fromisoformat(d.get("when", ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if d.get("valore") != valore:
        return None                      # il numero è cambiato: la spiegazione va rifatta
    if (tempo.adesso() - quando).days > GIORNI_CACHE_AI:
        return None
    return {"ok": True, "text": d.get("text", ""), "conf": d.get("conf", "media"),
            "dalla_cache": True}


@router.post("/analisi/ai")
async def analisi_ai(label: str = Form(""), valore: str = Form(""), metric: str = Form("")):
    """Spiega una metrica dell'analisi (popup ✨): risposta JSON per il modal.

    All'agente non diamo più solo etichetta+valore (lo costringeva a indovinare):
    gli passiamo la SCHEDA della metrica — cosa misura, su quali dati e con quale
    copertura è calcolato QUESTO numero — così spiega invece di inventare."""
    from fastapi.responses import JSONResponse
    label, valore = (label or "").strip()[:120], (valore or "").strip()[:60]
    metric = (metric or "").strip()[:40]
    if not label:
        return JSONResponse({"ok": False, "error": "vuoto"})
    gia = _cache_ai_metrica(metric, valore)
    if gia:
        return JSONResponse(gia)
    scheda, contesto = None, ""
    try:
        # niente HTTP nel popup: né qui né dentro l'analisi (prima
        # `analisi_completa()` poteva scaricare i fondamentali, smentendo la
        # promessa scritta due righe sopra)
        lt = analytics.look_through(cached_only=True)
        an = analytics.analisi_completa(cached_only=True)
        scheda = _scheda_metrica(metric, an, lt, analytics.get_cached_risk())
        settori = ", ".join(f"{s['key']} {s['pct']}%" for s in lt["settori"][:6])
        contesto = (f"Portafoglio personale diversificato: {lt['n_titoli']} titoli; "
                    f"settori principali: {settori or 'n/d'}.")
    except Exception:
        # Contesto e scheda sono un CONTORNO: servono a far parlare l'agente
        # del portafoglio vero invece che in astratto. Se il calcolo non
        # riesce restano vuoti e la spiegazione della metrica esce lo stesso,
        # solo più generica. Far fallire il popup sarebbe peggio del popup
        # generico — e questa è una finestrella che si apre con un dito, non
        # un posto dove mostrare una schermata di errore.
        pass
    res = ai.spiega_metrica(label, valore, contesto, scheda=scheda)
    if res.get("ok") and metric:
        settings_store.set_setting(f"ai_metrica_{metric}", json.dumps({
            "valore": valore, "text": res["text"], "conf": res.get("conf", "media"),
            "when": tempo.adesso().isoformat(timespec="minutes")}))
    return JSONResponse(res)


@router.post("/analisi/rischio")
def calcola_rischio():
    analytics.compute_risk()
    return RedirectResponse("/analisi", status_code=303)


@router.get("/portafoglio", response_class=HTMLResponse)
def elenco(request: Request):
    vista = service.vista_portafoglio()
    snapshot = market.get_perf_snapshot()
    # perf ~12 mesi del portafoglio: media pesata sui titoli con storia nota.
    # SOLO sul valore: mescolare euro e % target nello stesso denominatore
    # (com'era) somma due unità di misura diverse.
    num = den = 0.0
    for r in vista["righe"]:
        pf = snapshot.get((r["p"].ticker or "").upper())
        if pf is None or not r["valore"]:
            continue
        num += r["valore"] * pf
        den += r["valore"]
    # P/L REALE per posizione: valore attuale vs quanto hai versato (versato_totale).
    # Sostituisce nella colonna il vecchio rendimento a 12 mesi (che resta nel dettaglio).
    tot_versato = 0.0
    pl_map = {}
    for r in vista["righe"]:
        p = r["p"]
        v = p.versato_totale or 0.0
        tot_versato += v
        if v > 0 and r["valore"] is not None:
            pl_map[p.id] = {"eur": round(r["valore"] - v, 2),
                            "pct": round((r["valore"] / v - 1) * 100, 2)}
    pl_tot = None
    if tot_versato > 0 and vista["ha_totale"]:
        pl_tot = {"eur": round(vista["totale"] - tot_versato, 2),
                  "pct": round((vista["totale"] / tot_versato - 1) * 100, 2)}

    qp = request.query_params
    return templates.TemplateResponse(request, "portfolio_positions.html", {
        "active": "portafoglio",
        "vista": vista,
        "riepilogo": service.riepilogo(vista),
        "perf": snapshot,                            # P/L ~12m per ticker (ora nel dettaglio)
        "pf_perf": round(num / den, 2) if den else None,
        "pl_map": pl_map,                            # {id: {eur, pct}} P/L reale per titolo
        "pmc_map": service.pmc_map(),                # prezzo medio di carico per titolo
        "tot_versato": round(tot_versato, 2),
        "pl_tot": pl_tot,                            # P/L reale complessivo
        "versamenti": versamenti.lista(),            # storico PAC (in fondo alla pagina)
        "promemoria": versamenti.promemoria(),
        "flash_added": qp.get("added", ""),
        "flash_deleted": qp.get("deleted", ""),
        "flash_saved": qp.get("saved", "") == "1",
        "flash_pac": qp.get("pac", ""),
        "flash_tr": qp.get("tr", "") == "1",
        "open_form": qp.get("add", "") == "1",       # apre il form inline
    })


def _default_conto(conti: list) -> str:
    """Conto di provenienza suggerito: Trade Republic se c'è, altrimenti il primo."""
    for c in conti:
        k = (c or "").strip().lower()
        if k in ("trade republic", "tr") or k.startswith("trade republic"):
            return c
    return conti[0] if conti else ""


@router.get("/portafoglio/versamento", response_class=HTMLResponse)
def versamento_form(request: Request, vid: int = 0):
    """Schermata 'Registra PAC': data, importo, conto, i 37 titoli con
    interruttore (ON di default). Con ?vid=N pre-riempie per la modifica."""
    posizioni = service.lista_posizioni()
    conti = [w.nome for w in fin_service.wallets()]
    pre = versamenti.dettaglio(vid) if vid else None
    return templates.TemplateResponse(request, "portfolio_versamento.html", {
        "active": "portafoglio", "posizioni": posizioni, "conti": conti,
        "importo": (pre["importo"] if pre else 100.0),
        "data": (pre["data"] if pre else tempo.oggi()).isoformat(),
        "conto": (pre["conto"] if pre else _default_conto(conti)),
        "inclusi_ids": (pre["inclusi_ids"] if pre else {p.id for p in posizioni}),
        "orari": (pre["orari"] if pre else {}),
        "orari_scorsi": versamenti.ultimi_orari(escludi_vid=vid or None),
        # Il fuso di un PAC già registrato è quello con cui è stato scritto e
        # non si tocca; per uno nuovo si propone l'ultimo usato.
        "fuso": (pre["fuso"] if pre else versamenti.ultimo_fuso()),
        "fusi": tempo.FUSI, "fuso_app": tempo.etichetta(),
        "vid": str(vid) if vid else "", "anteprima": None,
    })


def _orari_dal_modulo(form) -> dict:
    """Gli orari per titolo mandati dal modulo: campi `ora_<id_posizione>`.

    Sono tanti quanti i titoli e i loro nomi dipendono dal database, quindi non
    si possono dichiarare come parametri: si leggono dal form intero."""
    fuori = {}
    for chiave, valore in form.multi_items():
        if not chiave.startswith("ora_"):
            continue
        pid = chiave[4:]
        if pid.isdigit() and str(valore).strip():
            fuori[int(pid)] = str(valore).strip()
    return fuori


@router.post("/portafoglio/versamento", response_class=HTMLResponse)
async def versamento_post(
    request: Request,
    azione: str = Form("anteprima"),
    importo: str = Form("0"),
    data: str = Form(""),
    conto: str = Form(""),
    fuso: str = Form(""),
    vid: str = Form(""),
    incl: list[str] = Form(default=[]),
):
    """Un solo endpoint: 'anteprima' ricalcola e mostra la tabella; 'conferma'
    scrive (nuovo o modifica) e torna al portafoglio."""
    imp = to_float(importo, 0.0) or 0.0
    d = to_date(data) or tempo.oggi()
    incl_ids = {int(x) for x in incl if x.isdigit()}
    posizioni = service.lista_posizioni()
    esclusi = {p.id for p in posizioni if p.id not in incl_ids}
    vid_i = int(vid) if vid.strip().isdigit() else None
    orari = _orari_dal_modulo(await request.form())

    if azione == "conferma":
        versamenti.salva(imp, d, conto, esclusi, vid=vid_i, orari=orari, fuso=fuso)
        return RedirectResponse("/portafoglio?pac=1", status_code=303)

    conti = [w.nome for w in fin_service.wallets()]
    return templates.TemplateResponse(request, "portfolio_versamento.html", {
        "active": "portafoglio", "posizioni": posizioni, "conti": conti,
        "importo": imp, "data": d.isoformat(),
        "conto": conto or _default_conto(conti),
        "inclusi_ids": incl_ids, "vid": vid,
        # Riscritti come li ha capiti il server ("0935" torna indietro "09:35"),
        # così il modulo mostra l'ora che verrà davvero usata. Quello che NON si
        # capisce torna indietro tale e quale: cancellarlo nasconderebbe lo
        # sbaglio, e quel titolo prenderebbe il prezzo del giorno invece di
        # quello del suo momento, senza che nessuno se ne accorga.
        "orari": {pid: (versamenti.normalizza_ora(v) or v) for pid, v in orari.items()},
        "orari_scorsi": versamenti.ultimi_orari(escludi_vid=vid_i),
        "fuso": fuso, "fusi": tempo.FUSI, "fuso_app": tempo.etichetta(),
        "anteprima": versamenti.anteprima(imp, d, esclusi, orari, fuso),
    })


@router.post("/portafoglio/versamento/{vid}/elimina")
def versamento_elimina(vid: int):
    versamenti.elimina(vid)
    return RedirectResponse("/portafoglio?pac=del", status_code=303)


@router.post("/portafoglio/aggiorna")
def aggiorna_prezzi(next: str = Form("/portafoglio")):
    market.refresh_all()
    dest = next if next.startswith("/") else "/portafoglio"
    return RedirectResponse(dest, status_code=303)


@router.post("/portafoglio/allinea")
def allinea_tr(totale: str = Form(""), data: str = Form(""), togli: str = Form("")):
    """«Allinea a TR»: un numero solo — il totale che leggi sul broker — e da
    quel momento il totale dell'app è quello, con le stime per titolo riscalate.
    Il campo vuoto (o «togli») cancella l'allineamento e si torna alle stime."""
    if togli:
        service.salva_allineamento_tr(0)
    else:
        service.salva_allineamento_tr(to_float(totale, 0.0) or 0.0,
                                      to_date(data) or tempo.oggi())
    return RedirectResponse("/portafoglio?tr=1", status_code=303)


@router.get("/portafoglio/nuovo", response_class=HTMLResponse)
def form_nuovo(request: Request):
    return templates.TemplateResponse(request, "portfolio_form.html", {
        "active": "portafoglio",
        "p": None, "tipi": [TIPO_ETF, TIPO_AZIONE],
    })


@router.get("/portafoglio/{pos_id}/modifica", response_class=HTMLResponse)
def form_modifica(request: Request, pos_id: int):
    with SessionLocal() as db:
        p = db.get(Position, pos_id)
        if p is None:
            return RedirectResponse("/portafoglio", status_code=303)
        return templates.TemplateResponse(request, "portfolio_form.html", {
            "active": "portafoglio",
            "p": p, "tipi": [TIPO_ETF, TIPO_AZIONE],
        })


@router.post("/portafoglio/salva")
def salva(
    pos_id: str = Form(""),
    nome: str = Form(...),
    nome_breve: str = Form(""),
    tipo: str = Form(TIPO_AZIONE),
    categoria: str = Form(""),
    ticker: str = Form(""),
    isin: str = Form(""),
    pct_target: str = Form("0"),
    importo_fisso: str = Form(""),
    quantita: str = Form(""),
    valore_posseduto: str = Form(""),
    data_ultimo_acquisto: str = Form(""),
    note: str = Form(""),
):
    dati = dict(
        nome=nome.strip(),
        nome_breve=nome_breve.strip(),
        tipo=tipo if tipo in (TIPO_ETF, TIPO_AZIONE) else TIPO_AZIONE,
        categoria=categoria.strip(),
        ticker=ticker.strip().upper(),
        isin=isin.strip().upper(),
        pct_target=to_float(pct_target, 0.0) or 0.0,
        importo_fisso=to_float(importo_fisso, None),
        quantita=to_float(quantita, None),
        valore_posseduto=to_float(valore_posseduto, None),
        data_ultimo_acquisto=to_date(data_ultimo_acquisto),
        note=note.strip(),
    )
    with SessionLocal() as db:
        if pos_id.strip().isdigit():            # modifica di una posizione esistente
            p = db.get(Position, int(pos_id))
            if p:
                for k, v in dati.items():
                    setattr(p, k, v)
            db.commit()
            return RedirectResponse("/portafoglio?saved=1", status_code=303)
        # nuova posizione, in fondo all'elenco
        ultimo = db.query(Position).order_by(Position.ordine.desc()).first()
        dati["ordine"] = (ultimo.ordine + 1) if ultimo else 0
        db.add(Position(**dati))
        db.commit()
    etichetta = urllib.parse.quote(dati["ticker"] or dati["nome"])
    return RedirectResponse(f"/portafoglio?added={etichetta}", status_code=303)


@router.post("/portafoglio/{pos_id}/elimina")
def elimina(pos_id: int):
    etichetta = ""
    with SessionLocal() as db:
        p = db.get(Position, pos_id)
        if p:
            etichetta = p.ticker or p.nome
            db.delete(p)
            db.commit()
    return RedirectResponse(
        f"/portafoglio?deleted={urllib.parse.quote(etichetta)}", status_code=303)


@router.get("/pac", response_class=HTMLResponse)
def pac(request: Request, importo: str = ""):
    importo = (importo or "").strip() or "500"
    importo_val = to_float(importo, 500.0) or 0.0
    return templates.TemplateResponse(request, "pac.html", {
        "active": "pac",
        "r": service.calcola_pac(importo_val),
        "importo_input": importo,
    })


@router.get("/portafoglio/{pos_id}/holdings", response_class=HTMLResponse)
def holdings_fragment(request: Request, pos_id: int):
    """Frammento HTML con le holdings dell'ETF (per la tendina cliccabile)."""
    with SessionLocal() as db:
        p = db.get(Position, pos_id)
    fund = None
    if p and (p.ticker or "").strip():
        fund = market.get_fundamentals(p.ticker, tipo=p.tipo)
    return templates.TemplateResponse(request, "portfolio_holdings_fragment.html", {"fund": fund})


def _ai_take_cached(ticker: str) -> dict | None:
    """L'analisi AI della posizione, se già generata (cache per ticker)."""
    raw = settings_store.get_setting(f"ai_take_{(ticker or '').upper()}", "")
    if not raw:
        return None
    try:
        saved = json.loads(raw)
        return {"text": saved.get("text", ""), "conf": saved.get("conf", "media")}
    except json.JSONDecodeError:
        return None


def _descrizione_pubblica(p, fund, perf) -> str:
    """Descrizione SOLO da dati pubblici (mai ISIN/quantità/valori posseduti)."""
    righe = [f"Strumento: {p.nome} ({p.ticker}), tipo {p.tipo}, categoria {p.categoria or 'n/d'}."]
    if fund:
        if p.tipo == "ETF":
            settori = ", ".join(f"{s['name']} {s['weight']}%" for s in (fund.get("sectors") or [])[:5])
            righe.append(f"Categoria fondo: {fund.get('category') or 'n/d'}; settori principali: {settori or 'n/d'}.")
            top = ", ".join(h.get("name") or h.get("symbol") or "" for h in (fund.get("holdings") or [])[:5])
            if top:
                righe.append(f"Prime posizioni (parziale): {top}.")
        else:
            righe.append(f"Settore: {fund.get('sector') or 'n/d'}; industria: {fund.get('industry') or 'n/d'}; "
                         f"paese: {fund.get('country') or 'n/d'}; beta: {fund.get('beta') or 'n/d'}.")
        if fund.get("div_yield"):
            righe.append(f"Rendimento da dividendo: {round(fund['div_yield'] * 100, 2)}%.")
    if perf is not None:
        righe.append(f"Performance ~12 mesi: {perf}%.")
    return "\n".join(righe)


# domicilio dello strumento dal prefisso ISIN (dato reale, nessuna stima)
_ISIN_PAESE = {
    "IE": "Irlanda", "LU": "Lussemburgo", "US": "Stati Uniti", "IT": "Italia",
    "DE": "Germania", "FR": "Francia", "GB": "Regno Unito", "NL": "Paesi Bassi",
    "CH": "Svizzera", "ES": "Spagna", "JE": "Jersey", "GG": "Guernsey",
}


@router.get("/portafoglio/{pos_id}", response_class=HTMLResponse)
def dettaglio(request: Request, pos_id: int):
    """Scheda di dettaglio di una posizione (ETF: fondo+holdings; azione: profilo),
    con grafico ~12 mesi e, se generata, l'analisi qualitativa dell'agente."""
    with SessionLocal() as db:
        p = db.get(Position, pos_id)
    if not p:
        return RedirectResponse("/portafoglio", status_code=303)
    q = market.quotes_map().get((p.ticker or "").upper())
    fund = market.get_fundamentals(p.ticker, tipo=p.tipo) if (p.ticker or "").strip() else None
    perf = None
    punti, sale = "", True
    if (p.ticker or "").strip():
        closes = market.history_closes(market._yahoo_symbol(p.ticker), "1y", "1wk")
        if len(closes) >= 2 and closes[0]:
            perf = round((closes[-1] / closes[0] - 1) * 100, 2)
            punti, sale = chart_points(closes)
    if fund is not None:
        fund["ai"] = _ai_take_cached(p.ticker)
    # ?panel=1 -> solo il frammento per il DRAWER (aperto da app.js sopra
    # l'elenco, come nel design); senza parametro -> pagina intera.
    tpl = "portfolio_detail_panel.html" if request.query_params.get("panel") \
        else "portfolio_detail.html"
    return templates.TemplateResponse(request, tpl, {
        "active": "portafoglio", "p": p, "q": q, "fund": fund, "perf": perf,
        "chart_points": punti, "chart_up": sale, "ai_on": ai.is_configured(),
        "domicilio": _ISIN_PAESE.get((p.isin or "")[:2].upper()),
        "pmc": service.pmc_map().get(p.id),
    })


@router.post("/portafoglio/{pos_id}/ai")
def genera_ai_take(pos_id: int):
    """Genera (o rigenera) l'analisi dell'agente per una posizione e la salva."""
    with SessionLocal() as db:
        p = db.get(Position, pos_id)
    if not p or not (p.ticker or "").strip():
        return RedirectResponse("/portafoglio", status_code=303)
    fund = market.get_fundamentals(p.ticker, tipo=p.tipo)
    perf = None
    closes = market.history_closes(market._yahoo_symbol(p.ticker), "1y", "1wk")
    if len(closes) >= 2 and closes[0]:
        perf = round((closes[-1] / closes[0] - 1) * 100, 2)
    res = ai.analizza_posizione(_descrizione_pubblica(p, fund, perf))
    if res.get("ok"):
        settings_store.set_setting(f"ai_take_{p.ticker.upper()}", json.dumps({
            "text": res["text"], "conf": res["conf"],
            # le pagine effettivamente consultate: si citano sempre, così puoi
            # controllare da solo invece di fidarti
            "fonti": res.get("fonti") or [],
            "when": tempo.adesso().isoformat(timespec="minutes")}))
    return RedirectResponse(f"/portafoglio/{pos_id}", status_code=303)
