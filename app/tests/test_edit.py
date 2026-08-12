"""Test della MODIFICA dei movimenti (redesign-ui).

- movimento normale: aggiorna_movimento cambia i campi IN-PLACE (stesso record,
  uid invariato, rev++), i saldi riflettono il nuovo importo;
- partita di giro: aggiorna_giro sostituisce TUTTE le gambe mantenendo il giro_id
  (le vecchie diventano tombstone), i saldi restano corretti;
- dati_modifica ricostruisce i dati per il form (giro scomposto in spese+rientri).
"""
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
from shared import tempo          # che ora è: il fuso scelto, non l'orologio del PC
from finance.models import Wallet, Transaction, TIPO_USCITA, TIPO_GIRO
import finance.service as service
import shared.backup as backup_mod
from motore import engine_di_prova


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    engine = engine_di_prova(db_path)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    import shared.db as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(service, "SessionLocal", TestSession)
    monkeypatch.setattr(backup_mod, "SessionLocal", TestSession)
    yield {"Session": TestSession}


def _wallet(Session, nome="W1", saldo=100.0):
    with Session() as db:
        w = Wallet(nome=nome, tipo="conto", saldo_iniziale=saldo)
        db.add(w)
        db.commit()
        return w.id


def _saldo(Session, wid):
    with Session() as db:
        return round(service._saldi_map(db).get(wid, 0.0), 2)


# ── movimento normale ───────────────────────────────────────────────────────

def test_aggiorna_movimento_inplace(test_db):
    Session = test_db["Session"]
    wid = _wallet(Session, saldo=100.0)
    service.crea_movimento(TIPO_USCITA, tempo.adesso(), 30.0, wid,
                           categoria_nome="Spesa", descrizione="vecchia")
    with Session() as db:
        t = db.execute(select(Transaction)).scalar_one()
        tid, uid_before = t.id, t.uid
    assert _saldo(Session, wid) == 70.0

    ok = service.aggiorna_movimento(tid, tipo=TIPO_USCITA, data=tempo.adesso(),
                                    importo=50.0, wallet_id=wid,
                                    categoria_nome="Spesa", descrizione="nuova")
    assert ok is True
    with Session() as db:
        t = db.get(Transaction, tid)
        assert t.uid == uid_before          # stesso record (sync-friendly)
        assert t.rev >= 2                    # rev incrementato dalla modifica
        assert t.importo == 50.0
        assert t.descrizione == "nuova"
    assert _saldo(Session, wid) == 50.0      # 100 − 50


def test_aggiorna_movimento_ignora_giro(test_db):
    """aggiorna_movimento non deve toccare le gambe di una partita di giro."""
    Session = test_db["Session"]
    wid = _wallet(Session)
    gid = service.crea_giro(spese=[{"importo": 20.0, "wallet_id": wid}], aperta=True)
    with Session() as db:
        leg = db.execute(select(Transaction).where(Transaction.giro_id == gid)).scalars().first()
    assert service.aggiorna_movimento(leg.id, tipo=TIPO_USCITA, data=None,
                                      importo=999.0, wallet_id=wid) is False


# ── partita di giro ─────────────────────────────────────────────────────────

def test_aggiorna_giro_sostituisce_gambe(test_db):
    Session = test_db["Session"]
    wid = _wallet(Session, saldo=100.0)
    gid = service.crea_giro(
        spese=[{"importo": 30.0, "wallet_id": wid}],
        rientri=[{"importo": 20.0, "wallet_id": wid}], aperta=False)
    assert _saldo(Session, wid) == 90.0      # 100 − 30 + 20

    ok = service.aggiorna_giro(
        gid,
        spese=[{"importo": 50.0, "wallet_id": wid}],
        rientri=[{"importo": 10.0, "wallet_id": wid}], aperta=False)
    assert ok is True

    with Session() as db:
        vive = db.execute(select(Transaction).where(
            Transaction.giro_id == gid, Transaction.deleted.is_(False))).scalars().all()
        morte = db.execute(select(Transaction).where(
            Transaction.giro_id == gid, Transaction.deleted.is_(True))).scalars().all()
    assert len(vive) == 2                     # 1 spesa + 1 rientro nuovi
    assert len(morte) == 2                    # le vecchie gambe = tombstone
    assert _saldo(Session, wid) == 60.0       # 100 − 50 + 10


def test_aggiorna_giro_senza_spese_non_fa_nulla(test_db):
    Session = test_db["Session"]
    wid = _wallet(Session, saldo=100.0)
    gid = service.crea_giro(spese=[{"importo": 30.0, "wallet_id": wid}], aperta=True)
    assert service.aggiorna_giro(gid, spese=[], rientri=[], aperta=True) is False
    assert _saldo(Session, wid) == 70.0       # invariato


# ── dati per il form ────────────────────────────────────────────────────────

def test_dati_modifica_generic(test_db):
    Session = test_db["Session"]
    wid = _wallet(Session)
    service.crea_movimento(TIPO_USCITA, tempo.adesso(), 12.34, wid, descrizione="x")
    with Session() as db:
        tid = db.execute(select(Transaction)).scalar_one().id
    d = service.dati_modifica(tid)
    assert d["kind"] == "generic"
    assert d["importo"] == "12,34"
    assert d["wallet_id"] == wid


def test_dati_modifica_giro_scompone_gambe(test_db):
    Session = test_db["Session"]
    wid = _wallet(Session)
    gid = service.crea_giro(
        spese=[{"importo": 30.0, "wallet_id": wid}, {"importo": 5.0, "wallet_id": wid}],
        rientri=[{"importo": 20.0, "wallet_id": wid}], aperta=False)
    with Session() as db:
        leg = db.execute(select(Transaction).where(Transaction.giro_id == gid)).scalars().first()
    d = service.dati_modifica(leg.id)
    assert d["kind"] == "giro"
    assert d["giro_id"] == gid
    assert len(d["spese"]) == 2
    assert len(d["rientri"]) == 1
