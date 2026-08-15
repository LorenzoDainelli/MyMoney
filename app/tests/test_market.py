"""Il modulo da cui nasce OGNI numero dell'app — e che non aveva un solo test.

`market.py` è il posto dove un errore fa più danno, perché non si vede: un
simbolo sbagliato dà un prezzo plausibile ma di un altro strumento, e da lì in
poi valore, risultato, settori, rischio e le letture dell'agente sono tutti
sbagliati insieme, con l'aria di essere giusti. Non è teoria: in questo file ci
sono già stati un fondo sbagliato (GIFL), due ETF letti in dollari invece che
in euro e i pence di Londra non divisi per cento.

Qui si difende quello strato. Niente rete: le risposte di Yahoo sono finte.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import portfolio.market as M


# ------------------------- simboli: la mappa degli ETF -------------------------
def test_gli_etf_europei_non_usano_il_ticker_nudo():
    """Il ticker «semplice» su Yahoo prende un'altra linea di borsa (spesso in
    dollari o pence). Ogni ETF del portafoglio deve passare dalla mappa."""
    for tk in ("IWDA", "CSPX", "CNDX", "VHYL", "GIFL", "HEAL", "NATO", "UKRN"):
        assert M._yahoo_symbol(tk) != tk, f"{tk} finirebbe sulla borsa sbagliata"
        assert "." in M._yahoo_symbol(tk)


def test_un_ticker_sconosciuto_resta_se_stesso():
    """Le azioni USA usano il ticker così com'è: la mappa non deve inventare."""
    assert M._yahoo_symbol("AAPL") == "AAPL"
    assert M._yahoo_symbol("nvda") == "nvda"


# ------------------------- pence e centesimi -------------------------
def _finta_chart(prezzo, valuta, chiusure=None):
    res = {"meta": {"regularMarketPrice": prezzo, "currency": valuta}}
    if chiusure is not None:
        res["indicators"] = {"quote": [{"close": chiusure}]}
        res["timestamp"] = list(range(len(chiusure)))
    return json.dumps({"chart": {"result": [res]}}).encode()


def test_londra_quota_in_pence_e_va_divisa_per_cento(monkeypatch):
    """Un titolo a 950 GBp vale 9,50 sterline, non 950: senza la divisione il
    portafoglio risulterebbe cento volte più ricco."""
    monkeypatch.setattr(M, "_http", lambda url: _finta_chart(950.0, "GBp"))
    prezzo, valuta = M._yahoo_quote("XXX.L")
    assert prezzo == 9.5
    assert valuta == "GBP"


def test_anche_lo_STORICO_va_diviso_per_cento(monkeypatch):
    """Il prezzo corrente era già gestito, lo storico no: bastava quello per
    far sballare di 100× il grafico e le metriche di rischio."""
    monkeypatch.setattr(M, "_http", lambda url: _finta_chart(0, "GBp", [100.0, 200.0]))
    assert M.history_closes("XXX.L") == [1.0, 2.0]


def test_una_valuta_normale_non_viene_toccata(monkeypatch):
    monkeypatch.setattr(M, "_http", lambda url: _finta_chart(12.34, "usd", [10.0, 11.0]))
    assert M._yahoo_quote("AAPL") == (12.34, "USD")
    assert M.history_closes("AAPL") == [10.0, 11.0]


def test_prezzo_assente_non_diventa_zero(monkeypatch):
    """Yahoo a volte risponde 200 con il prezzo a null: deve essere un errore,
    non uno zero che entra nei conti."""
    monkeypatch.setattr(M, "_http", lambda url: _finta_chart(None, "EUR"))
    try:
        M._yahoo_quote("XXX")
        assert False, "un prezzo assente non deve passare"
    except ValueError:
        pass


def test_storico_irraggiungibile_torna_vuoto(monkeypatch):
    def esplode(url):
        raise OSError("rete giù")
    monkeypatch.setattr(M, "_http", esplode)
    assert M.history_closes("AAPL") == []      # mai un'eccezione fuori da qui


# ------------------------- cambio -------------------------
def test_il_cambio_si_scarica_una_volta_sola(monkeypatch):
    """Con l'aggiornamento in parallelo più titoli chiedono insieme lo stesso
    cambio: deve partire UNA richiesta, non una per titolo."""
    M._FX_CACHE.clear()
    chiamate = []

    def finto(sym):
        chiamate.append(sym)
        return 1.08, "USD"

    monkeypatch.setattr(M, "_yahoo_quote", finto)
    assert M._fx_to_eur_rate("USD") == 1.08
    assert M._fx_to_eur_rate("USD") == 1.08
    assert len(chiamate) == 1
    assert M._fx_to_eur_rate("EUR") == 1.0     # l'euro non si scarica mai
    assert len(chiamate) == 1
    M._FX_CACHE.clear()


# ------------------------- normalizzazione dei fondamentali -------------------------
def test_normalize_legge_settori_e_holdings():
    grezzo = {
        "price": {"longName": "Fondo Prova", "currency": "EUR"},
        "summaryDetail": {"yield": {"raw": 0.0312}},
        "fundProfile": {"feesExpensesInvestment": {"annualReportExpenseRatio": {"raw": 0.0007}}},
        "topHoldings": {
            "holdings": [{"symbol": "NVDA", "holdingName": "NVIDIA Corp",
                          "holdingPercent": {"raw": 0.0812}}],
            "sectorWeightings": [{"technology": {"raw": 0.35}}, {"realestate": {"raw": 0.0}}],
        },
    }
    d = M._normalize(grezzo)
    assert d["name"] == "Fondo Prova"
    assert d["div_yield"] == 0.0312
    assert d["expense_ratio"] == 0.0007
    assert d["holdings"] == [{"symbol": "NVDA", "name": "NVIDIA Corp", "weight": 8.12}]
    # i pesi a zero non sono un settore: entrerebbero come voce vuota nel grafico
    assert d["sectors"] == [{"name": "technology", "weight": 35.0}]


def test_normalize_regge_una_risposta_vuota():
    """Per gli ETF europei Yahoo risponde spesso con quasi niente: nessuna
    chiave deve mancare, o le pagine esploderebbero a valle."""
    d = M._normalize({})
    for chiave in ("name", "sectors", "holdings", "div_yield", "expense_ratio", "sector"):
        assert chiave in d
    assert d["sectors"] == [] and d["holdings"] == []


def test_div_yield_assurdo_viene_scartato_e_dichiarato():
    """Il caso vero: SSNLF (Samsung sull'OTC) quota 65,21 USD ma Yahoo divide
    un dividendo in WON per quel prezzo, e pubblica 14,35 — cioè il 1435%.
    Nessun campo affidabile lo copre, quindi la vecchia catena di `or` ci
    scivolava sopra e il numero finiva in euro nel box dividendi."""
    d = M._normalize({
        "price": {"longName": "Samsung Electronics Co., Ltd.", "currency": "USD"},
        "summaryDetail": {"trailingAnnualDividendYield": {"raw": 14.353627},
                          "trailingAnnualDividendRate": {"raw": 936.0}},
    })
    assert d["div_yield"] is None          # fuori dal calcolo
    assert "14.35" in d["div_yield_scartato"]   # e detto, non tolto in silenzio


def test_div_yield_plausibile_resta():
    """Il tetto è generoso di proposito: un REIT al 12% è un fatto, non un errore."""
    d = M._normalize({"summaryDetail": {"dividendYield": {"raw": 0.12}}})
    assert d["div_yield"] == 0.12 and d["div_yield_scartato"] is None


def test_div_yield_zero_prova_il_campo_dopo():
    """Un campo a zero significa «qui non lo so», non «non paga dividendi»:
    la vecchia catena di `or` si comportava così e va conservato."""
    d = M._normalize({"summaryDetail": {"yield": {"raw": 0.0},
                                        "dividendYield": {"raw": 0.0242}}})
    assert d["div_yield"] == 0.0242


def test_cache_gia_avvelenata_smette_di_contare_subito():
    """Il valore assurdo è già SALVATO. Deve sparire alla rilettura, senza
    aspettare le 24 ore di scadenza della cache."""
    class Finta:
        data = '{"div_yield": 14.353627, "name": "Samsung"}'
        fetched_at = None
    d = M._rileggi(Finta())
    assert d["div_yield"] is None and d["div_yield_scartato"]


# ------------------------- fuso orario -------------------------
def test_ora_legale_e_solare_sono_diverse(monkeypatch):
    """L'ora mostrata segue l'ora legale. Prima l'offset era +2 fisso: da fine
    ottobre a fine marzo ogni orario era un'ora avanti, per mezzo anno, senza
    che nulla lo dicesse."""
    from shared import tempo
    monkeypatch.setattr(tempo, "nome_fuso", lambda: "Europe/Rome")
    luglio = datetime(2026, 7, 15, 12, 0)       # UTC
    dicembre = datetime(2026, 12, 15, 12, 0)
    assert M.fmt_ts(luglio) == "15/07 · 14:00"  # ora legale: +2
    assert M.fmt_ts(dicembre) == "15/12 · 13:00"  # ora solare: +1


def test_l_orario_mostrato_segue_il_fuso_scelto(monkeypatch):
    """Lo stesso istante, letto da due Paesi diversi. È il motivo per cui la
    regola dell'ora legale non si può scrivere a mano: fuori dall'Europa i
    cambi cadono in date diverse."""
    from shared import tempo
    istante = datetime(2026, 7, 15, 12, 0)      # UTC
    monkeypatch.setattr(tempo, "nome_fuso", lambda: "Europe/Rome")
    assert M.fmt_ts(istante) == "15/07 · 14:00"
    monkeypatch.setattr(tempo, "nome_fuso", lambda: "Europe/Dublin")
    assert M.fmt_ts(istante) == "15/07 · 13:00"
    monkeypatch.setattr(tempo, "nome_fuso", lambda: "UTC")
    assert M.fmt_ts(istante) == "15/07 · 12:00"


def test_fmt_ts_di_niente_e_niente():
    assert M.fmt_ts(None) is None


# ------------------------- parallelo e robustezza -------------------------
def test_un_fallimento_non_ferma_gli_altri():
    """Regola di robustezza del progetto: se una fonte cade, le altre passano."""
    def a_volte(x):
        if x == 3:
            raise RuntimeError("questo titolo no")
        return x * 2

    assert sorted(M._in_parallelo(a_volte, [1, 2, 3, 4])) == [2, 4, 8]
    assert M._in_parallelo(a_volte, []) == []


def test_stato_prezzi_dice_da_quanto_sono_fermi(monkeypatch):
    """`is_stale` esisteva e non la chiamava nessuno: l'app poteva mostrare per
    giorni prezzi vecchi con la sola data in piccolo a difenderla."""
    from datetime import timedelta
    monkeypatch.setattr(M, "last_update", lambda: M.utc_now() - timedelta(hours=30))
    s = M.stato_prezzi(max_age_min=360)
    assert s["vecchi"] is True
    assert s["giorni"] == 1
    assert 1795 <= s["minuti"] <= 1805

    monkeypatch.setattr(M, "last_update", lambda: M.utc_now() - timedelta(minutes=5))
    assert M.stato_prezzi()["vecchi"] is False

    monkeypatch.setattr(M, "last_update", lambda: None)
    assert M.stato_prezzi()["mai"] is True
