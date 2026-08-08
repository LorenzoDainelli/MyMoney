"""Dati di mercato: prezzi correnti e conversione in euro. Tutto GRATIS.

Fonte primaria: Yahoo Finance (lo stesso dato di yfinance) via chiamata diretta,
senza librerie pesanti. Ripiego: Stooq. Nessuna chiave API.

Regole d'oro (dal CLAUDE.md):
- MAI inventare: se un prezzo non è reperibile si segna 'non disponibile' e si
  tiene la data dell'ultimo aggiornamento riuscito.
- Robustezza: ogni titolo è gestito a sé; se una chiamata fallisce, si prosegue.
- I prezzi pubblici NON sono dati sensibili: qui non c'è nulla di personale.
"""
import csv
import http.cookiejar
import io
import json
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, SessionLocal
from portfolio.models import Position

UA = {"User-Agent": "Mozilla/5.0 (finanza-app personale)"}
TIMEOUT = 8


# Quante richieste in parallelo verso Yahoo. Il collo di bottiglia è l'attesa di
# rete, non il PC: in serie l'avvio costava minuti (37 titoli × più chiamate
# ciascuno). Sei è un compromesso: veloce, ma non un martello sul loro server.
PARALLELE = 6


def utc_now() -> datetime:
    """Adesso in UTC, senza fuso attaccato: è il formato con cui il database
    conserva `fetched_at` (utcnow() è deprecato da Python 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _in_parallelo(funzione, elementi: list) -> list:
    """Esegue `funzione` su ogni elemento in parallelo e ritorna i risultati
    riusciti. Un elemento che fallisce NON ferma gli altri (regola di robustezza):
    torna semplicemente None e viene scartato."""
    if not elementi:
        return []

    def protetta(x):
        try:
            return funzione(x)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=PARALLELE) as pool:
        return [r for r in pool.map(protetta, elementi) if r is not None]

# Simboli Yahoo verificati per gli ETF europei: il ticker "semplice" non basta,
# serve il suffisso di borsa. Le azioni USA usano invece il ticker così com'è.
# ETF: simboli in EUR VERIFICATI (XETRA/Milano/Amsterdam) — così i valori sono
# vicini a Trade Republic e senza il giro Londra-$/£-cambio. Prima diversi erano
# su Londra in USD/pence (o, per GIFL, un fondo SBAGLIATO).
SYMBOL_MAP = {
    "IWDA": "IWDA.AS",           # Amsterdam, EUR
    "CSPX": "SXR8.DE",           # XETRA, EUR (era CSPX.L in USD)
    "CNDX": "SXRV.DE",           # XETRA, EUR (era CNDX.L in USD)
    "VHYL": "VGWD.DE",           # XETRA, EUR (era VHYL.L in pence)
    "XDWH": "XDWH.DE",           # XETRA, EUR
    "NATO": "ASWC.DE",           # XETRA, EUR (era NATO.L in USD)
    "NUKL": "NUKL.DE",           # XETRA, EUR
    "XDWM": "XDWM.DE",           # XETRA, EUR
    "GIFL": "IE000CK5G8J7.SG",   # Stuttgart, EUR (era GGRP.L: fondo sbagliato). Dati sottili su Yahoo.
    "UKRN": "UKRN.DE",           # XETRA, EUR (era UKRN.L in USD)
    "HEAL": "2B78.DE",           # XETRA, EUR (era HEAL.L in USD)
    # Su Yahoo l'ETC oro non ha una linea tedesca raggiungibile (provate .DE/.SG/.F,
    # tutte 404): si usa la linea di Londra in EURO, così niente conversione.
    # Stesso strumento di IGLN.L (USD) e SGLN.L (pence), ISIN IE00B4ND3602.
    "EGLN": "EGLN.L",            # LSE, EUR
}


class Quote(Base):
    """Ultimo prezzo noto per un ticker (cache locale)."""
    __tablename__ = "portfolio_quotes"
    ticker: Mapped[str] = mapped_column(String(30), primary_key=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)        # valuta nativa
    currency: Mapped[str] = mapped_column(String(8), default="")
    price_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ----------------------------- recupero dati ------------------------------
def _yahoo_symbol(ticker: str) -> str:
    return SYMBOL_MAP.get(ticker.upper(), ticker)


def _http(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _yahoo_quote(symbol: str):
    """(prezzo, valuta) da Yahoo. Gestisce le quotazioni in pence (GBp/GBX)."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=1d&interval=1d")
    meta = json.loads(_http(url))["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    if price is None:
        raise ValueError("prezzo assente")
    cur_raw = meta.get("currency") or ""
    price = float(price)
    if cur_raw in ("GBp", "GBX"):          # Londra quota spesso in pence
        return price / 100.0, "GBP"
    if cur_raw in ("ZAc", "ILA"):          # altri casi in centesimi
        return price / 100.0, cur_raw[:-1].upper()
    return price, cur_raw.upper()


# Valute quotate in centesimi/pence: le chiusure vanno divise per 100 per avere
# l'unità "grande" (come fa _yahoo_quote sul prezzo corrente). Se non lo facciamo,
# lo storico di un titolo di Londra risulta 100× troppo grande.
_CENT_CUR = {"GBp": 100.0, "GBX": 100.0, "ZAc": 100.0, "ILA": 100.0}


def _cent_divisor(currency) -> float:
    return _CENT_CUR.get(currency or "", 1.0)


def _stooq_quote(ticker: str):
    """Ripiego: Stooq (CSV). Funziona soprattutto per i titoli USA ('aapl.us')."""
    sym = f"{ticker.lower()}.us"
    url = f"https://stooq.com/q/l/?s={urllib.parse.quote(sym)}&f=sd2t2ohlc&e=csv"
    rows = list(csv.DictReader(io.StringIO(_http(url).decode("utf-8", "replace"))))
    if not rows:
        raise ValueError("nessun dato")
    close = rows[0].get("Close")
    if not close or close in ("N/D", "0"):
        raise ValueError("nessuna chiusura")
    return float(close), "USD"


_FX_CACHE: dict[str, float] = {}
_fx_lock = threading.Lock()


def _fx_to_eur_rate(currency: str) -> float:
    """Quante unità di 'currency' valgono 1 EUR (per dividere e ottenere gli euro).
    Con l'aggiornamento in parallelo più titoli chiedono lo stesso cambio insieme:
    il lucchetto evita di scaricarlo N volte."""
    cur = (currency or "EUR").upper()
    if cur == "EUR":
        return 1.0
    if cur in _FX_CACHE:
        return _FX_CACHE[cur]
    with _fx_lock:
        if cur in _FX_CACHE:
            return _FX_CACHE[cur]
        rate, _ = _yahoo_quote(f"EUR{cur}=X")
        _FX_CACHE[cur] = rate
        return rate


# ----------------------------- aggiornamento ------------------------------
def _store(ticker, **kw):
    with SessionLocal() as db:
        q = db.get(Quote, ticker) or Quote(ticker=ticker)
        for k, v in kw.items():
            setattr(q, k, v)
        db.add(q)
        db.commit()


def _scarica_uno(ticker: str) -> dict:
    """SOLO rete: prezzo, valuta e performance a ~12 mesi di un ticker.

    Non scrive niente. La scrittura la fa il chiamante, tutta insieme: SQLite
    regge male molte transazioni in parallelo, e comunque salvare 37 righe è
    istantaneo — il tempo se ne va tutto nell'attesa della rete."""
    now = utc_now()
    out = {"ticker": ticker, "fetched_at": now}
    try:
        price, cur = _yahoo_quote(_yahoo_symbol(ticker))
        source = "Yahoo"
    except Exception as e1:
        try:
            price, cur = _stooq_quote(ticker)
            source = "Stooq"
        except Exception:
            out.update(ok=False, error=type(e1).__name__, price=None,
                       currency="", price_eur=None, source="")
            return out
    try:
        rate = _fx_to_eur_rate(cur)
        price_eur = round(price / rate, 4) if rate else None
    except Exception:
        price_eur = None
    out.update(price=round(price, 4), currency=cur, price_eur=price_eur,
               source=source, ok=True, error="")
    closes = history_closes(_yahoo_symbol(ticker), "1y", "1wk")
    if len(closes) >= 2 and closes[0]:
        out["perf12m"] = round((closes[-1] / closes[0] - 1) * 100, 2)
    return out


def _tickers_unici() -> list[str]:
    with SessionLocal() as db:
        tickers = [(p.ticker or "").strip() for p in db.query(Position).all()
                   if (p.ticker or "").strip()]
    visti, fuori = set(), []
    for tk in tickers:
        if tk.upper() not in visti:
            visti.add(tk.upper())
            fuori.append(tk)
    return fuori


def refresh_all() -> int:
    """Aggiorna i prezzi di tutti i ticker del portafoglio. Ritorna quanti ok.

    Nello stesso giro calcola anche la performance a ~12 mesi di ogni titolo
    (chiusure settimanali) e la salva come snapshot: la dashboard la legge dalla
    cache senza fare chiamate di rete a ogni apertura."""
    _FX_CACHE.clear()
    risultati = _in_parallelo(_scarica_uno, _tickers_unici())
    perf = {}
    with SessionLocal() as db:
        for r in risultati:
            tk = r["ticker"]
            q = db.get(Quote, tk) or Quote(ticker=tk)
            for k in ("price", "currency", "price_eur", "source", "ok",
                      "error", "fetched_at"):
                if k in r:
                    setattr(q, k, r[k])
            db.add(q)
            if r.get("perf12m") is not None:
                perf[tk.upper()] = r["perf12m"]
        db.commit()
        ok = db.query(Quote).filter(Quote.ok.is_(True)).count()
    _save_perf_snapshot(perf)
    return ok


def _save_perf_snapshot(perf: dict) -> None:
    from shared import settings_store
    try:
        settings_store.set_setting("perf12m_snapshot", json.dumps(
            {"when": utc_now().isoformat(), "perf": perf}))
    except Exception:
        pass  # lo snapshot è un extra: mai far fallire l'aggiornamento prezzi


def get_perf_snapshot() -> dict:
    """{TICKER: perf% ~12 mesi} dall'ultimo refresh_all. {} se mai calcolato."""
    from shared import settings_store
    raw = settings_store.get_setting("perf12m_snapshot", "")
    if not raw:
        return {}
    try:
        return json.loads(raw).get("perf", {}) or {}
    except json.JSONDecodeError:
        return {}


# ----------------------------- lettura/cache ------------------------------
def quotes_map() -> dict:
    with SessionLocal() as db:
        return {q.ticker.upper(): q for q in db.query(Quote).all()}


def last_update():
    with SessionLocal() as db:
        rows = [q.fetched_at for q in db.query(Quote).all() if q.fetched_at]
    return max(rows) if rows else None


def is_stale(max_age_min: int = 360) -> bool:
    lu = last_update()
    if lu is None:
        return True
    return (utc_now() - lu).total_seconds() / 60.0 > max_age_min


def stato_prezzi(max_age_min: int = 360) -> dict:
    """Quanto sono vecchi i prezzi, in una forma che la pagina può dire a parole.

    Serviva: `is_stale` esisteva, era documentata e non la chiamava nessuno —
    l'app poteva restare aperta giorni mostrando prezzi di giorni prima con la
    sola data in piccolo a difenderla."""
    lu = last_update()
    if lu is None:
        return {"mai": True, "vecchi": True, "minuti": None, "quando": None}
    minuti = int((utc_now() - lu).total_seconds() / 60)
    return {"mai": False, "vecchi": minuti > max_age_min,
            "minuti": minuti, "ore": round(minuti / 60, 1),
            "giorni": minuti // 1440, "quando": fmt_ts(lu)}


def fmt_ts(dt) -> str | None:
    """Un istante UTC del database scritto nell'ora del fuso SCELTO.

    Qui dentro il database tiene tutto in UTC, che è giusto per confrontare due
    istanti e inutile da leggere: «prezzi aggiornati alle 11:24» quando sono le
    13:24 sembra un'app rotta. Prima la conversione era l'ora di Roma calcolata
    a mano — corretta finché stavi in Italia. Adesso segue il fuso che hai
    scelto, che è anche l'unico modo di funzionare fuori dall'Europa (le regole
    dell'ora legale americana sono altre date).
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    from shared import tempo
    return tempo.a_naive(dt).strftime("%d/%m · %H:%M")


# =========================================================================
#  FONDAMENTALI: holdings degli ETF, settori, dati di fondo, dividend yield.
#  Yahoo richiede un "crumb" (token) + cookie per questi dati: lo gestiamo qui.
#  Le holdings GRATIS sono le Top 10: lo segnaliamo come elenco PARZIALE.
# =========================================================================
class Fundamentals(Base):
    """Cache dei dati di fondo/holdings per un ticker (JSON normalizzato)."""
    __tablename__ = "portfolio_fundamentals"
    ticker: Mapped[str] = mapped_column(String(30), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), default="")     # etf | stock
    data: Mapped[str] = mapped_column(Text, default="")           # JSON
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


_session_lock = threading.Lock()
_opener = None
_crumb = None


def _ensure_session():
    global _opener, _crumb
    if _opener and _crumb:
        return
    with _session_lock:
        if _opener and _crumb:
            return
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        try:
            op.open(urllib.request.Request("https://fc.yahoo.com/", headers=UA), timeout=TIMEOUT)
        except Exception:
            pass  # serve solo a piazzare i cookie; spesso risponde 404
        crumb = op.open(
            urllib.request.Request("https://query2.finance.yahoo.com/v1/test/getcrumb", headers=UA),
            timeout=TIMEOUT).read().decode().strip()
        if not crumb:
            raise ValueError("crumb vuoto")
        _opener, _crumb = op, crumb


def _quote_summary(symbol: str, modules: str) -> dict:
    _ensure_session()
    url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
           f"{urllib.parse.quote(symbol)}?modules={urllib.parse.quote(modules)}"
           f"&crumb={urllib.parse.quote(_crumb)}")
    raw = _opener.open(urllib.request.Request(url, headers=UA), timeout=TIMEOUT).read()
    res = json.loads(raw).get("quoteSummary", {}).get("result")
    if not res:
        raise ValueError("nessun risultato")
    return res[0]


def _rawv(x):
    return x.get("raw") if isinstance(x, dict) else x


def _normalize(r: dict) -> dict:
    price = r.get("price", {}) or {}
    sd = r.get("summaryDetail", {}) or {}
    fp = r.get("fundProfile", {}) or {}
    ap = r.get("assetProfile", {}) or {}
    th = r.get("topHoldings", {}) or {}
    fees = fp.get("feesExpensesInvestment", {}) or {}
    holdings = [{"symbol": h.get("symbol"), "name": h.get("holdingName"),
                 "weight": round((_rawv(h.get("holdingPercent")) or 0) * 100, 2)}
                for h in th.get("holdings", []) if _rawv(h.get("holdingPercent"))]
    sectors = []
    for s in th.get("sectorWeightings", []) or []:
        for k, v in s.items():
            w = _rawv(v)
            if w:
                sectors.append({"name": k, "weight": round(w * 100, 2)})
    return {
        "name": price.get("longName") or price.get("shortName"),
        "currency": price.get("currency"),
        "quote_type": price.get("quoteType"),
        "category": fp.get("categoryName"),
        "total_assets": _rawv(sd.get("totalAssets")),
        "expense_ratio": _rawv(fees.get("annualReportExpenseRatio")),
        "div_yield": _rawv(sd.get("yield")) or _rawv(sd.get("dividendYield")) or _rawv(sd.get("trailingAnnualDividendYield")),
        "beta": _rawv(sd.get("beta")) or _rawv(sd.get("beta3Year")),
        "pe": _rawv(sd.get("trailingPE")),
        "sector": ap.get("sector"),
        "industry": ap.get("industry"),
        "country": ap.get("country"),
        "holdings": holdings,
        "holdings_count": _rawv(th.get("holdingsCount")) if th.get("holdingsCount") else None,
        "sectors": sectors,
    }


def _store_fund(ticker, **kw):
    key = ticker.upper()
    with SessionLocal() as db:
        f = db.get(Fundamentals, key) or Fundamentals(ticker=key)
        for k, v in kw.items():
            setattr(f, k, v)
        db.add(f)
        db.commit()


MODULES = "topHoldings,fundProfile,summaryDetail,assetProfile,price,quoteType"


def _scarica_fondamentali(coppia: tuple) -> dict:
    """SOLO rete: i fondamentali di un ticker. Come per i prezzi, la scrittura
    resta al chiamante così il parallelo non litiga con SQLite."""
    ticker, tipo = coppia
    now = utc_now()
    try:
        data = _normalize(_quote_summary(_yahoo_symbol(ticker), MODULES))
        kind = "etf" if (data.get("holdings") or tipo == "ETF") else "stock"
        return {"ticker": ticker, "kind": kind, "data": json.dumps(data),
                "ok": True, "error": "", "fetched_at": now}
    except Exception as e:
        return {"ticker": ticker, "ok": False, "error": type(e).__name__,
                "fetched_at": now}


def fetch_fundamentals(ticker: str, tipo: str = "") -> None:
    r = _scarica_fondamentali((ticker, tipo))
    _store_fund(**r)


def get_fundamentals(ticker: str, max_age_h: int = 24, tipo: str = "") -> dict | None:
    """Ritorna i fondamentali dalla cache; li scarica se mancanti o vecchi.
    Ritorna None se non reperibili (cosi' la pagina mostra 'non disponibile')."""
    key = (ticker or "").strip().upper()
    if not key:
        return None
    with SessionLocal() as db:
        f = db.get(Fundamentals, key)
    stale = (f is None) or (f.fetched_at is None) or \
            ((utc_now() - f.fetched_at).total_seconds() / 3600.0 > max_age_h)
    if stale:
        fetch_fundamentals(ticker, tipo)
        with SessionLocal() as db:
            f = db.get(Fundamentals, key)
    if f and f.ok and f.data:
        d = json.loads(f.data)
        d["_fetched"] = fmt_ts(f.fetched_at)
        return d
    return None


def get_fundamentals_cached(ticker: str) -> dict | None:
    """Fondamentali SOLO dalla cache locale (mai HTTP): per le pagine che non
    devono bloccarsi (dashboard). L'aggiornamento avviene in background."""
    key = (ticker or "").strip().upper()
    if not key:
        return None
    with SessionLocal() as db:
        f = db.get(Fundamentals, key)
    if f and f.ok and f.data:
        d = json.loads(f.data)
        d["_fetched"] = fmt_ts(f.fetched_at)
        return d
    return None


def refresh_all_fundamentals(max_age_h: int = 24) -> None:
    """Scarica/aggiorna i fondamentali di tutti i ticker (per look-through e
    rischio). Scarica solo quelli davvero scaduti, e in parallelo."""
    with SessionLocal() as db:
        positions = [(p.ticker.strip(), p.tipo) for p in db.query(Position).all()
                     if (p.ticker or "").strip()]
        cache = {f.ticker: f.fetched_at for f in db.query(Fundamentals).all()}
    ora = utc_now()
    seen, da_fare = set(), []
    for tk, tipo in positions:
        if tk.upper() in seen:
            continue
        seen.add(tk.upper())
        quando = cache.get(tk.upper())
        if quando is None or (ora - quando).total_seconds() / 3600.0 > max_age_h:
            da_fare.append((tk, tipo))
    risultati = _in_parallelo(_scarica_fondamentali, da_fare)
    for r in risultati:
        try:
            _store_fund(**r)
        except Exception:
            pass


_fond_lock = threading.Lock()


def refresh_fondamentali_async(max_age_h: int = 24) -> None:
    """Aggiorna i fondamentali in un thread, senza far aspettare la pagina.
    Se un aggiornamento è già in corso non ne parte un secondo."""
    if _fond_lock.locked():
        return

    def lavora():
        with _fond_lock:
            try:
                refresh_all_fundamentals(max_age_h=max_age_h)
            except Exception:
                pass          # i dati vecchi restano visibili: mai una pagina rotta

    threading.Thread(target=lavora, daemon=True).start()


def history_closes(symbol: str, rng: str = "1y", interval: str = "1wk") -> list:
    """Serie storica delle chiusure (per le metriche di rischio). [] se non c'e'."""
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(symbol)}?range={rng}&interval={interval}")
        res = json.loads(_http(url))["chart"]["result"][0]
        div = _cent_divisor(res.get("meta", {}).get("currency"))
        closes = res["indicators"]["quote"][0]["close"]
        return [c / div for c in closes if c is not None]
    except Exception:
        return []


def history_series(symbol: str, rng: str = "1y", interval: str = "1d") -> list:
    """Serie storica (timestamp, chiusura) per il grafico del patrimonio.
    Lista di coppie (epoch_sec, close); [] se non disponibile."""
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(symbol)}?range={rng}&interval={interval}")
        res = json.loads(_http(url))["chart"]["result"][0]
        div = _cent_divisor(res.get("meta", {}).get("currency"))
        ts = res.get("timestamp") or []
        closes = res["indicators"]["quote"][0]["close"]
        return [(t, c / div) for t, c in zip(ts, closes) if c is not None]
    except Exception:
        return []
