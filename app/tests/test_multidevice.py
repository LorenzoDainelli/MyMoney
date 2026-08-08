"""Test multi-dispositivo (Fase 7 T2).
Simula due device (PC e PWA, oppure Device 1 e Device 2) con due DB separati
che sincronizzano sullo stesso finto-Drive.
"""
import uuid
import json
import sys
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from shared.db import Base
import shared.db as db_mod
import shared.sync as sync_mod
import shared.settings_store as store_mod
import shared.drive_sync as drive_mod
from finance.models import Wallet, Transaction
from motore import engine_di_prova


class FakeDrive:
    def __init__(self):
        self.files = {}
        self._clock = 0

    def _tick(self):
        self._clock += 1
        return f"2026-07-15T10:00:{self._clock:02d}.000Z"

    def list_state_files(self):
        return [{"id": f["id"], "name": f["name"], "modifiedTime": f["modifiedTime"]}
                for f in self.files.values()]

    def download(self, file_id):
        return json.loads(json.dumps(self.files[file_id]["data"], default=str))

    def upload_state(self, name, data, file_id=None):
        fid = file_id or f"fid{len(self.files) + 1}"
        self.files[fid] = {"id": fid, "name": name, "modifiedTime": self._tick(),
                           "data": json.loads(json.dumps(data, default=str))}
        return fid


@pytest.fixture
def drive_condiviso():
    return FakeDrive()


@pytest.fixture
def device_factory(tmp_path):
    class Device:
        def __init__(self, name):
            self.name = name
            self.db_path = tmp_path / f"{name}.db"
            self.sync_dir = tmp_path / f"sync_{name}"
            self.sync_dir.mkdir(exist_ok=True)
            self.engine = engine_di_prova(self.db_path)
            self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
            Base.metadata.create_all(self.engine)

    def _crea(name):
        return Device(name)
    return _crea


@contextmanager
def come_device(device):
    import finance.service as service_mod
    with patch.object(db_mod, "engine", device.engine), \
         patch.object(db_mod, "SessionLocal", device.Session), \
         patch.object(sync_mod, "SessionLocal", device.Session), \
         patch.object(store_mod, "SessionLocal", device.Session), \
         patch.object(service_mod, "SessionLocal", device.Session), \
         patch.object(sync_mod, "SYNC_DIR", device.sync_dir), \
         patch.object(sync_mod, "get_device_id", lambda: device.name):
        yield device


def crea_wallet_tx(Session, nome_w, importo, rev=1, data=None):
    if data is None:
        data = datetime.now()
    w_uid = uuid.uuid4().hex
    t_uid = uuid.uuid4().hex
    with Session() as db:
        w = Wallet(nome=nome_w, tipo="conto", saldo_iniziale=0, uid=w_uid, rev=rev, updated_at=data)
        db.add(w)
        db.commit()
        t = Transaction(tipo="uscita", importo=importo, wallet_id=w.id, uid=t_uid, rev=rev, updated_at=data, data=data)
        db.add(t)
        db.commit()
    return w_uid, t_uid


def test_convergenza_x_y(device_factory, drive_condiviso):
    dev1 = device_factory("uno")
    dev2 = device_factory("due")

    with come_device(dev1):
        w1_uid, t1_uid = crea_wallet_tx(dev1.Session, "W1", 50.0)

    with come_device(dev2):
        w2_uid, t2_uid = crea_wallet_tx(dev2.Session, "W2", 10.0)

    # Sync a giro
    with come_device(dev1):
        r = drive_mod.sync_once(client=drive_condiviso)
        assert r["ok"]
    
    with come_device(dev2):
        r = drive_mod.sync_once(client=drive_condiviso)
        assert r["ok"]
        
    with come_device(dev1):
        r = drive_mod.sync_once(client=drive_condiviso)
        assert r["ok"]

    # Verifica stato convergente: entrambi devono avere X e Y
    def verifica(Session):
        with Session() as db:
            w_uids = {w.uid for w in db.query(Wallet).all()}
            t_uids = {t.uid for t in db.query(Transaction).all()}
            assert w1_uid in w_uids and w2_uid in w_uids
            assert t1_uid in t_uids and t2_uid in t_uids
    
    verifica(dev1.Session)
    verifica(dev2.Session)


def test_conflitto_lww(device_factory, drive_condiviso):
    dev1 = device_factory("uno")
    dev2 = device_factory("due")
    
    comune_uid = "transazione_comune"
    w_uid = "wallet_comune"
    
    # Su dev1 vince rev=2, updated_at più recente
    with come_device(dev1):
        with dev1.Session() as db:
            w = Wallet(uid=w_uid, nome="W", tipo="conto", saldo_iniziale=0)
            db.add(w)
            db.commit()
            db.add(Transaction(uid=comune_uid, tipo="uscita", importo=10.0, wallet_id=w.id, 
                               rev=2, updated_at=datetime(2026, 7, 15, 12, 0)))
            db.commit()

    # Su dev2 ha rev=1, updated_at più vecchio
    with come_device(dev2):
        with dev2.Session() as db:
            w = Wallet(uid=w_uid, nome="W", tipo="conto", saldo_iniziale=0)
            db.add(w)
            db.commit()
            db.add(Transaction(uid=comune_uid, tipo="uscita", importo=20.0, wallet_id=w.id, 
                               rev=1, updated_at=datetime(2026, 7, 15, 10, 0)))
            db.commit()
            
    with come_device(dev1): drive_mod.sync_once(client=drive_condiviso)
    with come_device(dev2): drive_mod.sync_once(client=drive_condiviso)
    with come_device(dev1): drive_mod.sync_once(client=drive_condiviso)
    
    def verifica(Session):
        with Session() as db:
            txs = db.query(Transaction).filter_by(uid=comune_uid).all()
            assert len(txs) == 1
            assert txs[0].importo == 10.0
            assert txs[0].rev == 2
            
    verifica(dev1.Session)
    verifica(dev2.Session)


def test_tombstone_propagata(device_factory, drive_condiviso):
    dev1 = device_factory("uno")
    dev2 = device_factory("due")
    
    with come_device(dev1):
        w_uid, t_uid = crea_wallet_tx(dev1.Session, "W1", 50.0)
        drive_mod.sync_once(client=drive_condiviso)
        
    with come_device(dev2):
        drive_mod.sync_once(client=drive_condiviso)
        
    with come_device(dev1):
        with dev1.Session() as db:
            t = db.query(Transaction).filter_by(uid=t_uid).first()
            t.deleted = True
            t.rev += 1
            t.updated_at = datetime.now()
            db.commit()
        drive_mod.sync_once(client=drive_condiviso)
        
    with come_device(dev2):
        drive_mod.sync_once(client=drive_condiviso)
        with dev2.Session() as db:
            t = db.query(Transaction).filter_by(uid=t_uid).first()
            assert t.deleted is True


# ---------------- righe generate dalla carta (arrotondamento / saveback) -------
# Il legame fra la spesa e le sue due righe è un id LOCALE: su un altro
# dispositivo quell'id è di un altro movimento, o di nessuno. Deve viaggiare per
# uid, altrimenti sul telefono le due righe arrivano orfane: visibili nel
# registro come movimenti a sé e non più cancellate insieme alla spesa.
def _carta_e_salvadanaio(Session):
    import finance.service as fin
    with Session() as db:
        db.add(Wallet(nome="Trade Republic", tipo="carta", saldo_iniziale=100.0,
                      uid=uuid.uuid4().hex, arrotonda=True, saveback_pct=1.0,
                      saveback_tetto=15.0))
        db.add(Wallet(nome=fin.NOME_WALLET_NASCOSTI, tipo="altro",
                      saldo_iniziale=0.0, uid=uuid.uuid4().hex))
        db.commit()
        return db.query(Wallet).filter(Wallet.nome == "Trade Republic").one().id


def test_le_righe_generate_restano_legate_sull_altro_dispositivo(device_factory, drive_condiviso):
    import finance.service as fin
    dev1, dev2 = device_factory("uno"), device_factory("due")

    with come_device(dev1):
        wid = _carta_e_salvadanaio(dev1.Session)
        tid = fin.crea_uscita_carta(data=datetime.now(), importo=7.60,
                                    wallet_id=wid, categoria_nome="Spesa")
        assert len(fin.lista_movimenti()) == 1          # una riga, non tre
        drive_mod.sync_once(client=drive_condiviso)

    with come_device(dev2):
        drive_mod.sync_once(client=drive_condiviso)
        # anche qui il registro ne mostra UNA, con le due figlie dentro
        righe = fin.lista_movimenti()
        assert len(righe) == 1
        assert righe[0]["addebito"] == 8.00
        assert sorted(f["t"].origine for f in righe[0]["figlie"]) == [
            fin.ORIGINE_ARROTONDAMENTO, fin.ORIGINE_SAVEBACK]
        # e cancellandola da qui se ne vanno tutte e tre
        fin.elimina_movimento(righe[0]["t"].id)
        with dev2.Session() as db:
            assert db.query(Transaction).filter(Transaction.deleted.is_(False)).count() == 0
    assert tid                                           # la spesa esisteva davvero


def test_il_saveback_viaggia_con_tutti_i_suoi_decimali(device_factory, drive_condiviso):
    """Il sync tagliava ogni importo a 2 decimali. Sul saveback (0,4045 € su una
    spesa da 40,45) voleva dire spedire 0,40: l'altro dispositivo lo avrebbe
    mostrato sbagliato e, alla prima modifica fatta da lì, avrebbe rimandato
    indietro la versione mutilata — cancellando i decimillesimi per sempre."""
    import finance.service as fin
    dev1, dev2 = device_factory("uno"), device_factory("due")

    with come_device(dev1):
        wid = _carta_e_salvadanaio(dev1.Session)
        fin.crea_uscita_carta(data=datetime.now(), importo=40.45,
                              wallet_id=wid, categoria_nome="Spesa")
        drive_mod.sync_once(client=drive_condiviso)

    with come_device(dev2):
        drive_mod.sync_once(client=drive_condiviso)
        figlie = fin.lista_movimenti()[0]["figlie"]
        sav = next(f["t"] for f in figlie if f["t"].origine == fin.ORIGINE_SAVEBACK)
        assert sav.importo == 0.4045


def test_la_figlia_che_arriva_prima_del_genitore_si_lega_lo_stesso(device_factory):
    """Nella lista di operazioni l'ordine non è garantito: se la figlia arriva
    per prima, il genitore non ha ancora un id da puntare. Il legame si chiude
    in seconda passata, non si perde."""
    import finance.service as fin
    dev = device_factory("solo")
    with come_device(dev):
        with dev.Session() as db:
            db.add(Wallet(nome="Conto", tipo="conto", saldo_iniziale=0.0, uid="w1"))
            db.commit()
        padre_uid, figlia_uid = uuid.uuid4().hex, uuid.uuid4().hex
        base = {"wallet_uid": "w1", "data": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(), "rev": 1, "deleted": False}
        ops = [
            {"schema": 1, "uid": figlia_uid, "entity": "transaction", "op": "upsert",
             "rev": 1, "updated_at": base["updated_at"], "device_id": "altro",
             "fields": dict(base, tipo="trasferimento", importo=0.40,
                            origine=fin.ORIGINE_ARROTONDAMENTO, parent_uid=padre_uid)},
            {"schema": 1, "uid": padre_uid, "entity": "transaction", "op": "upsert",
             "rev": 1, "updated_at": base["updated_at"], "device_id": "altro",
             "fields": dict(base, tipo="uscita", importo=7.60)},
        ]
        assert sync_mod.import_ops(ops, source_device_id="altro")["applied"] == 2
        with dev.Session() as db:
            padre = db.query(Transaction).filter(Transaction.uid == padre_uid).one()
            figlia = db.query(Transaction).filter(Transaction.uid == figlia_uid).one()
            assert figlia.parent_tx_id == padre.id
            assert figlia.origine == fin.ORIGINE_ARROTONDAMENTO
