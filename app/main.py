"""Punto di avvio dell'app (FastAPI).

Crea le tabelle, precarica il portafoglio la prima volta, collega le pagine.
Si avvia con run.py (o col doppio click su Avvia-Finanza.bat).
"""
import json
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from shared.config import APP_DIR, APP_NAME, JOB_TOKEN
from shared.db import Base, engine
from shared.templating import templates
from shared import ai, auth, lavori, settings_store, tempo

# Importa i modelli PRIMA di create_all, cosi' le tabelle vengono registrate.
import shared.settings_store          # noqa: F401  -> tabella shared_settings
import portfolio.models               # noqa: F401  -> tabella portfolio_positions
from portfolio import market          # noqa: F401  -> tabella portfolio_quotes
import finance.models                 # noqa: F401  -> tabelle finance_*
import shared.sync                    # noqa: F401  -> hook diario sync (Fase 4)
from shared import storico            # tabella storico_giornaliero

from portfolio import seed, analytics, wealth, versamenti
from portfolio import service as pf_service
from portfolio.routes import router as portfolio_router
from finance import service as fin_service
from finance.routes import router as finance_router, _contesto_finanze
from finance.api_routes import router as finance_api_router
from shared.settings_routes import router as settings_router
from shared.prefs_routes import router as prefs_router
from news import reader
from news.routes import router as news_router

# --- preparazione database (una tantum) ---
Base.metadata.create_all(bind=engine)
fin_service.migra_schema()             # colonne nuove su DB esistenti (es. colore)
seed.migra_schema()                    # idem per il portafoglio (es. nome_breve)
seed.seed_if_empty()
seed.assicura_posizioni_mancanti()     # titoli nuovi in lista anche su DB già popolati
seed.applica_nomi_brevi()              # nomi corti degli ETF anche su DB già popolati
fin_service.seed_wallets_if_empty()
fin_service.assicura_wallet_brand()    # conti/carte reali (AIB, Hype, Revolut, TR, PayPal), mai generici
fin_service.assicura_salvadanaio()     # «Nascosti» + arrotondamento/saveback sulla carta TR
fin_service.applica_saldi_iniziali()   # saldi di apertura al 4/7/2026 (solo dove ancora a zero)


# --- lavori periodici: prezzi, notizie, pulizia, fotografia del patrimonio ---
# Sul PC partono da soli a ogni avvio, come è sempre stato. Su un server no: là
# c'è un programma di Google che ci chiama una volta al giorno (l'indirizzo è
# più sotto), perché un server si spegne quando non lo usi e la fotografia del
# patrimonio salterebbe i giorni in cui non apri l'app. Vedi shared/lavori.py.
if not JOB_TOKEN:
    lavori.in_background()

# --- app web ---
app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
# Guscio PWA (v2): servito anche dal PC per prova/uso in LAN. In produzione il
# guscio sta su Cloudflare Pages (HTTPS), ma i file sono gli stessi (cartella pwa/).
_PWA_DIR = APP_DIR.parent / "pwa"
if _PWA_DIR.exists():
    app.mount("/pwa", StaticFiles(directory=str(_PWA_DIR), html=True), name="pwa")
app.include_router(portfolio_router)
app.include_router(finance_router)
app.include_router(finance_api_router)
app.include_router(settings_router)
app.include_router(prefs_router)
app.include_router(news_router)


# --------------------------- la porta d'ingresso ---------------------------
@app.middleware("http")
async def _porta_chiusa(request: Request, call_next):
    """Tutto chiuso, tranne il poco che deve restare aperto.

    L'elenco è di ciò che si APRE (shared/auth.py, PERCORSI_LIBERI), non di ciò
    che si chiude: così una pagina nuova nasce protetta, e dimenticarsi di
    proteggerla non è più possibile.

    Sul PC di casa questo controllo non fa nulla: senza chiave di sessione
    configurata `richiede_accesso()` è falso e si passa sempre, esattamente come
    l'app ha sempre funzionato.
    """
    if not auth.richiede_accesso() or auth.percorso_libero(request.url.path):
        return await call_next(request)
    if auth.utente_da_richiesta(request) is None:
        return RedirectResponse("/accedi", status_code=303)
    return await call_next(request)


# --------------------------- servizio (non sono pagine) ---------------------------
@app.get("/salute")
def salute():
    """Dice solo «sono in piedi», senza toccare il database.

    Serve a chi ospita l'app per capire se rispondere o riavviarla. Deve restare
    leggerissimo: se interrogasse il database, un database lento farebbe pensare
    che l'app sia morta e la farebbe riavviare proprio quando è più in difficoltà.
    """
    return {"stato": "ok", "app": APP_NAME}


@app.post("/lavori/giornaliero")
def lavori_giornaliero(request: Request):
    """Fa partire i lavori periodici. Lo chiama una volta al giorno il programma
    di Google (Cloud Scheduler), mai una persona.

    Protetto da una parola d'ordine: senza, chiunque conosca l'indirizzo potrebbe
    farci chiamare i prezzi a raffica. La regola sta in shared/lavori.py, dove è
    coperta dai test — compresa la parte che conta: se la parola d'ordine non è
    configurata NON si passa, invece di lasciare aperto.
    """
    if not lavori.token_valido(request.headers.get("X-Job-Token", "")):
        return JSONResponse({"errore": "non autorizzato"}, status_code=401)
    # su un server il database è uno solo: non c'è niente da sincronizzare
    return lavori.giornaliero(includi_sync=False)


# --------------------------- dashboard (design MyMoney) ---------------------------
def _dashboard_ctx() -> dict:
    """Contesto della dashboard (design freeze v1.0): hero (patrimonio, spesa
    media, saldo), grafico patrimonio per range, migliori/peggiori, notizie,
    dividendi, punto della settimana AI, esposizione per settore."""
    vista = pf_service.vista_portafoglio()
    sal = fin_service.saldi()
    now = tempo.adesso()
    riep = fin_service.riepilogo_mese(now.year, now.month)
    inv_tot = vista["totale"]
    # solo la liquidità: il conto PAC porta già il valore del Portafoglio (inv_tot),
    # sommarlo qui lo conterebbe due volte
    liq = sal["liquido"]
    # ...ma il salvadanaio della carta va aggiunto: quei soldi non sono spendibili
    # (per questo stanno fuori da 'liquido') e non sono ancora titoli — se non li
    # sommassimo qui, il patrimonio calerebbe a ogni arrotondamento e risalirebbe
    # di scatto il giorno in cui la banca compra.
    bloc = sal.get("bloccato", 0.0)
    snapshot = market.get_perf_snapshot()

    # --- IL TUO RISULTATO: quanto vale oggi contro quanto ci hai messo ---------
    # Questo è il numero che riguarda i soldi dell'utente, e per questo apre.
    # Prima qui c'era la performance a 12 mesi dei titoli, per giunta convertita
    # in euro: leggeva «+53 € negli ultimi 12 mesi» mentre il risultato vero era
    # −0,71 €. Non era un'imprecisione, era un'altra cosa — la storia del
    # MERCATO, successa prima che comprasse — spacciata per il suo guadagno.
    versato = round(sum((r["p"].versato_totale or 0.0) for r in vista["righe"]), 2)
    risultato_eur = risultato_pct = None
    if vista["ha_totale"] and versato > 0:
        risultato_eur = round(inv_tot - versato, 2)
        risultato_pct = round((inv_tot / versato - 1) * 100, 2)

    # --- la storia di MERCATO, tenuta separata e dichiarata --------------------
    # Pesata sul VALORE soltanto: mescolare euro e % target nello stesso
    # denominatore (com'era prima) somma mele e pere.
    num = den = 0.0
    for r in vista["righe"]:
        pf = snapshot.get((r["p"].ticker or "").upper())
        if pf is None or not r["valore"]:
            continue
        num += r["valore"] * pf
        den += r["valore"]
    inv_perf = round(num / den, 2) if den else None
    perf_cop = round(den / inv_tot * 100) if (den and inv_tot) else None

    # migliori e peggiori del portafoglio (2 + 2, click → dettaglio)
    movers = []
    if snapshot:
        rows = [(r["p"], snapshot.get((r["p"].ticker or "").upper())) for r in vista["righe"]]
        rows = [(p, pf) for p, pf in rows if pf is not None]
        rows.sort(key=lambda x: x[1], reverse=True)
        sel = rows[:2] + [x for x in rows[-2:] if x not in rows[:2]]
        movers = [{"id": p.id, "tk": p.ticker, "name": p.nome_vista, "pl": pf} for p, pf in sel]

    # dividendi: reddito stimato dai rendimenti reali (solo coi valori inseriti).
    # La «resa» va calcolata sulla STESSA base del reddito — i soli titoli che
    # pagano — e quella base va dichiarata. Prima qui usciva 0,32% (diluito su
    # tutto) mentre l'Analisi mostrava 1,08% (sui paganti): due numeri diversi
    # per la stessa cosa in due pagine, nessuno dei due con la sua base scritta.
    dividendi = None
    if vista["ha_totale"]:
        div_rows, div_tot, val_paganti = [], 0.0, 0.0
        for r in vista["righe"]:
            p = r["p"]
            if not r["valore"] or not (p.ticker or "").strip():
                continue
            f = market.get_fundamentals_cached(p.ticker)
            if f and f.get("div_yield"):
                annuo = r["valore"] * f["div_yield"]
                div_tot += annuo
                val_paganti += r["valore"]
                div_rows.append({"id": p.id, "tk": p.ticker, "annuo": round(annuo, 2)})
        if div_tot > 0:
            div_rows.sort(key=lambda x: -x["annuo"])
            dividendi = {
                "annuo": round(div_tot, 2),
                "mese": round(div_tot / 12, 2),
                "resa": round(div_tot / val_paganti * 100, 2) if val_paganti else None,
                "coperto": round(val_paganti / inv_tot * 100) if inv_tot else None,
                "top": div_rows[:3],
                "top_max": div_rows[0]["annuo"] if div_rows else 1.0,
            }

    # esposizione per settore: look-through dalla sola cache (mai HTTP qui)
    settori = []
    try:
        settori = analytics.look_through(cached_only=True)["settori"][:7]
    except Exception:
        pass

    ai_read = None
    raw = settings_store.get_setting("dash_ai", "")
    if raw:
        try:
            saved = json.loads(raw)
            ai_read = {"text": saved.get("text", ""), "conf": saved.get("conf", "media")}
        except json.JSONDecodeError:
            pass

    news = [{"ticker": c["ticker"], "titolo": c["titolo"],
             "tipo": c["tipo_evento"] or "news", "fonte": c["fonte"],
             "data": c["data_it"], "rilevanza": int(c["rilevanza"] or 0)}
            for c in reader.news_cards(limit=3)]

    # grafico del patrimonio: serie per range dalla cache (rebuild in background)
    w = wealth.get_cached()

    return {
        "patrimonio": round(inv_tot + liq + bloc, 2),
        "investito": inv_tot, "liquido": liq, "bloccato": bloc,
        "versato": versato,
        "risultato_eur": risultato_eur, "risultato_pct": risultato_pct,
        "perf12m": inv_perf, "perf12m_cop": perf_cop,
        "updated": vista["ultimo_agg"], "prezzi": vista["prezzi"], "tr": vista["tr"],
        "spesa_media": round(riep["uscite"] / 30, 2),
        "saldo_mese": riep["saldo"], "entrate": riep["entrate"], "uscite": riep["uscite"],
        # due fatti secchi sotto i numeri, al posto di due card mezze vuote: la
        # spesa più grossa (che una media nasconde) e per quanti giorni la
        # liquidità regge a quella media. Il secondo è una divisione, non una
        # previsione — e la base è la liquidità, non il saldo del mese.
        "spesa_top": fin_service.spesa_top(now.year, now.month),
        "giorni_coperti": (round(liq / (riep["uscite"] / 30))
                           if (riep["uscite"] > 0 and liq > 0) else None),
        "movers": movers, "dividendi": dividendi, "settori": settori,
        "wealth": (w or {}).get("ranges") or {},
        "ai": ai_read, "news": news,
        "ai_on": ai.is_configured(),
        "pac_promemoria": versamenti.promemoria(),
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    # la fotografia di oggi (idempotente: una riga per giorno, si sovrascrive)
    storico.registra()
    # in modalità Proattiva la lettura si aggiorna da sola quando è vecchia,
    # senza far aspettare la pagina (vedi ai.forse_rigenera)
    ai.forse_rigenera("dash_ai", _genera_punto_settimana)
    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "home",
        "d": _dashboard_ctx(),
        "ai_proattivo": ai.proattivo_attivo(),
    })


@app.post("/dashboard/ai")
def dashboard_ai():
    """Genera 'il punto della settimana' (dati aggregati e anonimi) e lo salva."""
    _genera_punto_settimana()
    return RedirectResponse("/", status_code=303)


def _genera_punto_settimana() -> None:
    """Costruisce il contesto della dashboard, chiede la lettura e la salva."""
    contesto = _contesto_finanze()
    # più materiale = lettura più ricca; ogni pezzo è opzionale e non blocca
    try:
        sal = fin_service.saldi()
        pac = fin_service.valore_pac_live()
        contesto += (f"\nLiquidità disponibile (esclusi gli investimenti): "
                     f"{sal['liquido']:.0f}€.")
        if pac:
            contesto += (f"\nPAC: versati {pac['versato']:.2f}€, valore attuale "
                         f"{pac['valore']:.2f}€ (rivalutazione {pac['rivalutazione']:+.2f}€).")
    except Exception:
        pass
    try:
        d = _dashboard_ctx()
        # NB: le performance a 12 mesi dei titoli NON entrano qui di proposito.
        # Sono storia del mercato, avvenuta prima che l'utente comprasse: dargliele
        # significa vederle rispuntare come se fossero suoi guadagni. Restano
        # visibili in pagina, dove sono etichettate per quello che sono, e sulla
        # scheda del singolo titolo dove il discorso è sullo strumento.
        if d["dividendi"]:
            contesto += (f"\nDividendi attesi (stima lorda dai rendimenti dichiarati): "
                         f"{d['dividendi']['annuo']:.2f}€ l'anno, "
                         f"{d['dividendi']['mese']:.2f}€ al mese.")
    except Exception:
        pass
    try:
        lt = analytics.look_through()
        settori = ", ".join(f"{s['key']} {s['pct']}%" for s in lt["settori"][:6])
        contesto += (f"\nPortafoglio investimenti: {lt['n_titoli']} titoli; "
                     f"settori principali: {settori or 'n/d'}.")
    except Exception:
        pass  # senza look-through l'analisi resta valida sui soli dati finanze
    res = ai.punto_settimana(contesto)
    if res.get("ok"):
        settings_store.set_setting("dash_ai", json.dumps({
            "text": res["text"], "conf": res["conf"],
            "when": tempo.adesso().isoformat(timespec="minutes")}))
