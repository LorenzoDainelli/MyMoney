"""Test della sync via Google Drive (Fase 5) — PIANO-FASE-5.md T5.

Regola del PIANO-V2 (§7): ogni pezzo di sync ha test automatici PRIMA di
toccare il Drive vero → qui il trasporto è un finto-Drive in memoria con la
stessa interfaccia di DriveClient. Verifica:
- prima sync → lo stato viene caricato;
- X+Y: lo stato remoto di un altro device viene fuso e il ri-upload lo contiene;
- remoto invariato → non si riscarica (cursore modifiedTime);
- tombstone remota → il record locale diventa deleted;
- nessun cambiamento → non si ricarica (impronta del contenuto);
- non connesso / token: esiti puliti, mai eccezioni.
"""
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from shared.db import Base
from finance.models import Wallet, Transaction

import shared.sync as sync_mod
import shared.settings_store as store_mod
import shared.drive_sync as drive_mod
from motore import engine_di_prova


# ── fixture: DB in memoria + finto device id (come test_sync.py) ─────────────

@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    test_engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(test_engine)

    import shared.db as db_mod
    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(sync_mod, "SessionLocal", TestSession)
    # settings_store ha catturato SessionLocal all'import: va patchato anche lì
    monkeypatch.setattr(store_mod, "SessionLocal", TestSession)

    monkeypatch.setattr(sync_mod, "SYNC_DIR", tmp_path / "sync")
    monkeypatch.setattr(sync_mod, "get_device_id", lambda: "pc_test_device")

    yield {"Session": TestSession}


# ── finto-Drive: stessa interfaccia di DriveClient ───────────────────────────

class FakeDrive:
    def __init__(self):
        self.files = {}        # id -> {id, name, modifiedTime, data}
        self.downloads = 0
        self.uploads = 0
        self._clock = 0

    def _tick(self):
        self._clock += 1
        return f"2026-07-15T10:00:{self._clock:02d}.000Z"

    def list_state_files(self):
        return [{"id": f["id"], "name": f["name"], "modifiedTime": f["modifiedTime"]}
                for f in self.files.values()]

    def download(self, file_id):
        self.downloads += 1
        return json.loads(json.dumps(self.files[file_id]["data"], default=str))

    def upload_state(self, name, data, file_id=None):
        self.uploads += 1
        fid = file_id or f"fid{len(self.files) + 1}"
        self.files[fid] = {"id": fid, "name": name, "modifiedTime": self._tick(),
                           "data": json.loads(json.dumps(data, default=str))}
        return fid

    # helper per i test: "un altro dispositivo ha caricato il suo stato"
    def put_remote_state(self, device_id, snap):
        return self.upload_state(f"state-{device_id}.json", snap)


class AuthFailDrive:
    def list_state_files(self):
        raise drive_mod.DriveAuthError("401")

class QuotaFailDrive:
    def list_state_files(self):
        raise drive_mod.DriveError("quota")


# ── helper dati ──────────────────────────────────────────────────────────────

def _wallet_fields(uid, nome="Remoto", rev=1, deleted=False):
    return {"uid": uid, "nome": nome, "tipo": "conto", "saldo_iniziale": 100.0,
            "note": "", "ordine": 0, "colore": "", "archiviato": False,
            "deleted": deleted, "rev": rev, "updated_at": "2026-07-15T09:00:00"}


def _tx_fields(uid, wallet_uid, importo=25.0, rev=1, deleted=False):
    return {"uid": uid, "tipo": "uscita", "data": "2026-07-15T09:00:00",
            "importo": importo, "wallet_uid": wallet_uid, "wallet_to_uid": None,
            "categoria_uid": None, "descrizione": "", "giro_id": "",
            "giro_aperta": False, "importo_ricevuto": None, "data_ricevuto": None,
            "controparte": "", "deleted": deleted, "rev": rev,
            "updated_at": "2026-07-15T09:00:00"}


def _remote_snap(device_id, wallets=(), movimenti=()):
    return {"schema": 1, "device_id": device_id, "ts": "2026-07-15T09:00:00",
            "wallets": list(wallets), "categorie": [], "movimenti": list(movimenti)}


def _crea_locale(Session, nome="Locale", importo=50.0):
    """Un wallet + una uscita creati come farebbe l'app (hook attivi)."""
    with Session() as db:
        w = Wallet(nome=nome, tipo="conto", saldo_iniziale=200.0, uid=uuid.uuid4().hex)
        db.add(w)
        db.commit()
        w_uid = w.uid
        t = Transaction(tipo="uscita", importo=importo, wallet_id=w.id,
                        data=datetime.now(), uid=uuid.uuid4().hex)
        db.add(t)
        db.commit()
        t_uid = t.uid
    return w_uid, t_uid


# ── test: sync con finto-Drive ───────────────────────────────────────────────

class TestDriveSync:

    def test_prima_sync_carica_lo_stato(self, test_db):
        w_uid, t_uid = _crea_locale(test_db["Session"])
        fake = FakeDrive()
        r = drive_mod.sync_once(client=fake)
        assert r["ok"] is True
        assert r["uploaded"] is True
        nomi = [f["name"] for f in fake.list_state_files()]
        assert "state-pc_test_device.json" in nomi
        data = next(f["data"] for f in fake.files.values()
                    if f["name"] == "state-pc_test_device.json")
        assert w_uid in [w["uid"] for w in data["wallets"]]
        assert t_uid in [m["uid"] for m in data["movimenti"]]

    def test_convergenza_x_piu_y(self, test_db):
        """PC ha X, il telefono ha caricato Y → dopo la sync il PC ha X+Y e lo
        stato ricaricato su Drive contiene ENTRAMBI (criterio PIANO-V2)."""
        Session = test_db["Session"]
        wx_uid, tx_uid = _crea_locale(Session, nome="PC", importo=50.0)
        wy_uid, ty_uid = uuid.uuid4().hex, uuid.uuid4().hex
        fake = FakeDrive()
        fake.put_remote_state("pwa_abc", _remote_snap(
            "pwa_abc",
            wallets=[_wallet_fields(wy_uid, nome="Telefono")],
            movimenti=[_tx_fields(ty_uid, wy_uid, importo=10.0)]))

        r = drive_mod.sync_once(client=fake)
        assert r["ok"] is True
        assert r["downloaded"] == 1
        assert r["applied"] >= 2      # wallet + movimento del telefono
        assert r["errors"] == 0

        with Session() as db:
            uids_w = {w.uid for w in db.execute(select(Wallet)).scalars()}
            uids_t = {t.uid for t in db.execute(select(Transaction)).scalars()}
        assert {wx_uid, wy_uid} <= uids_w
        assert {tx_uid, ty_uid} <= uids_t

        data = next(f["data"] for f in fake.files.values()
                    if f["name"] == "state-pc_test_device.json")
        assert {m["uid"] for m in data["movimenti"]} >= {tx_uid, ty_uid}

    def test_remoto_invariato_non_riscarica(self, test_db):
        _crea_locale(test_db["Session"])
        fake = FakeDrive()
        fake.put_remote_state("pwa_abc", _remote_snap(
            "pwa_abc", wallets=[_wallet_fields(uuid.uuid4().hex)]))
        r1 = drive_mod.sync_once(client=fake)
        assert r1["ok"] and r1["downloaded"] == 1
        scaricati = fake.downloads
        r2 = drive_mod.sync_once(client=fake)
        assert r2["ok"] is True
        assert r2["downloaded"] == 0          # cursore modifiedTime
        assert fake.downloads == scaricati    # nessun nuovo download

    def test_nessun_cambiamento_non_ricarica(self, test_db):
        _crea_locale(test_db["Session"])
        fake = FakeDrive()
        r1 = drive_mod.sync_once(client=fake)
        assert r1["uploaded"] is True
        r2 = drive_mod.sync_once(client=fake)
        assert r2["ok"] is True
        assert r2["uploaded"] is False        # impronta del contenuto invariata
        assert fake.uploads == 1

    def test_tombstone_remota_cancella_in_locale(self, test_db):
        Session = test_db["Session"]
        w_uid, t_uid = _crea_locale(Session)
        fake = FakeDrive()
        fake.put_remote_state("pwa_abc", _remote_snap(
            "pwa_abc",
            wallets=[],
            movimenti=[_tx_fields(t_uid, w_uid, importo=50.0, rev=2, deleted=True)]))
        r = drive_mod.sync_once(client=fake)
        assert r["ok"] is True
        with Session() as db:
            t = db.execute(select(Transaction)
                           .where(Transaction.uid == t_uid)).scalar_one()
            assert t.deleted is True

    def test_schema_sconosciuto_viene_saltato(self, test_db):
        _crea_locale(test_db["Session"])
        fake = FakeDrive()
        fake.put_remote_state("pwa_abc", {"schema": 99, "wallets": [
            _wallet_fields(uuid.uuid4().hex)]})
        r = drive_mod.sync_once(client=fake)
        assert r["ok"] is True
        assert r["applied"] == 0
        assert r["errors"] == 0               # non è un errore di sync, è un forward-compat skip
        assert r["future"] == 1

    def test_non_connesso_esito_pulito(self, test_db):
        r = drive_mod.sync_once()             # nessuna credenziale nel DB di test
        assert r == {"ok": False, "error": "non_connesso", "applied": 0,
                     "skipped": 0, "errors": 0, "downloaded": 0, "uploaded": False, "future": 0}

    def test_401_esito_pulito(self, test_db):
        r = drive_mod.sync_once(client=AuthFailDrive())
        assert r["ok"] is False
        assert r["error"] == "auth"

    def test_quota_esito_pulito(self, test_db):
        r = drive_mod.sync_once(client=QuotaFailDrive())
        assert r["ok"] is False
        assert r["error"] == "quota"

    def test_flag_aggiornamento_si_autoguarisce(self, test_db):
        """Uno stato di schema futuro accende 'sync_needs_update'; quando poi
        una sync è pulita (nessuno schema futuro) il flag si spegne da solo."""
        from shared import settings_store
        _crea_locale(test_db["Session"])
        fake = FakeDrive()
        fake.put_remote_state("pwa_x", {"schema": 999, "device_id": "pwa_x",
                                        "wallets": [], "categorie": [], "movimenti": []})
        r1 = drive_mod.sync_once(client=fake)
        assert r1["future"] == 1
        assert settings_store.get_setting("sync_needs_update", "") == "1"
        # lo stato futuro sparisce (l'altro device torna a uno schema noto):
        fake.files.clear()
        r2 = drive_mod.sync_once(client=fake)
        assert r2["future"] == 0
        assert settings_store.get_setting("sync_needs_update", "") == ""


# ── test: OAuth (senza rete: endpoint token finto) ───────────────────────────

class TestOAuth:

    def test_callback_con_state_sconosciuto_rifiutata(self, test_db):
        ok, err = drive_mod.handle_callback("codice", "state-inventato", "http://x")
        assert ok is False
        assert err == "state"

    def test_access_token_valido_non_rinfresca(self, test_db, monkeypatch):
        drive_mod._save_token({"access_token": "a1", "refresh_token": "r1",
                               "expires_at": time.time() + 3600})
        chiamate = []
        monkeypatch.setattr(drive_mod, "_token_request",
                            lambda p: chiamate.append(p) or {})
        assert drive_mod.get_access_token() == "a1"
        assert chiamate == []                 # nessuna chiamata di refresh

    def test_token_scaduto_viene_rinfrescato(self, test_db, monkeypatch):
        drive_mod._save_token({"access_token": "a1", "refresh_token": "r1",
                               "expires_at": time.time() - 10})
        monkeypatch.setattr(drive_mod, "_token_request",
                            lambda p: {"access_token": "a2", "expires_in": 3600})
        assert drive_mod.get_access_token() == "a2"
        tok = drive_mod._load_token()
        assert tok["access_token"] == "a2"
        assert tok["refresh_token"] == "r1"   # il refresh token resta

    def test_revoca_google_cancella_il_token(self, test_db, monkeypatch):
        drive_mod._save_token({"access_token": "a1", "refresh_token": "r1",
                               "expires_at": time.time() - 10})
        monkeypatch.setattr(drive_mod, "_token_request",
                            lambda p: {"error": "invalid_grant"})
        assert drive_mod.get_access_token() is None
        assert drive_mod._load_token() == {}  # dimenticato: si ricollega dalla UI
        assert drive_mod.is_connected() is False
