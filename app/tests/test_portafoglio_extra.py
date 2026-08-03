"""Le tre aggiunte al portafoglio: allineamento al broker, PMC, promemoria PAC.

Il filo comune è lo stesso di tutto il progetto: un numero può essere corretto
e lo stesso ingannevole se non si dice da dove viene. L'allineamento a TR
riscrive dei valori — quindi va dichiarato e va difeso da un errore di
battitura; il PMC è esatto solo dove il prezzo era noto; il promemoria non deve
inventare un'abitudine che non esiste.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
from portfolio.models import Position, Versamento, VersamentoRiga
import portfolio.service as pf
import portfolio.versamenti as versamenti
import shared.settings_store as store
from motore import engine_di_prova


class _Q:
    def __init__(self, price_eur, ok=True, currency="EUR"):
        self.price_eur, self.ok, self.currency = price_eur, ok, currency
        self.price = price_eur


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    import shared.db as db_mod
    for mod in (db_mod, pf, versamenti, store):
        monkeypatch.setattr(mod, "SessionLocal", TestSession, raising=False)
    monkeypatch.setattr(pf.market, "last_update", lambda: None)
    monkeypatch.setattr(pf.market, "stato_prezzi", lambda *a, **k: {"mai": True})
    yield TestSession


def _seed(Session, prezzi=(10.0, 10.0)):
    with Session() as db:
        db.add_all([
            Position(nome="Alpha", ticker="A", pct_target=50.0, quantita=5.0,
                     versato_totale=50.0, ordine=0),
            Position(nome="Beta", ticker="B", pct_target=50.0, quantita=5.0,
                     versato_totale=50.0, ordine=1),
        ])
        db.commit()


# ------------------------- allineamento a Trade Republic -------------------------
def test_senza_allineamento_il_totale_e_la_stima(test_db, monkeypatch):
    _seed(test_db)
    monkeypatch.setattr(pf.market, "quotes_map", lambda: {"A": _Q(10.0), "B": _Q(10.0)})
    v = pf.vista_portafoglio()
    assert v["totale"] == 100.0
    assert v["tr"] is None


def test_allineare_fissa_il_totale_e_riscala_le_stime(test_db, monkeypatch):
    """Il punto dell'allineamento: il totale diventa quello letto sul broker, e
    le stime per titolo si spostano tutte dello stesso fattore."""
    _seed(test_db)
    monkeypatch.setattr(pf.market, "quotes_map", lambda: {"A": _Q(10.0), "B": _Q(10.0)})
    pf.salva_allineamento_tr(80.0, date.today())
    v = pf.vista_portafoglio()
    assert v["totale"] == 80.0
    assert [r["valore"] for r in v["righe"]] == [40.0, 40.0]
    # la stima grezza resta consultabile: l'allineamento non cancella il dato
    assert [r["valore_stimato"] for r in v["righe"]] == [50.0, 50.0]
    assert v["tr"]["scarto_eur"] == -20.0
    assert v["tr"]["scarto_pct"] == -20.0


def test_il_versato_non_si_tocca_mai(test_db, monkeypatch):
    """Il valore è una stima, il versato è un fatto: riscalare anche quello
    falserebbe il risultato dell'utente, cioè proprio il numero da proteggere."""
    _seed(test_db)
    monkeypatch.setattr(pf.market, "quotes_map", lambda: {"A": _Q(10.0), "B": _Q(10.0)})
    pf.salva_allineamento_tr(80.0, date.today())
    v = pf.vista_portafoglio()
    assert sum(r["p"].versato_totale for r in v["righe"]) == 100.0


def test_un_totale_assurdo_viene_mostrato_ma_non_applicato(test_db, monkeypatch):
    """Uno scarto di 40× è quasi sempre una virgola sbagliata, non il mercato."""
    _seed(test_db)
    monkeypatch.setattr(pf.market, "quotes_map", lambda: {"A": _Q(10.0), "B": _Q(10.0)})
    pf.salva_allineamento_tr(4000.0, date.today())
    v = pf.vista_portafoglio()
    assert v["tr"]["assurdo"] is True
    assert v["totale"] == 100.0           # NON applicato
    assert [r["valore"] for r in v["righe"]] == [50.0, 50.0]


def test_un_allineamento_vecchio_viene_segnalato(test_db, monkeypatch):
    _seed(test_db)
    monkeypatch.setattr(pf.market, "quotes_map", lambda: {"A": _Q(10.0), "B": _Q(10.0)})
    pf.salva_allineamento_tr(95.0, date.today() - timedelta(days=30))
    v = pf.vista_portafoglio()
    assert v["tr"]["vecchio"] is True
    assert v["tr"]["giorni"] == 30
    assert v["totale"] == 95.0            # si applica lo stesso, ma dichiarandolo


def test_togliere_l_allineamento_riporta_alla_stima(test_db, monkeypatch):
    _seed(test_db)
    monkeypatch.setattr(pf.market, "quotes_map", lambda: {"A": _Q(10.0), "B": _Q(10.0)})
    pf.salva_allineamento_tr(80.0, date.today())
    pf.salva_allineamento_tr(0)
    v = pf.vista_portafoglio()
    assert v["tr"] is None and v["totale"] == 100.0


# ------------------------- prezzo medio di carico -------------------------
def test_il_pmc_media_solo_dove_il_prezzo_era_noto(test_db):
    """Una riga senza quote (prezzo n/d quel giorno) porta euro ma non quote:
    metterla nella media abbasserebbe un PMC che non è mai esistito."""
    with test_db() as db:
        db.add(Position(nome="Alpha", ticker="A", pct_target=100.0, ordine=0))
        db.commit()
        pid = db.execute(select(Position)).scalars().first().id
        db.add(Versamento(data=date(2026, 7, 16), importo=100.0))
        db.commit()
        vid = db.execute(select(Versamento)).scalars().first().id
        db.add_all([
            VersamentoRiga(versamento_id=vid, position_id=pid, euro=50.0, qta=5.0),
            VersamentoRiga(versamento_id=vid, position_id=pid, euro=30.0, qta=2.0),
            VersamentoRiga(versamento_id=vid, position_id=pid, euro=20.0, qta=None),
        ])
        db.commit()
    m = pf.pmc_map()[pid]
    assert m["qta"] == 7.0
    assert m["euro"] == 80.0              # i 20 € senza quote restano fuori
    assert m["pmc"] == round(80.0 / 7.0, 4)


def test_senza_versamenti_non_c_e_pmc(test_db):
    _seed(test_db)
    assert pf.pmc_map() == {}


# ------------------------- promemoria PAC -------------------------
def _versamento(Session, giorno, importo=100.0):
    with Session() as db:
        db.add(Versamento(data=giorno, importo=importo))
        db.commit()


def test_niente_promemoria_senza_uno_storico(test_db):
    """Senza versamenti non esiste nessuna abitudine da ricordare."""
    assert versamenti.promemoria(date(2026, 7, 20)) is None


def test_il_giorno_lo_decide_la_tua_storia_non_il_codice(test_db):
    """Se sposti il PAC, il promemoria si sposta con te."""
    for m in (4, 5, 6):
        _versamento(test_db, date(2026, m, 5))
    p = versamenti.promemoria(date(2026, 7, 6))
    assert p is not None and p["giorno"] == 5 and p["in_ritardo"] is True


def test_nessun_promemoria_se_il_mese_e_gia_registrato(test_db):
    _versamento(test_db, date(2026, 6, 16))
    _versamento(test_db, date(2026, 7, 16))
    assert versamenti.promemoria(date(2026, 7, 25)) is None


def test_nessun_promemoria_se_e_ancora_presto(test_db):
    """A inizio mese non è un promemoria, è rumore."""
    _versamento(test_db, date(2026, 6, 16))
    assert versamenti.promemoria(date(2026, 7, 3)) is None
    assert versamenti.promemoria(date(2026, 7, 14)) is not None   # due giorni prima: sì
