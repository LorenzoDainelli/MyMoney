"""Grafico del patrimonio (dashboard): serie REALI per i range 1G → MAX.

patrimonio(t) = Σ quantità_attuali × chiusura(t) × cambio_attuale + liquidità(t)

- Le chiusure vengono da Yahoo (3 fetch per titolo: intraday, 1 anno giornaliero,
  storico settimanale "max"). Contribuiscono solo i titoli con quantità inserita.
- La liquidità è ricostruita dai movimenti reali (finance.service).
- Il calcolo richiede molte chiamate HTTP: gira in un thread in background e
  finisce in cache (settings_store). La dashboard non si blocca mai; finché la
  cache non è pronta il grafico mostra "in preparazione".
- Onestà: è una STIMA (quantità di oggi × prezzi storici, cambio di oggi).
  I titoli senza storia entrano come valore costante; niente dati inventati
  oltre a questo, e l'etichetta in pagina dichiara che è una stima.
"""
import json
import logging
import threading
from datetime import datetime, timedelta

from shared import settings_store
from finance import service as fin_service
from portfolio import market
from portfolio.service import lista_posizioni

CACHE_KEY = "wealth_cache"
MAX_POINTS = 90          # punti massimi per serie inviata alla pagina

log = logging.getLogger(__name__)
_building = threading.Lock()

# base dati per i range: intraday (oggi), giornaliero (1 anno), settimanale (tutto)
_FETCHES = {"D0": ("1d", "5m"), "Y1": ("1y", "1d"), "MX": ("max", "1wk")}


def _downsample(pairs: list) -> list:
    if len(pairs) <= MAX_POINTS:
        return pairs
    step = (len(pairs) - 1) / (MAX_POINTS - 1)
    return [pairs[round(i * step)] for i in range(MAX_POINTS)]


def _grid_totale(serie_titoli: list, extra_flat: float) -> list:
    """Somma le serie [(ts, valore)] dei titoli su una griglia comune di
    timestamp (forward-fill: tra due chiusure vale l'ultima nota)."""
    all_ts = sorted({t for s in serie_titoli for t, _ in s})
    if not all_ts:
        return []
    tot = [extra_flat] * len(all_ts)
    for s in serie_titoli:
        j, last = 0, s[0][1]
        for i, ts in enumerate(all_ts):
            while j < len(s) and s[j][0] <= ts:
                last = s[j][1]
                j += 1
            tot[i] += last
    return list(zip(all_ts, tot))


def _griglia_piatta(key: str, now: datetime, base: float) -> list:
    """Nessun titolo ha storia: il patrimonio è la liquidità più il valore di
    oggi dei titoli (`base`), costante. Griglia di date equidistanti (40 punti)
    dall'inizio del tracking (o dalla finestra del range, se più recente) fino
    a ora, così anche con pochi giorni di storia i range non restano senza
    punti. Senza `base` il grafico mostrerebbe la sola liquidità mentre l'hero
    sopra mostra il patrimonio intero: due numeri diversi per la stessa cosa."""
    inizio = fin_service.data_inizio()
    if key == "D0":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif key == "Y1":
        start = max(now - timedelta(days=365), inizio) if inizio else now - timedelta(days=365)
    else:  # MX: tutta la storia = dall'inizio del tracking
        start = inizio
    if start is None or start >= now:
        return []
    n = 40
    step = (now - start) / (n - 1)
    return [((start + step * i).timestamp(), base) for i in range(n)]


def _build() -> dict:
    now = datetime.now()
    floor_ts = fin_service.data_inizio().timestamp()   # niente patrimonio prima dell'inizio del tracking
    posizioni = [p for p in lista_posizioni()
                 if (p.ticker or "").strip() and (p.quantita or 0) > 0]
    qmap = market.quotes_map()

    serie = {k: [] for k in _FETCHES}     # per base: liste di serie già in EUR
    flat = {k: 0.0 for k in _FETCHES}     # valore costante dei titoli senza storia

    # Le tre serie di ogni titolo sono richieste indipendenti: in fila erano
    # ~111 attese di rete una dopo l'altra (il pezzo più lento dell'avvio).
    lavori = [(p, key, rng, itv) for p in posizioni
              for key, (rng, itv) in _FETCHES.items()]

    def scarica(job):
        p, key, rng, itv = job
        return (p, key, market.history_series(market._yahoo_symbol(p.ticker), rng, itv))

    for p, key, s in market._in_parallelo(scarica, lavori):
        q = qmap.get((p.ticker or "").upper())
        fx = (q.price_eur / q.price) if (q and q.ok and q.price and q.price_eur) else 1.0
        if s:
            serie[key].append([(t, c * p.quantita * fx) for t, c in s])
        else:
            # senza storia il titolo entra come valore costante: è una stima
            # dichiarata, non un buco silenzioso
            val_oggi = (q.price_eur * p.quantita) if (q and q.ok and q.price_eur) \
                else (p.valore_posseduto or 0.0)
            flat[key] += val_oggi or 0.0

    griglie = {}
    for key in _FETCHES:
        g = _grid_totale(serie[key], flat[key])
        if not g:
            g = _griglia_piatta(key, now, flat[key])
        # Il taglio all'inizio del tracking va fatto QUI, non solo dopo in
        # `fetta`: la storia "max" di un titolo può partire dal 1962 (IBM) e su
        # Windows datetime.fromtimestamp() di un timestamp negativo alza
        # OSError. L'eccezione veniva inghiottita da _rebuild_safe e il grafico
        # restava congelato all'ultima ricostruzione riuscita.
        g = [x for x in g if x[0] >= floor_ts]
        if g:
            date = [datetime.fromtimestamp(ts) for ts, _ in g]
            liq = fin_service.liquidita_alle_date(date)
            g = [(ts, v + l) for (ts, v), l in zip(g, liq)]
        griglie[key] = g

    def fetta(base: str, days: int | None = None, ytd: bool = False) -> list:
        g = list(griglie.get(base) or [])      # già tagliata a floor_ts sopra
        if days is not None:
            lim = (now - timedelta(days=days)).timestamp()
            g = [x for x in g if x[0] >= lim]
        if ytd:
            lim = datetime(now.year, 1, 1).timestamp()
            g = [x for x in g if x[0] >= lim]
        return g

    defs = [
        ("1G",  fetta("D0")),
        ("1S",  fetta("Y1", days=7)),
        ("1M",  fetta("Y1", days=31)),
        ("3M",  fetta("Y1", days=92)),
        ("6M",  fetta("Y1", days=183)),
        ("YTD", fetta("Y1", ytd=True)),
        ("1A",  fetta("Y1", days=366)),
        ("3A",  fetta("MX", days=3 * 366)),
        ("5A",  fetta("MX", days=5 * 366)),
        ("10A", fetta("MX", days=10 * 366)),
        ("MAX", fetta("MX")),
    ]
    inizio_max = (griglie.get("MX") or [(None,)])[0][0]
    ranges = {}
    for k, g in defs:
        # 3A/5A/10A identici a MAX (storia più corta) non aggiungono nulla
        if k in ("3A", "5A", "10A") and g and inizio_max \
                and abs(g[0][0] - inizio_max) < 15 * 86400:
            continue
        g = _downsample(g)
        if len(g) < 2:
            continue
        first, last = g[0][1], g[-1][1]
        ranges[k] = {
            "t": [int(x[0]) for x in g],
            "v": [round(x[1], 2) for x in g],
            "pct": round((last / first - 1) * 100, 2) if first else None,
        }
    return {"when": now.isoformat(timespec="minutes"), "ranges": ranges}


def _rebuild_safe() -> None:
    if not _building.acquire(blocking=False):
        return                      # un rebuild è già in corso
    try:
        settings_store.set_setting(CACHE_KEY, json.dumps(_build()))
    except Exception:
        # mai far cadere l'app per il grafico — ma nemmeno fallire in silenzio:
        # senza questa riga il grafico resta fermo all'ultima cache buona e
        # niente lo dice.
        log.warning("patrimonio: ricostruzione del grafico fallita", exc_info=True)
    finally:
        _building.release()


def rebuild_async() -> None:
    if _building.locked():
        return
    threading.Thread(target=_rebuild_safe, daemon=True).start()


def get_cached(max_age_min: int = 60) -> dict | None:
    """Serie dal cache; se mancano o sono vecchie lancia il rebuild in
    background e intanto ritorna quello che c'è (o None)."""
    raw = settings_store.get_setting(CACHE_KEY, "")
    data = None
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
    stale = data is None
    if data is not None:
        try:
            when = datetime.fromisoformat(data["when"])
            stale = (datetime.now() - when).total_seconds() > max_age_min * 60
        except (KeyError, ValueError):
            stale = True
    if stale:
        rebuild_async()
    return data
