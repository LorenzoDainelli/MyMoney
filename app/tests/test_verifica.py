"""La verifica delle stime del monitor: il conto deve essere onesto anche
quando il risultato è brutto.

Il rischio di una pagina così è opposto a quello solito: non che inventi, ma
che si compiaccia. Basta scartare in silenzio i casi difficili, o dimenticare
il metro di paragone, per far sembrare bravo un oracolo che tira a indovinare.
Qui si difendono le tre cose che lo impediscono: i non verificabili si contano
a parte, la baseline si calcola, e la soglia di direzione è esplicita.
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import news.verifica as V


def _serie(prezzi, dal=date(2026, 6, 1)):
    """Serie giornaliera finta: [(data, chiusura), ...] su giorni consecutivi."""
    from datetime import timedelta
    return [(dal + timedelta(days=i), p) for i, p in enumerate(prezzi)]


# ------------------------- la soglia di direzione -------------------------
def test_la_soglia_decide_cosa_e_una_direzione():
    """Senza soglia qualunque mezzo punto sarebbe «positivo»: la banda è una
    scelta, e infatti la pagina la dichiara."""
    assert V._direzione(5.0) == "positivo"
    assert V._direzione(-5.0) == "negativo"
    assert V._direzione(1.0) == "neutro"
    assert V._direzione(-1.4) == "neutro"
    assert V._direzione(V.SOGLIA_PCT) == "neutro"       # sul bordo: non basta


# ------------------------- il singolo esito -------------------------
def test_una_stima_azzeccata():
    serie = _serie([100.0, 101, 102, 103, 104, 110])    # +10% dopo 5 giorni
    r = V._esito(serie, date(2026, 6, 1), "positivo")
    assert r["stato"] == "verificata"
    assert r["var_pct"] == 10.0
    assert r["azzeccata"] is True


def test_una_stima_sbagliata():
    serie = _serie([100.0, 99, 98, 97, 96, 90])
    r = V._esito(serie, date(2026, 6, 1), "positivo")
    assert r["reale"] == "negativo"
    assert r["azzeccata"] is False


def test_una_notizia_troppo_recente_resta_IN_ATTESA():
    """Il caso più pericoloso: scartarlo in silenzio gonfierebbe la percentuale
    di successo, perché sparirebbero solo le stime ancora non maturate."""
    serie = _serie([100.0, 101, 102])                   # non ci sono 5 giorni dopo
    assert V._esito(serie, date(2026, 6, 1), "positivo")["stato"] == "in_attesa"


def test_una_notizia_prima_dei_prezzi_non_e_verificabile():
    serie = _serie([100.0] * 10, dal=date(2026, 6, 10))
    assert V._esito(serie, date(2026, 6, 1), "positivo")["stato"] == "senza_dati"


def test_il_prezzo_di_riferimento_e_l_ultima_chiusura_utile():
    """Notizia di domenica: il riferimento è il venerdì, non il lunedì dopo —
    altrimenti si misurerebbe a reazione già avvenuta."""
    serie = _serie([100.0, 200, 300, 400, 500, 600, 700])
    r = V._esito(serie, date(2026, 6, 2), "positivo")   # indice 1 → p0=200
    assert r["dal"] == date(2026, 6, 2)
    assert r["var_pct"] == round((700 / 200 - 1) * 100, 2)


# ------------------------- il conto complessivo -------------------------
def _finto_monitor(monkeypatch, items, serie):
    import news.reader as reader
    monkeypatch.setattr(reader, "_load_items", lambda: items)
    monkeypatch.setattr(V, "_serie_per_ticker", lambda tk: serie)
    salvati = {}
    monkeypatch.setattr(V.settings_store, "set_setting",
                        lambda k, v: salvati.__setitem__(k, v))
    return salvati


def test_la_baseline_smaschera_un_oracolo_che_non_sa_niente(monkeypatch):
    """Se in quel periodo sale tutto, «positivo» ci azzecca da solo. Il numero
    che conta non è quante ne prende, ma quante ne prende IN PIÙ."""
    items = [{"ticker": "AAA", "data": "2026-06-01", "confidenza": "alta",
              "impatto": {"breve": "positivo"}, "titolo": f"n{i}"} for i in range(4)]
    serie = {"AAA": _serie([100.0, 101, 102, 103, 104, 120])}   # sale sempre
    _finto_monitor(monkeypatch, items, serie)
    d = V.calcola()
    assert d["totale"] == 4 and d["azzeccate"] == 4
    assert d["pct"] == 100.0
    # ...ma dicendo sempre «positivo» avresti fatto altrettanto: nessun merito
    assert d["baseline"] == 100.0
    assert d["baseline_esito"] == "positivo"


def test_i_non_verificabili_si_contano_a_parte(monkeypatch):
    items = [
        {"ticker": "AAA", "data": "2026-06-01", "impatto": {"breve": "positivo"}},
        {"ticker": "AAA", "data": "2026-06-06", "impatto": {"breve": "positivo"}},
        {"ticker": "ZZZ", "data": "2026-06-01", "impatto": {"breve": "positivo"}},
    ]
    serie = {"AAA": _serie([100.0, 101, 102, 103, 104, 120])}
    _finto_monitor(monkeypatch, items, serie)
    d = V.calcola()
    assert d["totale"] == 1          # solo la prima è maturata
    assert d["in_attesa"] == 1       # la seconda è troppo recente
    assert d["senza_dati"] == 1      # ZZZ non ha prezzi
    # e i non verificabili NON entrano nella percentuale
    assert d["pct"] == 100.0


def test_la_confidenza_viene_spacchettata(monkeypatch):
    """La domanda vera non è «quante ne prende» ma «quando dice alta, ci prende
    di più». Se le tre righe sono uguali, quella parola non sta dicendo niente."""
    su = _serie([100.0, 101, 102, 103, 104, 120])
    giu = _serie([100.0, 99, 98, 97, 96, 80])
    items = [
        {"ticker": "SU", "data": "2026-06-01", "confidenza": "alta",
         "impatto": {"breve": "positivo"}},
        {"ticker": "GIU", "data": "2026-06-01", "confidenza": "bassa",
         "impatto": {"breve": "positivo"}},
    ]
    _finto_monitor(monkeypatch, items, {"SU": su, "GIU": giu})
    d = V.calcola()
    assert d["per_confidenza"]["alta"] == {"tot": 1, "ok": 1, "pct": 100.0}
    assert d["per_confidenza"]["bassa"] == {"tot": 1, "ok": 0, "pct": 0.0}
    assert d["per_confidenza"]["media"]["pct"] is None      # nessuna: non zero


def test_senza_stime_verificabili_niente_percentuale_inventata(monkeypatch):
    _finto_monitor(monkeypatch, [], {})
    d = V.calcola()
    assert d["totale"] == 0
    assert d["pct"] is None and d["baseline"] is None


def test_le_stime_senza_ticker_o_data_vengono_ignorate(monkeypatch):
    items = [{"ticker": "", "data": "2026-06-01", "impatto": {"breve": "positivo"}},
             {"ticker": "AAA", "data": "", "impatto": {"breve": "positivo"}}]
    _finto_monitor(monkeypatch, items, {"AAA": _serie([100.0] * 8)})
    d = V.calcola()
    assert d["totale"] == 0 and d["senza_dati"] == 0 and d["in_attesa"] == 0
