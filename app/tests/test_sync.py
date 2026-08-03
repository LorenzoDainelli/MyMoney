"""Test del motore di sincronizzazione (Fase 4).

Verifica i criteri di accettazione dal PIANO-V2:
- PC aggiunge X, telefono aggiunge Y (offline) → dopo sync entrambi hanno X+Y
- modifica/cancella su un lato → l'altro la riceve
- doppia modifica dello stesso record → vince l'ultima, nessun duplicato
- saldi identici al centesimo tra i due lati
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Aggiungi la cartella app/ al path (i test girano dalla radice del repo)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from shared.db import Base
from shared import settings_store
from finance.models import Wallet, Category, Transaction, _MODELLI_SYNC

# Importa sync DOPO aver configurato il path
import shared.sync as sync_mod
from motore import engine_di_prova


# ── fixture: database in memoria per ogni test ──────────────────────────────

@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    """Crea un database SQLite in memoria per ogni test, isolato."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    test_engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Crea tutte le tabelle
    Base.metadata.create_all(test_engine)

    # Patch OVUNQUE: sia in shared.db che in sync_mod (che ha già catturato il ref)
    import shared.db as db_mod
    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(sync_mod, "SessionLocal", TestSession)
    
    import finance.service as service_mod
    monkeypatch.setattr(service_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(settings_store, "SessionLocal", TestSession)

    # Patch SYNC_DIR
    sync_dir = tmp_path / "sync"
    monkeypatch.setattr(sync_mod, "SYNC_DIR", sync_dir)

    # Patch device_id (usa un id fisso per i test)
    monkeypatch.setattr(sync_mod, "get_device_id", lambda: "pc_test_device")

    yield {"engine": test_engine, "Session": TestSession, "sync_dir": sync_dir}


# ── helper ──────────────────────────────────────────────────────────────────

def _make_wallet(nome="Test Wallet", saldo=100.0, uid=None):
    return Wallet(nome=nome, tipo="conto", saldo_iniziale=saldo,
                  uid=uid or uuid.uuid4().hex)


def _make_category(nome="Test Cat", uid=None):
    return Category(nome=nome, kind="uscita", uid=uid or uuid.uuid4().hex)


def _make_transaction(tipo="uscita", importo=10.0, wallet_id=1,
                      uid=None, wallet_to_id=None, category_id=None):
    return Transaction(tipo=tipo, importo=importo, wallet_id=wallet_id,
                       wallet_to_id=wallet_to_id, category_id=category_id,
                       data=datetime.now(), uid=uid or uuid.uuid4().hex)


# ── test merge LWW ──────────────────────────────────────────────────────────

class TestMergeLWW:
    """Il merge last-write-wins decide correttamente chi vince."""

    def test_higher_rev_wins(self, test_db):
        assert sync_mod._wins(3, "2026-01-01", "dev_a", 2, "2026-01-01", "dev_b") is True
        assert sync_mod._wins(1, "2026-01-01", "dev_a", 2, "2026-01-01", "dev_b") is False

    def test_same_rev_later_updated_wins(self, test_db):
        assert sync_mod._wins(2, "2026-07-14T10:00", "dev_a", 2, "2026-07-14T09:00", "dev_b") is True
        assert sync_mod._wins(2, "2026-07-14T08:00", "dev_a", 2, "2026-07-14T09:00", "dev_b") is False

    def test_same_rev_same_time_higher_device_wins(self, test_db):
        assert sync_mod._wins(2, "2026-07-14T10:00", "dev_b", 2, "2026-07-14T10:00", "dev_a") is True
        assert sync_mod._wins(2, "2026-07-14T10:00", "dev_a", 2, "2026-07-14T10:00", "dev_b") is False


class TestImportOps:
    """import_ops applica correttamente le operazioni remote."""

    def test_insert_new_wallet(self, test_db):
        Session = test_db["Session"]
        uid = uuid.uuid4().hex
        ops = [{
            "schema": 1, "uid": uid, "entity": "wallet", "op": "upsert",
            "fields": {"uid": uid, "nome": "Nuovo Wallet", "tipo": "conto",
                       "saldo_iniziale": 50.0, "note": "", "ordine": 0,
                       "colore": "", "archiviato": False, "deleted": False,
                       "rev": 1, "updated_at": "2026-07-14T10:00:00"},
            "rev": 1, "updated_at": "2026-07-14T10:00:00",
            "device_id": "pwa_remote", "ts": "2026-07-14T10:00:00"
        }]
        result = sync_mod.import_ops(ops)
        assert result["applied"] == 1
        assert result["skipped"] == 0
        with Session() as db:
            w = db.execute(select(Wallet).where(Wallet.uid == uid)).scalar_one_or_none()
            assert w is not None
            assert w.nome == "Nuovo Wallet"
            assert w.saldo_iniziale == 50.0

    def test_skip_lower_rev(self, test_db):
        Session = test_db["Session"]
        uid = uuid.uuid4().hex
        # Crea un wallet locale con rev=3
        with Session() as db:
            db.add(Wallet(nome="Locale", tipo="conto", uid=uid, rev=3,
                          updated_at=datetime.now(), deleted=False))
            db.commit()
        # Prova a importare con rev=2 → deve essere skippato
        ops = [{
            "schema": 1, "uid": uid, "entity": "wallet", "op": "upsert",
            "fields": {"uid": uid, "nome": "Remoto", "tipo": "conto",
                       "saldo_iniziale": 0, "note": "", "ordine": 0,
                       "colore": "", "archiviato": False, "deleted": False,
                       "rev": 2, "updated_at": "2026-07-14T09:00:00"},
            "rev": 2, "updated_at": "2026-07-14T09:00:00",
            "device_id": "pwa_remote", "ts": "2026-07-14T09:00:00"
        }]
        result = sync_mod.import_ops(ops)
        assert result["skipped"] == 1
        with Session() as db:
            w = db.execute(select(Wallet).where(Wallet.uid == uid)).scalar_one()
            assert w.nome == "Locale"  # non cambiato

    def test_update_with_higher_rev(self, test_db):
        Session = test_db["Session"]
        uid = uuid.uuid4().hex
        # Crea locale rev=1
        with Session() as db:
            db.add(Wallet(nome="Vecchio", tipo="conto", uid=uid, rev=1,
                          updated_at=datetime(2026, 7, 14, 8, 0), deleted=False))
            db.commit()
        # Importa con rev=2
        ops = [{
            "schema": 1, "uid": uid, "entity": "wallet", "op": "upsert",
            "fields": {"uid": uid, "nome": "Aggiornato", "tipo": "carta",
                       "saldo_iniziale": 100.0, "note": "da remoto", "ordine": 1,
                       "colore": "#FF0000", "archiviato": False, "deleted": False,
                       "rev": 2, "updated_at": "2026-07-14T10:00:00"},
            "rev": 2, "updated_at": "2026-07-14T10:00:00",
            "device_id": "pwa_remote", "ts": "2026-07-14T10:00:00"
        }]
        result = sync_mod.import_ops(ops)
        assert result["applied"] == 1
        with Session() as db:
            w = db.execute(select(Wallet).where(Wallet.uid == uid)).scalar_one()
            assert w.nome == "Aggiornato"
            assert w.tipo == "carta"
            assert w.saldo_iniziale == 100.0

    def test_tombstone_wins(self, test_db):
        """Una cancellazione (tombstone) con rev più alto vince."""
        Session = test_db["Session"]
        uid = uuid.uuid4().hex
        with Session() as db:
            db.add(Wallet(nome="Da cancellare", tipo="conto", uid=uid, rev=1,
                          updated_at=datetime.now(), deleted=False))
            db.commit()
        ops = [{
            "schema": 1, "uid": uid, "entity": "wallet", "op": "delete",
            "fields": {"uid": uid, "nome": "Da cancellare", "tipo": "conto",
                       "saldo_iniziale": 0, "note": "", "ordine": 0,
                       "colore": "", "archiviato": False, "deleted": True,
                       "rev": 2, "updated_at": "2026-07-14T12:00:00"},
            "rev": 2, "updated_at": "2026-07-14T12:00:00",
            "device_id": "pwa_remote", "ts": "2026-07-14T12:00:00"
        }]
        result = sync_mod.import_ops(ops)
        assert result["applied"] == 1
        with Session() as db:
            w = db.execute(select(Wallet).where(Wallet.uid == uid)).scalar_one()
            assert w.deleted is True

    def test_idempotent_reimport(self, test_db):
        """Reimportare lo stesso diario non crea duplicati.
        Il secondo import ri-applica (stessi dati), ma il conteggio dei record
        resta 1 — nessun duplicato."""
        Session = test_db["Session"]
        uid = uuid.uuid4().hex
        ops = [{
            "schema": 1, "uid": uid, "entity": "wallet", "op": "upsert",
            "fields": {"uid": uid, "nome": "Idem", "tipo": "conto",
                       "saldo_iniziale": 0, "note": "", "ordine": 0,
                       "colore": "", "archiviato": False, "deleted": False,
                       "rev": 1, "updated_at": "2026-07-14T10:00:00"},
            "rev": 1, "updated_at": "2026-07-14T10:00:00",
            "device_id": "pwa_remote", "ts": "2026-07-14T10:00:00"
        }]
        sync_mod.import_ops(ops)
        sync_mod.import_ops(ops)  # reimport — idempotente
        with Session() as db:
            count = db.query(Wallet).filter(Wallet.uid == uid).count()
            assert count == 1  # nessun duplicato
            w = db.execute(select(Wallet).where(Wallet.uid == uid)).scalar_one()
            assert w.nome == "Idem"  # dati invariati


class TestTransactionSync:
    """Test sync dei movimenti con FK (wallet_uid → wallet_id)."""

    def test_transaction_with_fk_resolution(self, test_db):
        """Un movimento importato risolve wallet_uid e categoria_uid in id locali."""
        Session = test_db["Session"]
        w_uid = uuid.uuid4().hex
        c_uid = uuid.uuid4().hex
        t_uid = uuid.uuid4().hex
        # Importa wallet, categoria e transazione insieme (ordine gestito dal motore)
        ops = [
            {"schema": 1, "uid": w_uid, "entity": "wallet", "op": "upsert",
             "fields": {"uid": w_uid, "nome": "W", "tipo": "conto",
                        "saldo_iniziale": 100, "note": "", "ordine": 0,
                        "colore": "", "archiviato": False, "deleted": False,
                        "rev": 1, "updated_at": "2026-07-14T10:00:00"},
             "rev": 1, "updated_at": "2026-07-14T10:00:00",
             "device_id": "pwa_remote", "ts": "2026-07-14T10:00:00"},
            {"schema": 1, "uid": c_uid, "entity": "category", "op": "upsert",
             "fields": {"uid": c_uid, "nome": "Spesa", "kind": "uscita",
                        "archiviato": False, "deleted": False,
                        "rev": 1, "updated_at": "2026-07-14T10:00:00"},
             "rev": 1, "updated_at": "2026-07-14T10:00:00",
             "device_id": "pwa_remote", "ts": "2026-07-14T10:00:00"},
            {"schema": 1, "uid": t_uid, "entity": "transaction", "op": "upsert",
             "fields": {"uid": t_uid, "tipo": "uscita", "data": "2026-07-14T10:00:00",
                        "importo": 25.50, "wallet_uid": w_uid, "wallet_to_uid": None,
                        "categoria_uid": c_uid, "descrizione": "Benzina",
                        "giro_id": "", "giro_aperta": False,
                        "importo_ricevuto": None, "data_ricevuto": None,
                        "controparte": "", "deleted": False,
                        "rev": 1, "updated_at": "2026-07-14T10:00:00"},
             "rev": 1, "updated_at": "2026-07-14T10:00:00",
             "device_id": "pwa_remote", "ts": "2026-07-14T10:00:00"},
        ]
        result = sync_mod.import_ops(ops)
        assert result["applied"] == 3
        with Session() as db:
            t = db.execute(select(Transaction).where(Transaction.uid == t_uid)).scalar_one()
            w = db.execute(select(Wallet).where(Wallet.uid == w_uid)).scalar_one()
            c = db.execute(select(Category).where(Category.uid == c_uid)).scalar_one()
            assert t.wallet_id == w.id
            assert t.category_id == c.id
            assert t.importo == 25.50


    def test_compatta_tombstone(self, test_db):
        from finance.service import compatta_tombstone
        from datetime import datetime, timedelta
        Session = test_db["Session"]
        with Session() as db:
            w = Wallet(nome="W", tipo="conto", saldo_iniziale=0, uid=uuid.uuid4().hex)
            db.add(w)
            db.commit()
            
            t_vivo = Transaction(uid="vivo", tipo="uscita", importo=10, wallet_id=w.id,
                                 data=datetime.now(), rev=1, deleted=False, updated_at=datetime.now())
            t_recente = Transaction(uid="lapide_recente", tipo="uscita", importo=10, wallet_id=w.id,
                                    data=datetime.now(), rev=2, deleted=True, updated_at=datetime.now())
            t_vecchia = Transaction(uid="lapide_vecchia", tipo="uscita", importo=10, wallet_id=w.id,
                                    data=datetime.now(), rev=2, deleted=True, updated_at=datetime.now() - timedelta(days=400))
            db.add_all([t_vivo, t_recente, t_vecchia])
            db.commit()

        rimosse = compatta_tombstone(365)
        assert rimosse == 1

        with Session() as db:
            assert db.query(Transaction).filter_by(uid="vivo").first() is not None
            assert db.query(Transaction).filter_by(uid="lapide_recente").first() is not None
            assert db.query(Transaction).filter_by(uid="lapide_vecchia").first() is None


class TestSnapshot:
    """Snapshot: fotografia completa e applicazione."""

    def test_build_and_apply(self, test_db):
        Session = test_db["Session"]
        # Crea dati
        with Session() as db:
            w = Wallet(nome="Test", tipo="conto", uid=uuid.uuid4().hex,
                       saldo_iniziale=50.0, rev=1, updated_at=datetime.now(), deleted=False)
            db.add(w)
            c = Category(nome="Cat", kind="", uid=uuid.uuid4().hex,
                         rev=1, updated_at=datetime.now(), deleted=False)
            db.add(c)
            db.commit()
        snap = sync_mod.build_snapshot()
        assert snap["schema"] == 1
        assert len(snap["wallets"]) == 1
        assert len(snap["categorie"]) == 1
        assert snap["wallets"][0]["nome"] == "Test"

    def test_apply_snapshot_creates_records(self, test_db):
        Session = test_db["Session"]
        w_uid = uuid.uuid4().hex
        snap = {
            "schema": 1, "device_id": "pc_other",
            "ts": datetime.now().isoformat(),
            "wallets": [{"uid": w_uid, "nome": "Snap W", "tipo": "conto",
                         "saldo_iniziale": 200, "note": "", "ordine": 0,
                         "colore": "", "archiviato": False, "deleted": False,
                         "rev": 1, "updated_at": "2026-07-14T10:00:00"}],
            "categorie": [], "movimenti": [],
        }
        result = sync_mod.apply_snapshot(snap)
        assert result["applied"] == 1
        with Session() as db:
            w = db.execute(select(Wallet).where(Wallet.uid == w_uid)).scalar_one()
            assert w.nome == "Snap W"


class TestDiary:
    """Diario: scrittura, lettura e conteggio righe."""

    def test_diary_write_and_read(self, test_db):
        ops = [
            {"uid": "aaa", "entity": "wallet", "op": "upsert", "fields": {},
             "rev": 1, "updated_at": "2026-07-14", "device_id": "pc_test", "ts": "now"},
            {"uid": "bbb", "entity": "wallet", "op": "upsert", "fields": {},
             "rev": 1, "updated_at": "2026-07-14", "device_id": "pc_test", "ts": "now"},
        ]
        sync_mod._write_diary(ops)
        assert sync_mod.diary_lines_count() == 2
        all_ops = sync_mod.export_diary(since_line=0)
        assert len(all_ops) == 2
        since_1 = sync_mod.export_diary(since_line=1)
        assert len(since_1) == 1
        assert since_1[0]["uid"] == "bbb"


class TestExportImportBundle:
    """Export/import manuale (fallback)."""

    def test_round_trip(self, test_db):
        Session = test_db["Session"]
        # Crea dati
        with Session() as db:
            db.add(Wallet(nome="RoundTrip", tipo="conto", uid=uuid.uuid4().hex,
                          saldo_iniziale=75.0, rev=1, updated_at=datetime.now(),
                          deleted=False))
            db.commit()
        bundle = sync_mod.export_bundle()
        assert bundle["type"] == "bundle"
        assert len(bundle["snapshot"]["wallets"]) == 1
        # Pulisci e reimporta
        with Session() as db:
            db.query(Wallet).delete()
            db.commit()
        result = sync_mod.import_bundle(bundle)
        assert result["applied"] >= 1
        with Session() as db:
            w = db.query(Wallet).first()
            assert w.nome == "RoundTrip"


# ── revisione Fase 4: correzioni post-analisi ───────────────────────────────

class TestTimestampNaive:
    """_parse_dt riporta SEMPRE datetime naive (niente mix naive/aware anche se
    il telefono manda updated_at in UTC con la 'Z')."""

    def test_z_suffix_becomes_naive(self, test_db):
        dt = sync_mod._parse_dt("2026-07-14T08:00:00.123Z")
        assert dt is not None
        assert dt.tzinfo is None            # convertito a naive

    def test_plain_iso_stays_naive(self, test_db):
        dt = sync_mod._parse_dt("2026-07-14T10:00:00.123456")
        assert dt is not None
        assert dt.tzinfo is None

    def test_invalid_and_empty(self, test_db):
        assert sync_mod._parse_dt("") is None
        assert sync_mod._parse_dt(None) is None
        assert sync_mod._parse_dt("non-una-data") is None

    def test_imported_transaction_updated_at_is_naive(self, test_db):
        """Un movimento importato dal telefono (updated_at con 'Z') finisce in DB
        con updated_at naive: la colonna resta omogenea."""
        Session = test_db["Session"]
        w_uid = uuid.uuid4().hex
        t_uid = uuid.uuid4().hex
        ops = [
            {"schema": 1, "uid": w_uid, "entity": "wallet", "op": "upsert",
             "fields": {"uid": w_uid, "nome": "W", "tipo": "conto",
                        "saldo_iniziale": 0, "note": "", "ordine": 0, "colore": "",
                        "archiviato": False, "deleted": False, "rev": 1,
                        "updated_at": "2026-07-14T08:00:00.000Z"},
             "rev": 1, "updated_at": "2026-07-14T08:00:00.000Z",
             "device_id": "pwa_remote", "ts": "2026-07-14T08:00:00.000Z"},
            {"schema": 1, "uid": t_uid, "entity": "transaction", "op": "upsert",
             "fields": {"uid": t_uid, "tipo": "uscita", "data": "2026-07-14T10:00:00",
                        "importo": 5.0, "wallet_uid": w_uid, "wallet_to_uid": None,
                        "categoria_uid": None, "descrizione": "", "giro_id": "",
                        "giro_aperta": False, "importo_ricevuto": None,
                        "data_ricevuto": None, "controparte": "", "deleted": False,
                        "rev": 1, "updated_at": "2026-07-14T08:00:00.123Z"},
             "rev": 1, "updated_at": "2026-07-14T08:00:00.123Z",
             "device_id": "pwa_remote", "ts": "2026-07-14T08:00:00.123Z"},
        ]
        assert sync_mod.import_ops(ops)["applied"] == 2
        with Session() as db:
            t = db.execute(select(Transaction).where(Transaction.uid == t_uid)).scalar_one()
            assert t.updated_at is not None
            assert t.updated_at.tzinfo is None


class TestSnapshotCursor:
    """Lo snapshot espone diary_lines, il cursore iniziale del client (evita di
    ri-scaricare tutto il diario alla prima sync normale)."""

    def test_snapshot_has_diary_lines(self, test_db):
        sync_mod._write_diary([
            {"uid": "x", "entity": "wallet", "op": "upsert", "fields": {},
             "rev": 1, "updated_at": "2026-07-14", "device_id": "pc_test", "ts": "now"},
        ])
        snap = sync_mod.build_snapshot()
        assert "diary_lines" in snap
        assert snap["diary_lines"] == 1


class TestSchemaGuard:
    def test_schema_troppo_nuovo_snapshot(self, test_db):
        from shared import settings_store
        settings_store.set_setting("sync_needs_update", "")
        data = {"schema": 999, "wallets": []}
        r = sync_mod.apply_snapshot(data)
        assert r["future"] == 1
        assert settings_store.get_setting("sync_needs_update", "") == "1"

    def test_schema_troppo_nuovo_ops(self, test_db):
        from shared import settings_store
        settings_store.set_setting("sync_needs_update", "")
        ops = [{"schema": 999, "entity": "wallet", "op": "upsert", "fields": {}}]
        r = sync_mod.import_ops(ops)
        assert r["future"] == 1
        assert r["applied"] == 0
        assert settings_store.get_setting("sync_needs_update", "") == "1"


class TestReplaceAllFromSnapshot:
    """Fase 3 (specchio): la sostituzione TOTALE rimpiazza i dati, non li fonde."""

    def test_sostituzione_totale(self, test_db):
        Session = test_db["Session"]
        # Dati di partenza da BUTTARE: 2 wallet, 1 categoria, 2 movimenti
        with Session() as db:
            db.add(_make_wallet(nome="Vecchio A", uid="old_a"))
            db.add(_make_wallet(nome="Vecchio B", uid="old_b"))
            db.add(_make_category(nome="Cat vecchia", uid="old_cat"))
            db.commit()
            wid = db.execute(select(Wallet).where(Wallet.uid == "old_a")).scalar_one().id
            cid = db.execute(select(Category).where(Category.uid == "old_cat")).scalar_one().id
            db.add(_make_transaction(importo=5, wallet_id=wid, category_id=cid, uid="old_t1"))
            db.add(_make_transaction(importo=7, wallet_id=wid, uid="old_t2"))
            db.commit()

        # Snapshot "specchio" completamente diverso: 1 wallet, 1 categoria, 1 movimento
        snap = {
            "schema": 1,
            "wallets": [{"uid": "new_w", "nome": "Nuovo", "tipo": "conto",
                         "saldo_iniziale": 200.0, "note": "", "ordine": 0, "colore": "",
                         "archiviato": False, "deleted": False, "rev": 4,
                         "updated_at": "2026-07-22T10:00:00"}],
            "categorie": [{"uid": "new_c", "nome": "Regali", "kind": "uscita",
                           "archiviato": False, "deleted": False, "rev": 2,
                           "updated_at": "2026-07-22T10:00:00"}],
            "movimenti": [{"uid": "new_t", "tipo": "uscita", "data": "2026-07-20T10:00:00",
                           "importo": 12.0, "wallet_uid": "new_w", "wallet_to_uid": None,
                           "categoria_uid": "new_c", "descrizione": "Test", "giro_id": "",
                           "giro_aperta": False, "importo_ricevuto": None,
                           "data_ricevuto": None, "controparte": "", "deleted": False,
                           "rev": 3, "updated_at": "2026-07-22T10:00:00"}],
        }
        r = sync_mod.replace_all_from_snapshot(snap)
        assert r["ok"] is True
        assert r["count"] == 1

        with Session() as db:
            wallets = db.execute(select(Wallet)).scalars().all()
            cats = db.execute(select(Category)).scalars().all()
            movs = db.execute(select(Transaction)).scalars().all()
            # I vecchi record NON ci sono più (sostituzione, non fusione)
            assert {w.uid for w in wallets} == {"new_w"}
            assert {c.uid for c in cats} == {"new_c"}
            assert {m.uid for m in movs} == {"new_t"}
            # rev/updated_at preservati dallo snapshot (import: niente ri-timbratura)
            assert wallets[0].rev == 4
            assert wallets[0].saldo_iniziale == 200.0
            # FK risolte via uid → id locale
            assert movs[0].wallet_id == wallets[0].id
            assert movs[0].category_id == cats[0].id

    def test_schema_troppo_nuovo_non_distrugge(self, test_db):
        Session = test_db["Session"]
        with Session() as db:
            db.add(_make_wallet(nome="Da tenere", uid="keep"))
            db.commit()
        r = sync_mod.replace_all_from_snapshot({"schema": 999, "wallets": []})
        assert r.get("future") == 1
        with Session() as db:   # i dati locali NON sono stati toccati
            assert db.execute(select(Wallet)).scalars().all()[0].uid == "keep"
