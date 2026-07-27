"""L'archivio giornaliero: la memoria che all'app mancava.

Tutto `insights.py` è costruito sul confrontare l'utente con sé stesso, ma il
proprio passato non veniva salvato da nessuna parte: si sovrascrivevano sempre
gli ultimi valori. Qui si difendono le due proprietà che rendono l'archivio
affidabile: una riga per giorno (mai doppioni) e nessun confronto promesso
quando i dati non ci sono.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
import shared.storico as S


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'test.db'}",
                           connect_args={"check_same_thread": False})
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    import shared.db as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(S, "SessionLocal", TestSession)
    yield TestSession


def _scrivi(giorno, **kw):
    """Scrive una giornata saltando la misura live (che vorrebbe i prezzi)."""
    valori = {"patrimonio": 0.0, "liquido": 0.0, "investito": 0.0, "versato": 0.0,
              "risultato_eur": None, "entrate_mese": 0.0, "uscite_mese": 0.0,
              "n_titoli": 0}
    valori.update(kw)
    with S.SessionLocal() as db:
        g = db.get(S.GiornoStorico, giorno) or S.GiornoStorico(data=giorno)
        for k, v in valori.items():
            setattr(g, k, v)
        db.add(g)
        db.commit()


def test_una_riga_per_giorno_anche_riscrivendola(monkeypatch):
    """`registra` viene chiamata a ogni apertura della dashboard: deve
    sovrascrivere il giorno, non accumulare righe."""
    monkeypatch.setattr(S, "_misura", lambda oggi: {
        "patrimonio": 100.0, "liquido": 10.0, "investito": 90.0, "versato": 95.0,
        "risultato_eur": -5.0, "entrate_mese": 0.0, "uscite_mese": 0.0, "n_titoli": 3})
    S.registra(date(2026, 7, 20))
    S.registra(date(2026, 7, 20))
    S.registra(date(2026, 7, 20))
    assert S.giorni_disponibili() == 1
    assert S.serie(30)[0]["patrimonio"] == 100.0


def test_se_i_dati_non_ci_sono_non_si_scrive_una_riga_a_zero(monkeypatch):
    """Una giornata a zero sarebbe indistinguibile da una giornata vera in cui
    hai perso tutto: meglio il buco."""
    def esplode(oggi):
        raise RuntimeError("prezzi non disponibili")
    monkeypatch.setattr(S, "_misura", esplode)
    assert S.registra(date(2026, 7, 20)) is None
    assert S.giorni_disponibili() == 0


def test_il_confronto_separa_i_tuoi_soldi_dal_mercato():
    """Se il portafoglio è salito di 110 € ma tu ne hai versati 100, il mercato
    ha fatto 10: è l'unica scomposizione che risponde a «come sto andando»."""
    oggi = date.today()
    _scrivi(oggi - timedelta(days=7), investito=200.0, versato=200.0, patrimonio=300.0)
    _scrivi(oggi, investito=310.0, versato=300.0, patrimonio=420.0)
    c = S.confronto(7)
    assert c["giorni"] == 7
    assert c["versato"] == 100.0
    assert c["mercato"] == 10.0
    assert c["patrimonio"] == 120.0


def test_niente_confronto_se_non_c_e_abbastanza_passato():
    """Con un giorno solo in archivio «rispetto a settimana scorsa» non esiste,
    e inventarlo sarebbe esattamente ciò che l'app non deve fare."""
    _scrivi(date.today(), investito=100.0)
    assert S.confronto(7) is None


def test_il_confronto_usa_la_riga_piu_vicina_e_dice_quanti_giorni_sono():
    """Se non apri l'app tutti i giorni, il confronto resta possibile ma deve
    dichiarare l'intervallo VERO, non fingere che siano 7 giorni."""
    oggi = date.today()
    _scrivi(oggi - timedelta(days=20), investito=100.0, versato=100.0)
    _scrivi(oggi - timedelta(days=11), investito=150.0, versato=140.0)
    _scrivi(oggi, investito=200.0, versato=180.0)
    c = S.confronto(7)
    assert c["giorni"] == 11              # la più recente fra le abbastanza vecchie
    assert c["mercato"] == 10.0           # +50 di valore, +40 versati


def test_la_serie_e_in_ordine_e_taglia_il_troppo_vecchio():
    oggi = date.today()
    for k in (100, 30, 2, 0):
        _scrivi(oggi - timedelta(days=k), investito=float(k))
    date_serie = [g["data"] for g in S.serie(60)]
    assert date_serie == sorted(date_serie)
    assert len(date_serie) == 3           # quella di 100 giorni fa resta fuori
