"""Come sono andate le stime del monitor: il conto che nessuno faceva.

Il robot-notizie dichiara per ogni notizia un impatto atteso (positivo / neutro
/ negativo) su tre orizzonti e una confidenza (bassa / media / alta). Sono state
scritte 240 stime e non le ha mai controllate nessuno — il che è curioso per uno
strumento la cui prima regola è «non sei un oracolo».

Qui si verifica solo l'orizzonte **breve** (1-5 giorni di borsa), l'unico per cui
esiste già il dato: `medio` arriva alla trimestrale, `lungo` fra un anno o due.

Tre scelte che vanno dichiarate perché cambiano il risultato:

1. **Da quando si misura.** Prezzo di riferimento = ultima chiusura fino alla
   data della notizia; confronto = chiusura 5 giorni di borsa dopo.
2. **Cosa vuol dire "positivo".** Serve una soglia, altrimenti qualunque
   movimento di mezzo punto sarebbe una direzione: qui è ±1,5% su 5 giorni.
   Dentro la banda = neutro.
3. **Il metro di paragone.** Il numero che conta NON è «quante ne ha prese»: se
   in quel periodo saliva tutto, «positivo» ci azzecca da solo. Il confronto
   giusto è con chi avesse sempre detto la risposta più frequente — la
   `baseline` qui sotto. Sopra quella soglia c'è informazione, sotto no.

E resta il limite più grosso, che la pagina deve dire a voce alta: sono poche
settimane e poche decine di titoli, molti ripetuti. Non è una statistica, è un
primo sguardo.
"""
import json
from datetime import datetime, timedelta

from shared import settings_store

SOGLIA_PCT = 1.5          # oltre questa variazione su 5 giorni la direzione conta
GIORNI_BORSA = 5          # l'orizzonte "breve" definito nel CLAUDE.md
CHIAVE_CACHE = "verifica_stime"
CONFIDENZE = ("alta", "media", "bassa")


def _direzione(var_pct: float) -> str:
    if var_pct > SOGLIA_PCT:
        return "positivo"
    if var_pct < -SOGLIA_PCT:
        return "negativo"
    return "neutro"


def _norm(val) -> str:
    s = str(val or "").lower()
    if "positiv" in s:
        return "positivo"
    if "negativ" in s:
        return "negativo"
    return "neutro"


def _serie_per_ticker(tickers: set) -> dict:
    """{TICKER: [(data, chiusura), ...]} — una sola richiesta per titolo."""
    from portfolio import market

    def scarica(tk):
        serie = market.history_series(market._yahoo_symbol(tk), "1y", "1d")
        if not serie:
            return None
        return (tk, [(datetime.fromtimestamp(ts).date(), c) for ts, c in serie])

    return dict(market._in_parallelo(scarica, sorted(tickers)))


def _esito(serie: list, quando, atteso: str) -> dict:
    """Verifica UNA stima contro i prezzi. Ritorna anche i casi non verificabili:
    «non lo so» è un esito legittimo e va contato a parte, mai scartato in
    silenzio (scartarlo gonfierebbe la percentuale di successo)."""
    prima = [(d, c) for d, c in serie if d <= quando]
    if not prima:
        return {"stato": "senza_dati"}
    i = len(prima) - 1
    if i + GIORNI_BORSA >= len(serie):
        return {"stato": "in_attesa"}
    p0, p1 = serie[i][1], serie[i + GIORNI_BORSA][1]
    if not p0:
        return {"stato": "senza_dati"}
    var = (p1 / p0 - 1) * 100
    reale = _direzione(var)
    return {"stato": "verificata", "var_pct": round(var, 2), "reale": reale,
            "azzeccata": reale == atteso, "dal": serie[i][0], "al": serie[i + GIORNI_BORSA][0]}


def calcola() -> dict:
    """Verifica tutte le stime verificabili e riassume. Chiamata a richiesta:
    scarica lo storico dei titoli citati."""
    from news import reader

    items = [it for it in reader._load_items()
             if (it.get("ticker") or "").strip() and it.get("data")]
    serie = _serie_per_ticker({it["ticker"].strip().upper() for it in items})

    righe, conteggi = [], {c: {"tot": 0, "ok": 0} for c in CONFIDENZE}
    reali = {"positivo": 0, "neutro": 0, "negativo": 0}
    in_attesa = senza_dati = 0
    for it in items:
        tk = it["ticker"].strip().upper()
        try:
            quando = datetime.fromisoformat(str(it["data"])[:10]).date()
        except ValueError:
            continue
        atteso = _norm((it.get("impatto") or {}).get("breve"))
        s = serie.get(tk)
        res = _esito(s, quando, atteso) if s else {"stato": "senza_dati"}
        if res["stato"] == "in_attesa":
            in_attesa += 1
            continue
        if res["stato"] == "senza_dati":
            senza_dati += 1
            continue
        conf = str(it.get("confidenza", "media")).lower()
        conf = conf if conf in CONFIDENZE else "media"
        conteggi[conf]["tot"] += 1
        conteggi[conf]["ok"] += 1 if res["azzeccata"] else 0
        reali[res["reale"]] += 1
        righe.append({
            "ticker": tk, "data": quando.strftime("%d/%m/%Y"),
            "titolo": (it.get("titolo") or "")[:110],
            "atteso": atteso, "reale": res["reale"], "var_pct": res["var_pct"],
            "azzeccata": res["azzeccata"], "confidenza": conf,
            "rilevanza": it.get("rilevanza") or 0, "url": it.get("url", ""),
        })

    tot = sum(c["tot"] for c in conteggi.values())
    ok = sum(c["ok"] for c in conteggi.values())
    # se avessi detto SEMPRE l'esito più frequente del periodo, quanto avresti
    # preso? È il metro vero: sotto questa riga, la stima non aggiunge nulla.
    baseline = round(max(reali.values()) / tot * 100, 1) if tot else None
    righe.sort(key=lambda r: (r["data"][6:], r["data"][3:5], r["data"][:2]), reverse=True)
    dati = {
        "quando": datetime.now().isoformat(timespec="minutes"),
        "totale": tot, "azzeccate": ok,
        "pct": round(ok / tot * 100, 1) if tot else None,
        "baseline": baseline,
        "baseline_esito": max(reali, key=reali.get) if tot else None,
        "in_attesa": in_attesa, "senza_dati": senza_dati,
        "per_confidenza": {c: {**conteggi[c],
                               "pct": round(conteggi[c]["ok"] / conteggi[c]["tot"] * 100, 1)
                               if conteggi[c]["tot"] else None}
                           for c in CONFIDENZE},
        "distribuzione": reali,
        "soglia": SOGLIA_PCT, "giorni": GIORNI_BORSA,
        "righe": righe[:60],
    }
    settings_store.set_setting(CHIAVE_CACHE, json.dumps(dati, default=str))
    return dati


def dalla_cache() -> dict | None:
    raw = settings_store.get_setting(CHIAVE_CACHE, "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
