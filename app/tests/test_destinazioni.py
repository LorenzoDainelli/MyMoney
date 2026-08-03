"""«Dove è finito il mese», la spesa più grossa, le categorie già viste.

Il riquadro delle destinazioni è l'unico punto dell'app in cui il PAC sta
accanto alle spese, e per questo è anche l'unico che può contare due volte gli
stessi soldi: il versamento PAC è un TRASFERIMENTO, non un'uscita. Se un giorno
diventasse un'uscita, speso + investito + rimasto smetterebbe di fare le
entrate — ed è esattamente quello che questi test guardano.

Niente rete.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
from finance.models import (Wallet, Category, Transaction,
                            TIPO_USCITA, TIPO_ENTRATA)
import shared.settings_store  # noqa: F401
import shared.sync            # noqa: F401
import finance.service as fin
from motore import engine_di_prova


ANNO, MESE = 2026, 7


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    import shared.db as db_mod
    for mod in (db_mod, fin):
        monkeypatch.setattr(mod, "SessionLocal", TestSession)
    with TestSession() as db:
        db.add(Wallet(id=1, nome="Contanti", tipo="contanti", saldo_iniziale=0.0))
        db.add(Category(id=1, nome="Regali"))
        db.add(Category(id=2, nome="Trasporti"))
        db.commit()
    yield TestSession


def _mov(db, tipo, giorno, importo, cat=None, descr=""):
    db.add(Transaction(tipo=tipo, data=datetime(ANNO, MESE, giorno, 12, 0),
                       importo=importo, wallet_id=1, category_id=cat,
                       descrizione=descr))


def _versamenti(monkeypatch, righe):
    """Sostituisce lo storico dei versamenti PAC (import pigro dentro la funzione)."""
    import portfolio.versamenti as v
    monkeypatch.setattr(v, "lista", lambda: righe)


# ------------------------------ destinazioni ------------------------------
def test_senza_entrate_il_riquadro_non_compare(test_db):
    """Una torta con una fetta sola non spiega niente: meglio assente."""
    with test_db() as db:
        _mov(db, TIPO_USCITA, 3, 20.0, cat=1)
        db.commit()
    assert fin.destinazioni_mese(ANNO, MESE) is None


def test_le_tre_destinazioni_fanno_le_entrate(monkeypatch, test_db):
    with test_db() as db:
        _mov(db, TIPO_ENTRATA, 1, 1000.0)
        _mov(db, TIPO_USCITA, 5, 300.0, cat=1)
        db.commit()
    _versamenti(monkeypatch, [{"importo": 100.0, "data": date(ANNO, MESE, 16)}])
    d = fin.destinazioni_mese(ANNO, MESE)
    val = {v["key"]: v["val"] for v in d["voci"]}
    assert val == {"speso": 300.0, "investito": 100.0, "rimasto": 600.0}
    assert round(sum(val.values()), 2) == d["entrate"] == 1000.0
    assert round(sum(v["pct"] for v in d["voci"])) == 100
    assert d["in_rosso"] is False


def test_il_pac_non_e_gia_dentro_le_uscite(monkeypatch, test_db):
    """Il versamento è un trasferimento: se finisse anche in «speso» il totale
    delle tre voci supererebbe le entrate."""
    with test_db() as db:
        _mov(db, TIPO_ENTRATA, 1, 500.0)
        db.commit()
    _versamenti(monkeypatch, [{"importo": 100.0, "data": date(ANNO, MESE, 16)}])
    d = fin.destinazioni_mese(ANNO, MESE)
    assert dict((v["key"], v["val"]) for v in d["voci"])["speso"] == 0.0
    assert round(sum(v["val"] for v in d["voci"]), 2) == 500.0


def test_solo_i_versamenti_del_mese(monkeypatch, test_db):
    with test_db() as db:
        _mov(db, TIPO_ENTRATA, 1, 1000.0)
        db.commit()
    _versamenti(monkeypatch, [
        {"importo": 100.0, "data": date(ANNO, MESE, 16)},
        {"importo": 100.0, "data": date(ANNO, MESE - 1, 16)},   # giugno
        {"importo": 100.0, "data": date(ANNO, MESE + 1, 16)},   # agosto
    ])
    d = fin.destinazioni_mese(ANNO, MESE)
    assert dict((v["key"], v["val"]) for v in d["voci"])["investito"] == 100.0


def test_se_esce_piu_di_quanto_entra_lo_dice(monkeypatch, test_db):
    """Niente percentuali sopra il 100%: la base diventa il totale uscito e il
    rimasto va in negativo, dichiarato."""
    with test_db() as db:
        _mov(db, TIPO_ENTRATA, 1, 100.0)
        _mov(db, TIPO_USCITA, 5, 250.0, cat=1)
        db.commit()
    _versamenti(monkeypatch, [{"importo": 100.0, "data": date(ANNO, MESE, 16)}])
    d = fin.destinazioni_mese(ANNO, MESE)
    val = {v["key"]: v["val"] for v in d["voci"]}
    assert val["rimasto"] == -250.0
    assert d["in_rosso"] is True
    assert sum(v["pct"] for v in d["voci"]) <= 100.0


def test_senza_pac_restano_speso_e_rimasto(monkeypatch, test_db):
    """Il PAC è un extra: se il Portafoglio non risponde il riquadro regge."""
    with test_db() as db:
        _mov(db, TIPO_ENTRATA, 1, 1000.0)
        _mov(db, TIPO_USCITA, 5, 300.0, cat=1)
        db.commit()
    import portfolio.versamenti as v
    monkeypatch.setattr(v, "lista", lambda: (_ for _ in ()).throw(RuntimeError("giù")))
    d = fin.destinazioni_mese(ANNO, MESE)
    val = {x["key"]: x["val"] for x in d["voci"]}
    assert val == {"speso": 300.0, "investito": 0.0, "rimasto": 700.0}


# ------------------------------ spesa più grossa ------------------------------
def test_la_spesa_piu_grossa_del_mese(test_db):
    with test_db() as db:
        _mov(db, TIPO_USCITA, 3, 20.0, cat=2)
        _mov(db, TIPO_USCITA, 21, 75.0, cat=1, descr="bracciale")
        _mov(db, TIPO_ENTRATA, 1, 9999.0)      # un'entrata non è una spesa
        db.commit()
    top = fin.spesa_top(ANNO, MESE)
    assert top["importo"] == 75.0
    assert top["data"].day == 21
    assert top["categoria"] == "Regali"


def test_senza_uscite_non_c_e_una_spesa_piu_grossa(test_db):
    assert fin.spesa_top(ANNO, MESE) is None


# ------------------------------ categorie del mese ------------------------------
def test_quante_volte_e_quanto_per_categoria(test_db):
    with test_db() as db:
        _mov(db, TIPO_USCITA, 3, 10.0, cat=2)
        _mov(db, TIPO_USCITA, 9, 15.0, cat=2)
        _mov(db, TIPO_USCITA, 21, 75.0, cat=1)
        db.commit()
    m = fin.uscite_per_categoria_mese(ANNO, MESE)
    assert m["trasporti"] == {"n": 2, "tot": 25.0}
    assert m["regali"] == {"n": 1, "tot": 75.0}
    assert "benzina" not in m          # mai vista = «prima volta» nel riquadro
