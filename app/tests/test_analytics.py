"""L'analisi del portafoglio: i numeri devono essere ONESTI.

Questo modulo prima non aveva test — ed è dove si annidavano gli errori più
insidiosi, perché sembravano innocui: una copertura gonfiata, una media parziale
spacciata per totale, lo stesso titolo contato due volte sotto due nomi, e una
performance di MERCATO messa dove l'utente legge il proprio guadagno.

Qui si difende, uno per uno, che ognuna di quelle cose non torni.
Nessuna rete: mercato e database sono finti.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import portfolio.analytics as A


class _Pos:
    def __init__(self, id, ticker, tipo, pct, qta=0.0, versato=0.0, nome=None):
        self.id, self.ticker, self.tipo, self.pct_target = id, ticker, tipo, pct
        self.quantita, self.versato_totale = qta, versato
        self.nome = nome or ticker
        self.nome_vista, self.is_fisso = self.nome, False


class _Q:
    def __init__(self, price_eur, ok=True, currency="USD"):
        self.price_eur, self.ok, self.currency = price_eur, ok, currency


def _patch_lt(monkeypatch, pos, quotes, fund):
    monkeypatch.setattr(A, "lista_posizioni", lambda: pos)
    monkeypatch.setattr(A.market, "quotes_map", lambda: quotes)
    monkeypatch.setattr(A.market, "get_fundamentals", lambda tk, tipo="": fund.get(tk))
    monkeypatch.setattr(A.market, "get_fundamentals_cached", lambda tk: fund.get(tk))


def _patch_an(monkeypatch, righe, fund, perf):
    totale = round(sum(r["valore"] for r in righe if r["valore"]), 2)
    monkeypatch.setattr(A, "vista_portafoglio", lambda: {
        "righe": righe, "totale": totale, "ha_totale": totale > 0})
    monkeypatch.setattr(A.market, "get_fundamentals", lambda tk, tipo="": fund.get(tk))
    monkeypatch.setattr(A.market, "get_perf_snapshot", lambda: perf)


# ---------------------- copertura settoriale onesta ----------------------
def test_coperto_conta_solo_chi_porta_settori(monkeypatch):
    """Un fondo di cui non si conosce la scomposizione NON copre nulla e non deve
    gonfiare il denominatore (era il caso reale di GIFL: +4 punti fantasma)."""
    pos = [_Pos(1, "AAA", "ETF", 50, qta=1, versato=50),
           _Pos(2, "GIFL", "ETF", 50, qta=1, versato=50)]
    quotes = {"AAA": _Q(10.0, currency="EUR"), "GIFL": _Q(10.0, currency="EUR")}
    fund = {"AAA": {"sectors": [{"name": "Technology", "weight": 100.0}]},
            "GIFL": {"name": "un fondo", "sectors": []}}   # dati presenti, settori no
    _patch_lt(monkeypatch, pos, quotes, fund)
    lt = A.look_through()
    assert lt["tech"] == 100.0            # 100% del portafoglio COPERTO, non del totale
    assert lt["coperto_pct"] == 50.0      # GIFL non copre: solo AAA (10€ su 20€)


def test_i_settori_sommano_a_cento(monkeypatch):
    pos = [_Pos(1, "AAA", "ETF", 50, qta=1, versato=50),
           _Pos(2, "BBB", "Azione", 50, qta=1, versato=50)]
    quotes = {"AAA": _Q(10.0, currency="EUR"), "BBB": _Q(10.0)}
    fund = {"AAA": {"sectors": [{"name": "Technology", "weight": 60.0},
                                {"name": "Energy", "weight": 40.0}]},
            "BBB": {"sector": "Energy"}}
    _patch_lt(monkeypatch, pos, quotes, fund)
    lt = A.look_through()
    assert round(sum(s["pct"] for s in lt["settori"]), 1) == 100.0


# ---------------------- una base sola: valore o target ----------------------
def test_base_valore_quando_ci_sono_prezzi(monkeypatch):
    pos = [_Pos(1, "AAA", "ETF", 50, qta=2, versato=0),      # valore 20
           _Pos(2, "BBB", "Azione", 50, qta=1, versato=0)]   # valore 10
    quotes = {"AAA": _Q(10.0, currency="EUR"), "BBB": _Q(10.0)}
    fund = {"AAA": {"sectors": [{"name": "Technology", "weight": 100.0}]},
            "BBB": {"sector": "Energy"}}
    _patch_lt(monkeypatch, pos, quotes, fund)
    lt = A.look_through()
    assert lt["usa_valori"] is True
    assert lt["tech"] == 66.7             # 20€ tech su 30€, non 50% dei pesi target


def test_base_target_senza_prezzi(monkeypatch):
    pos = [_Pos(1, "AAA", "ETF", 60, qta=1, versato=0),
           _Pos(2, "BBB", "Azione", 40, qta=1, versato=0)]
    fund = {"AAA": {"sectors": [{"name": "Technology", "weight": 100.0}]},
            "BBB": {"sector": "Energy"}}
    _patch_lt(monkeypatch, pos, {}, fund)   # nessun prezzo disponibile
    lt = A.look_through()
    assert lt["usa_valori"] is False
    assert lt["tech"] == 60.0             # ripiega sulla % target


# ---------------------- look-through: niente doppioni ----------------------
def test_look_through_unisce_lo_stesso_titolo(monkeypatch):
    """La NVIDIA dentro un ETF e la NVIDIA diretta sono lo stesso titolo: una
    riga sola. Sdoppiarla nascondeva proprio ciò che il look-through deve svelare."""
    etf = _Pos(1, "CSPX", "ETF", 50, qta=1, versato=50)
    nvda = _Pos(2, "NVDA", "Azione", 50, qta=1, versato=50, nome="NVIDIA")
    righe = [{"p": etf, "valore": 50.0, "q": _Q(1, True, "EUR")},
             {"p": nvda, "valore": 50.0, "q": _Q(1, True, "USD")}]
    fund = {"CSPX": {"holdings": [{"symbol": "NVDA", "name": "NVIDIA Corp", "weight": 10.0}]},
            "NVDA": {"sector": "Technology"}}
    _patch_an(monkeypatch, righe, fund, perf={})
    an = A.analisi_completa()
    nv = [r for r in an["look"] if "NVIDIA" in r["n"]]
    assert len(nv) == 1                   # NON due righe
    assert nv[0]["w"] == 55.0             # 50% diretta + 5% dentro l'ETF
    assert nv[0]["n"] == "NVIDIA"         # il tuo nome vince su "NVIDIA Corp"


def test_classi_azionarie_restano_distinte(monkeypatch):
    """GOOG e GOOGL si somigliano ma sono strumenti diversi: NON vanno fusi."""
    etf = _Pos(1, "CSPX", "ETF", 100, qta=1, versato=100)
    righe = [{"p": etf, "valore": 100.0, "q": _Q(1, True, "EUR")}]
    fund = {"CSPX": {"holdings": [
        {"symbol": "GOOGL", "name": "Alphabet Inc Class A", "weight": 5.0},
        {"symbol": "GOOG", "name": "Alphabet Inc Class C", "weight": 4.0}]}}
    _patch_an(monkeypatch, righe, fund, perf={})
    an = A.analisi_completa()
    names = [r["n"] for r in an["look"]]
    assert any("Class A" in n for n in names)
    assert any("Class C" in n for n in names)


# ---------------------- il risultato è dell'utente, non del mercato ----------------------
def test_risultato_reale_separato_dalla_perf_di_mercato(monkeypatch):
    """SNDK ha fatto +3281% sul MERCATO, ma l'utente ci ha messo 100€ e ne valgono
    97: il suo risultato è −3%, e i due numeri non devono mai confondersi."""
    p = _Pos(1, "SNDK", "Azione", 100, qta=1, versato=100, nome="SanDisk")
    righe = [{"p": p, "valore": 97.0, "q": _Q(97, True, "USD")}]
    _patch_an(monkeypatch, righe, fund={"SNDK": {}}, perf={"SNDK": 3281.0})
    an = A.analisi_completa()
    assert an["risultato_eur"] == -3.0
    assert an["risultato_pct"] == -3.0
    assert an["perf12m"] == 3281.0        # la storia di mercato resta, ma a parte
    assert an["versato_totale"] == 100.0


# ---------------------- dividendi: copertura dichiarata e coerente ----------------------
def test_dividendi_copertura_e_coerenza(monkeypatch):
    """Il rendimento medio e il reddito in euro devono poggiare sulla STESSA base
    (i soli titoli che pagano), e quella base va dichiarata: gli ETF non pubblicano
    il dato, quindi 1,08% non è il rendimento dell'intero portafoglio."""
    payer = _Pos(1, "KO", "Azione", 50, qta=1, versato=50, nome="Coca-Cola")
    etf = _Pos(2, "IWDA", "ETF", 50, qta=1, versato=50)
    righe = [{"p": payer, "valore": 50.0, "q": _Q(50, True, "USD")},
             {"p": etf, "valore": 50.0, "q": _Q(50, True, "EUR")}]
    fund = {"KO": {"div_yield": 0.03}, "IWDA": {}}   # l'ETF non dichiara il dividendo
    _patch_an(monkeypatch, righe, fund, perf={})
    an = A.analisi_completa()
    assert an["div_coverage"] == 50       # solo KO paga: metà del valore
    assert an["div_yield"] == 3.0
    assert an["div_income"] == 1.5
    # coerenza: reddito == rendimento dei paganti × valore dei paganti
    assert round(an["div_yield"] / 100 * 50, 2) == an["div_income"]


# ---------------------- prezzo mancante: fuori, e dichiarato ----------------------
def test_posizione_senza_prezzo_esclusa_e_dichiarata(monkeypatch):
    """Un titolo con quantità ma prezzo fallito non deve sparire in silenzio:
    resta fuori dal calcolo E finisce nell'elenco degli esclusi."""
    ok = _Pos(1, "AAPL", "Azione", 50, qta=1, versato=50, nome="Apple")
    ko = _Pos(2, "MSFT", "Azione", 50, qta=1, versato=50, nome="Microsoft")
    righe = [{"p": ok, "valore": 50.0, "q": _Q(50, True, "USD")},
             {"p": ko, "valore": None, "q": _Q(50, False, "USD")}]   # prezzo fallito
    _patch_an(monkeypatch, righe, fund={}, perf={})
    an = A.analisi_completa()
    assert "MSFT" in an["esclusi"]
    assert an["n_titoli"] == 1            # MSFT non conta nei calcoli
    assert an["versato_totale"] == 100.0  # ...ma il suo versato sì: è denaro reale


# ---------------------- rischio: in euro, e pesato sul valore ----------------------
def _serie_da_rendimenti(rendimenti, base=100.0):
    """Chiusure che producono esattamente quei rendimenti settimanali."""
    closes, x = [base], base
    for r in rendimenti:
        x *= (1 + r)
        closes.append(x)
    return closes


def _patch_risk(monkeypatch, pos, quotes, storie):
    monkeypatch.setattr(A, "lista_posizioni", lambda: pos)
    monkeypatch.setattr(A.market, "quotes_map", lambda: quotes)
    monkeypatch.setattr(A.market, "_yahoo_symbol", lambda s: s)
    monkeypatch.setattr(A.market, "history_closes",
                        lambda sym, rng="1y", interval="1wk": storie.get(sym, []))
    salvati = {}
    monkeypatch.setattr(A.settings_store, "set_setting",
                        lambda k, v: salvati.__setitem__(k, v))
    return salvati


def test_il_rischio_e_calcolato_in_euro_non_in_valuta_di_quotazione(monkeypatch):
    """Un titolo in dollari che sale del 2% mentre il dollaro perde il 3% per un
    portafoglio in euro è SCESO. Prima i rendimenti restavano in valuta di
    quotazione e venivano confrontati con un benchmark in euro: i due lati della
    misura erano in monete diverse."""
    piatto = [0.0] * 40
    su = [0.02] * 40                       # il titolo sale ogni settimana
    cambio = [0.03] * 40                   # ...ma l'euro si rafforza di più
    pos = [_Pos(1, "USA", "Azione", 100, qta=1, versato=100)]
    quotes = {"USA": _Q(10.0, currency="USD")}
    storie = {"USA": _serie_da_rendimenti(su),
              "EURUSD=X": _serie_da_rendimenti(cambio),
              "IWDA": _serie_da_rendimenti(piatto)}
    _patch_risk(monkeypatch, pos, quotes, storie)
    snap = A.compute_risk()
    # in euro il rendimento è (1,02/1,03 - 1) ≈ -0,97% a settimana: negativo
    assert snap is not None
    assert snap["ann"] < 0, "il cambio non è entrato nel calcolo"
    assert snap["in_euro"] is True


def test_senza_lo_storico_del_cambio_il_titolo_resta_fuori(monkeypatch):
    """Meglio un titolo escluso e dichiarato che uno convertito a occhio."""
    pos = [_Pos(1, "USA", "Azione", 50, qta=1, versato=50),
           _Pos(2, "EUR", "Azione", 50, qta=1, versato=50)]
    quotes = {"USA": _Q(10.0, currency="USD"), "EUR": _Q(10.0, currency="EUR")}
    storie = {"USA": _serie_da_rendimenti([0.01] * 40),
              "EUR": _serie_da_rendimenti([0.01] * 40),
              "IWDA": _serie_da_rendimenti([0.01] * 40)}   # EURUSD=X assente
    _patch_risk(monkeypatch, pos, quotes, storie)
    snap = A.compute_risk()
    assert snap["n"] == 1
    assert "USA" in snap["esclusi"]


def test_il_rischio_pesa_sul_valore_reale_e_lo_dichiara(monkeypatch):
    pos = [_Pos(1, "A", "Azione", 50, qta=9, versato=50),
           _Pos(2, "B", "Azione", 50, qta=1, versato=50)]
    quotes = {"A": _Q(10.0, currency="EUR"), "B": _Q(10.0, currency="EUR")}
    storie = {"A": _serie_da_rendimenti([0.05, -0.05] * 20),
              "B": _serie_da_rendimenti([0.0] * 40),
              "IWDA": _serie_da_rendimenti([0.0] * 40)}
    _patch_risk(monkeypatch, pos, quotes, storie)
    snap = A.compute_risk()
    assert snap["base"] == "valore"
    # A pesa 90 su 100 (non 50/50 come col peso target): la volatilità del
    # portafoglio è quasi tutta la sua
    assert snap["vol"] > 20, "col peso sul valore A domina, e A oscilla molto"


def test_uno_snapshot_di_un_metodo_vecchio_non_viene_mostrato(monkeypatch):
    """Mostrarne metà (senza VaR e R², con pesi target) si legge come se quelle
    metriche non esistessero: peggio che non mostrarlo."""
    import json
    vecchio = json.dumps({"vol": 12.9, "beta": 1.18})       # niente 'versione'
    monkeypatch.setattr(A.settings_store, "get_setting", lambda k, d="": vecchio)
    assert A.get_cached_risk() is None
    assert A.risk_scaduto() is True

    nuovo = json.dumps({"vol": 13.7, "versione": A.RISK_VERSIONE})
    monkeypatch.setattr(A.settings_store, "get_setting", lambda k, d="": nuovo)
    assert A.get_cached_risk()["vol"] == 13.7
    assert A.risk_scaduto() is False


def test_ter_solo_sugli_etf_che_lo_pubblicano(monkeypatch):
    etf1 = _Pos(1, "AAA", "ETF", 50, qta=1, versato=50)
    etf2 = _Pos(2, "BBB", "ETF", 50, qta=1, versato=50)
    righe = [{"p": etf1, "valore": 50.0, "q": _Q(50, True, "EUR")},
             {"p": etf2, "valore": 50.0, "q": _Q(50, True, "EUR")}]
    fund = {"AAA": {"expense_ratio": 0.002}, "BBB": {}}   # solo AAA pubblica il TER
    _patch_an(monkeypatch, righe, fund, perf={})
    an = A.analisi_completa()
    assert an["ter_n_etf"] == 2 and an["ter_n_con"] == 1
    assert an["ter"] == 0.2               # 0,2% (solo AAA), non diluito su entrambi
