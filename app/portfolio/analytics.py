"""Analisi del portafoglio: look-through settoriale e metriche di rischio.

Tutto DESCRITTIVO, mai prescrittivo: mostra fatti (esposizioni, volatilità, ...)
così decidi tu. Niente segnali operativi. I dati mancanti restano fuori dal calcolo
e la copertura viene dichiarata (onestà intellettuale).

Una regola di igiene sui PESI: quando l'utente ha valori reali (quantità × prezzo),
TUTTA la pagina pesa sul valore; quando non li ha, pesa sulla % target. Mai
mescolare le due basi nella stessa schermata — sarebbe confrontare mele con pere.
"""
import json
import math
import re
from datetime import datetime

from portfolio import market
from portfolio.service import lista_posizioni, vista_portafoglio
from shared import settings_store


def _sector_key(label: str) -> str:
    s = (label or "").strip().lower().replace(" ", "_")
    return "realestate" if s == "real_estate" else s


def _valore_posizione(p, qmap) -> float | None:
    """Valore reale in euro (quantità × prezzo), o None se il prezzo manca.
    Un prezzo fallito nell'ultimo aggiornamento (ok=False) conta come mancante:
    meglio escludere la riga che gonfiarla con un dato vecchio o sbagliato."""
    q = qmap.get((p.ticker or "").upper())
    if q and q.ok and q.price_eur is not None and p.quantita:
        return round(q.price_eur * p.quantita, 2)
    return None


def _canon_titolo(symbol: str, name: str) -> str:
    """Chiave canonica di un titolo per il look-through: il SIMBOLO quando c'è —
    così 'NVIDIA Corp' dentro un ETF e la tua 'NVIDIA' diretta risultano lo
    stesso titolo — altrimenti il nome normalizzato. Le classi azionarie diverse
    (GOOG vs GOOGL) restano distinte apposta: sono strumenti diversi."""
    s = (symbol or "").strip().upper().split(".")[0]
    if s and any(c.isalpha() for c in s):
        return s
    return "n:" + re.sub(r"[^a-z0-9]", "", (name or "").lower())


def look_through(cached_only: bool = False) -> dict:
    """Esposizione settoriale aggregata (ETF scomposti nei loro settori, azioni
    per settore). Pesi sul VALORE reale quando c'è, altrimenti sulla % target
    (una base sola per tutta la pagina). Con cached_only=True legge SOLO la cache
    locale (mai HTTP): per la dashboard.

    La copertura dichiarata conta solo il peso che porta DAVVERO un settore: un
    fondo di cui Yahoo non pubblica la scomposizione non «copre» nulla e non deve
    gonfiare il denominatore (era il caso di GIFL)."""
    posizioni = [p for p in lista_posizioni() if not p.is_fisso and (p.ticker or "").strip()]
    fetch = market.get_fundamentals_cached if cached_only else market.get_fundamentals
    qmap = market.quotes_map()

    valori = {p.id: _valore_posizione(p, qmap) for p in posizioni}
    usa_valori = sum(v for v in valori.values() if v) > 0

    def peso(p):
        return (valori[p.id] if usa_valori else p.pct_target) or 0.0

    base = sum(peso(p) for p in posizioni)          # peso analizzabile in totale
    sett: dict[str, float] = {}
    coperto = 0.0                                   # peso che contribuisce un settore
    senza_settore = []                              # chi non pubblica la scomposizione
    for p in posizioni:
        w = peso(p)
        if not w:
            continue                                # in modalità valore: niente prezzo → fuori
        f = fetch(p.ticker) if cached_only else fetch(p.ticker, tipo=p.tipo)
        if not f:
            senza_settore.append(p.ticker)
            continue
        porta_settore = False
        if p.tipo == "ETF" and f.get("sectors"):
            for s in f["sectors"]:
                # stessa normalizzazione delle azioni: così un ETF con
                # "Technology"/"real_estate" e un'azione con "technology"/"Real
                # Estate" finiscono nella STESSA voce, non in due
                k = _sector_key(s["name"])
                sett[k] = sett.get(k, 0.0) + w * s["weight"] / 100.0
            porta_settore = True
        elif p.tipo != "ETF" and f.get("sector"):
            k = _sector_key(f["sector"])
            sett[k] = sett.get(k, 0.0) + w
            porta_settore = True
        if porta_settore:
            coperto += w
        else:
            senza_settore.append(p.ticker)
    settori = []
    if coperto > 0:
        for k, v in sorted(sett.items(), key=lambda x: -x[1]):
            settori.append({"key": k, "pct": round(v / coperto * 100, 1)})
    tech = next((s["pct"] for s in settori if s["key"] == "technology"), 0.0)
    weights = [peso(p) for p in posizioni if peso(p)]
    sw = sum(weights)
    hhi = sum((x / sw) ** 2 for x in weights) if sw else 0
    return {
        "settori": settori,
        "tech": tech,
        "tech_alert": tech > 50,
        "usa_valori": usa_valori,
        "coperto_pct": round(coperto / base * 100, 1) if base else 0.0,
        # chi lascia il buco, per nome: «copertura 89,9%» non dice quali titoli
        # mancano, e senza il nome l'utente non può nemmeno verificare
        "senza_settore": sorted({t for t in senza_settore if t}),
        "eff_holdings": round(1 / hhi, 1) if hhi else 0,
        "n_titoli": len(weights),
    }


def analisi_completa(cached_only: bool = False) -> dict:
    """Sintesi, diversificazione, stile e look-through per titolo (design MyMoney).

    Pesi: il VALORE reale se inserito (quantità/valori), altrimenti la % target.
    Il reddito da dividendi in euro esiste solo coi valori reali. I dati mancanti
    restano None: la pagina li mostra vuoti, mai inventati.

    Due onestà importanti vivono qui:
    - il RISULTATO dell'utente (valore di oggi vs quanto ha versato) è il numero
      che lo riguarda davvero; la performance a 12 mesi del titolo è storia di
      MERCATO, avvenuta prima che comprasse, e va tenuta separata e dichiarata;
    - ogni media parziale (dividendi, TER, performance) porta con sé la sua
      COPERTURA: su quanta parte del portafoglio è calcolata.

    Con `cached_only=True` non parte NESSUNA chiamata di rete: si legge solo la
    cache locale dei fondamentali. Serve alle pagine che devono aprirsi subito
    (l'aggiornamento gira in background)."""
    vista = vista_portafoglio()
    snapshot = market.get_perf_snapshot()
    usa_valori = vista["ha_totale"]

    pesi = []           # (posizione, peso, fondamentali, quotazione)
    versato_tot = 0.0
    esclusi = []        # titoli con quantità ma senza prezzo: fuori dal calcolo, dichiarati
    for r in vista["righe"]:
        p = r["p"]
        if p.is_fisso:
            continue
        versato_tot += (p.versato_totale or 0.0)
        w = r["valore"] if usa_valori else p.pct_target
        if not w:
            if usa_valori and (p.ticker or "").strip() and p.quantita:
                esclusi.append(p.ticker)      # ha quantità ma il prezzo è mancato
            continue
        f = None
        if (p.ticker or "").strip():
            f = (market.get_fundamentals_cached(p.ticker) if cached_only
                 else market.get_fundamentals(p.ticker, tipo=p.tipo))
        pesi.append((p, float(w), f, r.get("q")))
    somma = sum(w for _, w, _, _ in pesi) or 1.0

    perf_n = perf_d = 0.0
    div_n = div_d = reddito = 0.0
    div_scartati: list[str] = []
    ter_n = ter_d = 0.0
    n_etf = n_etf_ter = 0
    etf_w = 0.0
    expo: dict[str, float] = {}         # chiave canonica -> peso (quota nel portafoglio)
    nomi_expo: dict[str, str] = {}      # chiave canonica -> nome da mostrare
    valute: dict[str, float] = {}       # valuta di QUOTAZIONE (dato reale)
    geo: dict[str, float] = {}          # paese: noto solo per le azioni
    geo_cop = 0.0
    for p, w, f, q in pesi:
        pf = snapshot.get((p.ticker or "").upper())
        if pf is not None:
            perf_n += w * pf
            perf_d += w
        if p.tipo == "ETF":
            etf_w += w
            n_etf += 1
        if f:
            if f.get("div_yield_scartato"):
                div_scartati.append(p.ticker)     # dato rotto della fonte, dichiarato
            elif f.get("div_yield"):
                div_n += w * f["div_yield"]
                div_d += w
                if usa_valori:
                    reddito += w * f["div_yield"]
            if p.tipo == "ETF" and f.get("expense_ratio"):
                ter_n += w * f["expense_ratio"]
                ter_d += w
                n_etf_ter += 1
            # esposizione reale: quote dentro gli ETF + azioni dirette, UNITE per
            # titolo (stesso simbolo = stessa riga, mai sdoppiato per il nome)
            if p.tipo == "ETF" and f.get("holdings"):
                for h in f["holdings"]:
                    nome = h.get("name") or h.get("symbol") or ""
                    peso_h = (h.get("weight") or 0) / 100.0
                    if nome and peso_h:
                        k = _canon_titolo(h.get("symbol"), nome)
                        expo[k] = expo.get(k, 0.0) + w / somma * peso_h
                        nomi_expo.setdefault(k, nome)
        if p.tipo != "ETF":
            k = _canon_titolo(p.ticker, p.nome)
            expo[k] = expo.get(k, 0.0) + w / somma
            nomi_expo[k] = p.nome           # la tua posizione ha la precedenza sul nome
            if f and f.get("country"):
                geo[f["country"]] = geo.get(f["country"], 0.0) + w
                geo_cop += w
        cur = (q.currency if (q and q.ok and q.currency) else None)
        if cur:
            valute[cur] = valute.get(cur, 0.0) + w

    ordinati = sorted(pesi, key=lambda x: -x[1])
    top1 = round(ordinati[0][1] / somma * 100, 1) if ordinati else None
    top1_tk = ordinati[0][0].ticker if ordinati else ""
    top5 = round(sum(w for _, w, _, _ in ordinati[:5]) / somma * 100, 1) if ordinati else None
    look = [{"n": nomi_expo[k], "w": round(v * 100, 1)} for k, v in
            sorted(expo.items(), key=lambda x: -x[1])[:8]]
    lista_valute = [{"n": k, "w": round(v / somma * 100, 1)} for k, v in
                    sorted(valute.items(), key=lambda x: -x[1])]
    lista_geo = [{"n": k, "w": round(v / geo_cop * 100, 1)} for k, v in
                 sorted(geo.items(), key=lambda x: -x[1])] if geo_cop else []

    valore_tot = vista["totale"] if usa_valori else None
    # il RISULTATO: quanto vale oggi contro quanto ci hai messo. Questo è il tuo
    # numero; la perf a 12 mesi qui sotto è un'altra cosa (storia del mercato).
    risultato_eur = risultato_pct = None
    if usa_valori and versato_tot > 0:
        risultato_eur = round(valore_tot - versato_tot, 2)
        risultato_pct = round((valore_tot / versato_tot - 1) * 100, 2)

    return {
        "valute": lista_valute,
        "geo": lista_geo,
        "geo_coverage": round(geo_cop / somma * 100, 1) if geo_cop else 0,
        "usa_valori": usa_valori,
        "valore_totale": valore_tot,
        "versato_totale": round(versato_tot, 2) if usa_valori else None,
        "risultato_eur": risultato_eur,
        "risultato_pct": risultato_pct,
        "perf12m": round(perf_n / perf_d, 2) if perf_d else None,
        # su quanta parte del valore è calcolata la perf di mercato (il resto:
        # titoli senza storia a 12 mesi su Yahoo)
        "perf12m_cop": round(perf_d / somma * 100) if (perf_d and somma) else None,
        "div_yield": round(div_n / div_d * 100, 2) if div_d else None,
        # copertura del rendimento da dividendo: gli ETF non pubblicano il dato,
        # quindi la media vale solo sui titoli che lo dichiarano
        "div_coverage": round(div_d / somma * 100) if (div_d and somma) else None,
        "div_income": round(reddito, 2) if (usa_valori and reddito) else None,
        # titoli lasciati fuori perché la fonte dà un rendimento impossibile
        "div_scartati": div_scartati,
        "ter": round(ter_n / ter_d * 100, 2) if ter_d else None,
        "ter_n_etf": n_etf,
        "ter_n_con": n_etf_ter,
        "quota_etf": round(etf_w / somma * 100, 1) if pesi else None,
        "top1": top1, "top1_tk": top1_tk, "top5": top5,
        "look": look, "look_max": look[0]["w"] if look else 1,
        "look_coverage": round(sum(expo.values()) * 100, 1) if expo else 0,
        "n_titoli": len(pesi),
        "esclusi": esclusi,
    }


def _weekly_returns(closes: list) -> list:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


# Versione del calcolo del rischio. Serve a riconoscere uno snapshot vecchio:
# quando cambiano le metriche o il METODO, un numero salvato mesi fa non è
# «un po' datato», è calcolato in un altro modo — e va rifatto, non mostrato.
RISK_VERSIONE = 2


def _rendimenti_in_euro(ticker: str, valuta: str, cache_fx: dict) -> list:
    """Rendimenti settimanali di un titolo VISTI DA UN PORTAFOGLIO IN EURO.

    Un titolo quotato in dollari che sale del 2% mentre il dollaro perde il 3%
    per te è sceso. Prima confrontavamo rendimenti in valuta di quotazione con
    un benchmark in euro: i due lati della misura erano in monete diverse, e il
    beta che ne usciva rispondeva a una domanda che nessuno aveva fatto.

    Il cambio storico entra come serie a sé (EUR/valuta, stesse settimane):
    r_eur = (1 + r_titolo) / (1 + r_cambio) − 1.
    """
    closes = market.history_closes(market._yahoo_symbol(ticker), "1y", "1wk")
    r = _weekly_returns(closes)
    cur = (valuta or "EUR").upper()
    if not r or cur == "EUR":
        return r
    if cur not in cache_fx:
        cache_fx[cur] = _weekly_returns(
            market.history_closes(f"EUR{cur}=X", "1y", "1wk"))
    fx = cache_fx[cur]
    if not fx:
        return []          # senza il cambio non fingiamo: il titolo resta fuori
    n = min(len(r), len(fx))
    r, fx = r[-n:], fx[-n:]
    return [(1 + r[i]) / (1 + fx[i]) - 1 for i in range(n) if fx[i] != -1]


def compute_risk() -> dict | None:
    """Metriche di rischio del portafoglio su ~1 anno di dati settimanali.

    Due scelte che cambiano il significato dei numeri:
    - i pesi sono quelli VERI (valore posseduto) quando ci sono, non le % target:
      la stessa base del resto della pagina;
    - tutto è riportato in EURO, cambio storico incluso, così il rischio è quello
      che corre l'utente e il confronto col mercato globale è fra pari.

    Calcolo pesante: lo lanciamo a richiesta e lo salviamo."""
    posizioni = [p for p in lista_posizioni() if not p.is_fisso and (p.ticker or "").strip()]
    qmap = market.quotes_map()
    valori = {p.id: _valore_posizione(p, qmap) for p in posizioni}
    usa_valori = sum(v for v in valori.values() if v) > 0

    cache_fx: dict[str, list] = {}
    rets, tot_w, esclusi = [], 0.0, []
    for p in posizioni:
        w = (valori[p.id] if usa_valori else p.pct_target) or 0.0
        if not w:
            esclusi.append(p.ticker)
            continue
        q = qmap.get((p.ticker or "").upper())
        r = _rendimenti_in_euro(p.ticker, (q.currency if q else ""), cache_fx)
        if len(r) >= 30:
            rets.append((w, r))
            tot_w += w
        else:
            esclusi.append(p.ticker)
    # il benchmark passa dalla stessa strada: MSCI World, quotato in euro
    bench = _rendimenti_in_euro("IWDA", "EUR", cache_fx)
    if not rets or not bench or tot_w <= 0:
        return None
    L = min(min(len(r) for _, r in rets), len(bench))
    port = [0.0] * L
    for w, r in rets:
        rr = r[-L:]
        for i in range(L):
            port[i] += (w / tot_w) * rr[i]
    b = bench[-L:]
    mean = sum(port) / L
    var = sum((x - mean) ** 2 for x in port) / (L - 1)
    vol = math.sqrt(var) * math.sqrt(52)
    cum = peak = 1.0
    mdd = 0.0
    for x in port:
        cum *= (1 + x)
        peak = max(peak, cum)
        mdd = min(mdd, cum / peak - 1)
    ann = (1 + mean) ** 52 - 1
    bmean = sum(b) / L
    cov = sum((port[i] - mean) * (b[i] - bmean) for i in range(L)) / (L - 1)
    bvar = sum((x - bmean) ** 2 for x in b) / (L - 1)
    snap = {
        "vol": round(vol * 100, 1),
        "mdd": round(mdd * 100, 1),
        "sharpe": round((ann - 0.02) / vol, 2) if vol else None,
        "beta": round(cov / bvar, 2) if bvar else None,
        # perdita mensile attesa max al 95% (parametrica) e correlazione col mercato
        "var95m": round(vol / math.sqrt(12) * 1.645 * 100, 1),
        "r2": round(cov * cov / (var * bvar) * 100, 1) if (var and bvar) else None,
        "ann": round(ann * 100, 1),
        "n": len(rets),
        "weeks": L,
        "when": market.fmt_ts(market.utc_now()),
        "versione": RISK_VERSIONE,
        # la base dei pesi e chi è rimasto fuori: senza queste due righe il
        # numero non è verificabile, e un numero non verificabile è un'opinione
        "base": "valore" if usa_valori else "target",
        "esclusi": sorted({t for t in esclusi if t}),
        "in_euro": True,
    }
    settings_store.set_setting("risk_snapshot", json.dumps(snap))
    return snap


def get_cached_risk() -> dict | None:
    """Lo snapshot salvato, SOLO se è ancora confrontabile con quello di oggi.

    Uno snapshot di una versione precedente non è «un po' vecchio»: gli mancano
    metriche (VaR, R²) ed è calcolato con un altro metodo (pesi target, valute
    miste). Mostrarne metà in silenzio è peggio che non mostrarlo: si legge come
    se quelle metriche non esistessero. Qui torna None e la pagina chiede di
    rifare il conto."""
    raw = settings_store.get_setting("risk_snapshot", "")
    if not raw:
        return None
    try:
        snap = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if snap.get("versione") != RISK_VERSIONE:
        return None
    return snap


def risk_scaduto() -> bool:
    """True se esiste uno snapshot ma è di un metodo vecchio: serve a spiegare
    all'utente PERCHÉ il riquadro è vuoto invece di lasciarlo a indovinare."""
    raw = settings_store.get_setting("risk_snapshot", "")
    if not raw:
        return False
    try:
        return json.loads(raw).get("versione") != RISK_VERSIONE
    except json.JSONDecodeError:
        return True
