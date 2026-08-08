"""Motore di sincronizzazione multi-dispositivo (v2, Fase 4).

Protocollo stile Cashew: diario append-only per dispositivo, merge last-write-wins
per record. Il contratto è in docs/SYNC-PROTOCOL.md.

Flusso (locale → remoto):
1. Ogni scrittura locale (insert/update/soft-delete) registra un'operazione nel
   diario del dispositivo (file JSONL append-only in app/data/sync/).
2. Il diario viene esposto via API (`GET /api/finanze/diary`).
3. Un dispositivo remoto scarica le operazioni e le applica con merge LWW.

Flusso (remoto → locale):
1. Le operazioni remote arrivano via `POST /api/finanze/ops`.
2. Vengono applicate con le stesse regole di merge.
3. Durante l'import i metadati (rev/updated_at) NON vengono ri-timbrati: restano
   quelli del dispositivo sorgente.

Privacy: qui non passa nulla verso l'esterno. Il diario sta sul filesystem locale
(app/data/sync/) e le API girano solo su 127.0.0.1 (vedi shared/config.py).
"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from shared.config import APP_DIR
from shared.db import SessionLocal
from shared import settings_store, tempo

log = logging.getLogger("mymoney.sync")

# ── configurazione ──────────────────────────────────────────────────────────
SYNC_DIR = APP_DIR / "data" / "sync"
# Anche questa è una costante di modulo e non un percorso calcolato dentro la
# funzione: così i test possono spostarla altrove e non scrivere mai nella
# cartella vera dell'app (vedi tests/conftest.py).
BACKUP_DIR = APP_DIR / "data" / "backups"
SCHEMA_VERSION = 1

# ── flag thread-local per sopprimere la registrazione durante l'import ──────
# Vive in shared/sync_ctx.py, che non registra agganci e quindi si può importare
# in qualunque momento (vedi il commento lì: importare QUESTO modulo durante un
# salvataggio romperebbe SQLAlchemy). I nomi restano disponibili anche da qui,
# perché il resto del codice usa `sync.importing()`.
from shared.sync_ctx import _ctx, _is_importing, importing  # noqa: F401


# ── device id ───────────────────────────────────────────────────────────────
def get_device_id() -> str:
    """ID stabile del dispositivo (generato al primo uso, poi persistito)."""
    did = settings_store.get_setting("sync_device_id", "")
    if not did:
        did = "pc_" + uuid.uuid4().hex[:12]
        settings_store.set_setting("sync_device_id", did)
    return did


# ── serializzazione entità → dict sync ──────────────────────────────────────
def _iso(dt):
    return dt.isoformat() if dt else None


def _wallet_to_fields(w) -> dict:
    return {
        "uid": w.uid, "nome": w.nome, "tipo": w.tipo,
        "saldo_iniziale": round(w.saldo_iniziale or 0.0, 2),
        "note": w.note or "", "ordine": w.ordine,
        "colore": w.colore or "", "archiviato": bool(w.archiviato),
        "deleted": bool(w.deleted),
        "rev": w.rev, "updated_at": _iso(w.updated_at),
    }


def _category_to_fields(c) -> dict:
    return {
        "uid": c.uid, "nome": c.nome, "kind": c.kind or "",
        "archiviato": bool(c.archiviato), "deleted": bool(c.deleted),
        "rev": c.rev, "updated_at": _iso(c.updated_at),
    }


def _transaction_to_fields(t, session) -> dict:
    """Campi sync di un movimento. Le FK (wallet_id, category_id) vengono
    risolte in uid del record referenziato (l'id interno non esce mai)."""
    from finance.models import Wallet, Category
    w_uid, wt_uid, cat_uid = None, None, None
    if t.wallet_id:
        w = session.get(Wallet, t.wallet_id)
        w_uid = w.uid if w else None
    if t.wallet_to_id:
        wt = session.get(Wallet, t.wallet_to_id)
        wt_uid = wt.uid if wt else None
    if t.category_id:
        cat = session.get(Category, t.category_id)
        cat_uid = cat.uid if cat else None
    return {
        "uid": t.uid, "tipo": t.tipo, "data": _iso(t.data),
        "importo": round(t.importo or 0.0, 2),
        "wallet_uid": w_uid, "wallet_to_uid": wt_uid,
        "categoria_uid": cat_uid,
        "descrizione": t.descrizione or "",
        "giro_id": t.giro_id or "", "giro_aperta": bool(t.giro_aperta),
        "importo_ricevuto": (round(t.importo_ricevuto, 2)
                             if t.importo_ricevuto is not None else None),
        "data_ricevuto": _iso(t.data_ricevuto),
        "controparte": t.controparte or "",
        # righe generate (arrotondamento/saveback): il legame col movimento che
        # le ha prodotte viaggia per UID, non per id locale — gli id non sono gli
        # stessi su due dispositivi. Senza questo, sull'altro telefono le due
        # righe arriverebbero orfane: visibili nel registro come movimenti a sé
        # e non più cancellate insieme alla spesa.
        "origine": t.origine or "",
        "parent_uid": _uid_movimento(session, t.parent_tx_id),
        "deleted": bool(t.deleted),
        "rev": t.rev, "updated_at": _iso(t.updated_at),
    }


def _uid_movimento(session, tid) -> str | None:
    from finance.models import Transaction
    if not tid:
        return None
    p = session.get(Transaction, tid)
    return p.uid if p else None


def _obj_to_sync(obj, session) -> dict | None:
    """Converte un oggetto SQLAlchemy in un dict sync (None se non è sincronizzabile)."""
    from finance.models import Wallet, Category, Transaction
    if isinstance(obj, Wallet):
        entity, fields = "wallet", _wallet_to_fields(obj)
    elif isinstance(obj, Category):
        entity, fields = "category", _category_to_fields(obj)
    elif isinstance(obj, Transaction):
        entity, fields = "transaction", _transaction_to_fields(obj, session)
    else:
        return None
    op = "delete" if getattr(obj, "deleted", False) else "upsert"
    return {
        "schema": SCHEMA_VERSION,
        "uid": obj.uid,
        "entity": entity,
        "op": op,
        "fields": fields,
        "rev": obj.rev,
        "updated_at": _iso(obj.updated_at),
        "device_id": get_device_id(),
        "ts": tempo.adesso().isoformat(),
    }


# ── registrazione automatica nel diario ─────────────────────────────────────

def _diary_path() -> Path:
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    return SYNC_DIR / f"changes-{get_device_id()}.jsonl"


def _write_diary(ops: list[dict]) -> None:
    """Append delle operazioni al diario locale (una riga JSON per operazione)."""
    if not ops:
        return
    path = _diary_path()
    with open(path, "a", encoding="utf-8") as f:
        for op in ops:
            f.write(json.dumps(op, ensure_ascii=False, default=str) + "\n")


def _setup_diary_hooks():
    """Registra gli hook SQLAlchemy per la registrazione automatica nel diario.

    Viene chiamato UNA volta all'import del modulo. Gli hook:
    - before_flush (dopo quello di models.py): raccoglie le operazioni pendenti
    - after_commit: le scrive nel diario (solo se il commit ha successo)
    - after_soft_rollback: pulisce le operazioni pendenti se il commit fallisce
    """
    from finance.models import _MODELLI_SYNC

    @event.listens_for(Session, "before_flush")
    def _collect_sync_ops(session, flush_context, instances):
        """Raccoglie le operazioni da registrare nel diario. Gira DOPO il
        before_flush di models.py (che timbra uid/rev/updated_at), quindi i
        valori sono definitivi."""
        if _is_importing():
            return
        pending = []
        # Nuovi record
        for obj in session.new:
            if isinstance(obj, _MODELLI_SYNC):
                entry = _obj_to_sync(obj, session)
                if entry:
                    pending.append(entry)
        # Record modificati
        for obj in session.dirty:
            if isinstance(obj, _MODELLI_SYNC) and session.is_modified(obj, include_collections=False):
                entry = _obj_to_sync(obj, session)
                if entry:
                    pending.append(entry)
        if pending:
            session.info.setdefault("_sync_pending", []).extend(pending)

    @event.listens_for(Session, "after_commit")
    def _flush_diary(session):
        """Scrive le operazioni nel diario SOLO se il commit ha successo."""
        ops = session.info.pop("_sync_pending", [])
        if ops:
            _write_diary(ops)

    @event.listens_for(Session, "after_soft_rollback")
    def _discard_pending(session, previous_transaction):
        """Pulisce le operazioni pendenti se il commit fallisce."""
        session.info.pop("_sync_pending", None)


# ── lettura del diario ──────────────────────────────────────────────────────

def diary_lines_count() -> int:
    """Numero totale di righe nel diario locale."""
    path = _diary_path()
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def export_diary(since_line: int = 0) -> list[dict]:
    """Ritorna le operazioni del diario locale dal cursore `since_line` in poi
    (0-indexed: 0 = tutte, N = dalla riga N esclusa)."""
    path = _diary_path()
    if not path.exists():
        return []
    ops = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < since_line:
                continue
            line = line.strip()
            if line:
                try:
                    ops.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # riga corrotta: skip, non bloccare la sync
    return ops


# ── merge LWW ───────────────────────────────────────────────────────────────

def _wins(remote_rev, remote_updated, remote_device,
          local_rev, local_updated, local_device) -> bool:
    """True se il record remoto vince sul locale (LWW per record).
    Ordinamento: (rev, updated_at, device_id) — vince il più alto."""
    if (remote_rev or 0) != (local_rev or 0):
        return (remote_rev or 0) > (local_rev or 0)
    r_up = str(remote_updated or "")
    l_up = str(local_updated or "")
    if r_up != l_up:
        return r_up > l_up
    return str(remote_device or "") > str(local_device or "")


def _parse_dt(s):
    """Parse ISO-8601 → datetime NAIVE (None se invalido). Tollera il suffisso
    'Z' (UTC) prodotto dal telefono e lo riporta nell'ora del fuso SCELTO, così
    la colonna resta omogenea: mai un mix naive/aware che sporcherebbe i
    confronti e i calcoli di date altrove.

    Prima si usava l'orologio della macchina che riceve: lo stesso movimento,
    arrivato sul PC o sul server, finiva a due ore di distanza. E sono proprio
    queste date che decidono la fusione «vince il più recente»."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return tempo.a_naive(dt)


def _schema_troppo_nuovo(s) -> bool:
    """True se il messaggio è di uno schema più nuovo di quello che capiamo."""
    try:
        return int(s) > SCHEMA_VERSION
    except (TypeError, ValueError):
        return False   # schema assente/illeggibile = trattalo come 1 (legacy)


def import_ops(ops: list[dict], source_device_id: str = "") -> dict:
    """Applica una lista di operazioni remote con merge LWW.

    Ritorna {applied: int, skipped: int, errors: int}.
    Le operazioni vengono raggruppate per entità e applicate nell'ordine
    corretto: wallet → category → transaction (per risolvere le FK).
    """
    from finance.models import Wallet, Category, Transaction

    # Raggruppa per entità e ordina per priorità di applicazione
    by_entity = {"wallet": [], "category": [], "transaction": []}
    for op in ops:
        entity = op.get("entity", "")
        if entity in by_entity:
            by_entity[entity].append(op)

    applied = skipped = errors = future = 0

    with importing():
        with SessionLocal() as db:
            # Mappa uid→id per risolvere le FK dei movimenti
            uid_to_wallet_id = {w.uid: w.id for w in db.execute(select(Wallet)).scalars().all()}
            uid_to_cat_id = {c.uid: c.id for c in db.execute(select(Category)).scalars().all()}
            device_id = get_device_id()

            da_collegare = []       # (riga generata, uid del genitore)
            for entity in ("wallet", "category", "transaction"):
                for op in by_entity[entity]:
                    if _schema_troppo_nuovo(op.get("schema")):
                        future += 1
                        continue
                    try:
                        result = _apply_one(db, op, entity, device_id,
                                            uid_to_wallet_id, uid_to_cat_id,
                                            da_collegare)
                        if result == "applied":
                            applied += 1
                        else:
                            skipped += 1
                    except Exception:
                        errors += 1
                        # Non blocca la sync, ma lascia traccia per il debug
                        # (solo uid/entity: niente importi o descrizioni nei log).
                        log.warning("sync: op saltata uid=%s entity=%s",
                                    op.get("uid", ""), entity, exc_info=True)

            _collega_genitori(db, da_collegare)
            db.commit()

    if future > 0:
        settings_store.set_setting("sync_needs_update", "1")

    return {"applied": applied, "skipped": skipped, "errors": errors, "future": future}


def _apply_one(db, op, entity, local_device_id, uid_to_wallet_id, uid_to_cat_id,
               da_collegare=None) -> str:
    """Applica UNA operazione con merge LWW. Ritorna 'applied' o 'skipped'."""
    from finance.models import Wallet, Category, Transaction

    uid = op.get("uid", "")
    fields = op.get("fields", {})
    remote_rev = op.get("rev", 0)
    remote_updated = op.get("updated_at", "")
    remote_device = op.get("device_id", "")

    if not uid or not fields:
        return "skipped"

    # Trova il record locale per uid
    ModelClass = {"wallet": Wallet, "category": Category, "transaction": Transaction}[entity]
    local = db.execute(select(ModelClass).where(ModelClass.uid == uid)).scalar_one_or_none()

    if local is not None:
        # Record esiste: confronta per decidere chi vince
        if not _wins(remote_rev, remote_updated, remote_device,
                     local.rev, _iso(local.updated_at), local_device_id):
            return "skipped"
        # Il remoto vince: aggiorna i campi
        _set_fields(local, entity, fields, uid_to_wallet_id, uid_to_cat_id)
        nuovo = local
    else:
        # Record non esiste: inserisci
        obj = ModelClass()
        obj.uid = uid
        _set_fields(obj, entity, fields, uid_to_wallet_id, uid_to_cat_id)
        db.add(obj)
        db.flush()
        # Aggiorna le mappe per eventuali record successivi che referenziano questo
        if entity == "wallet":
            uid_to_wallet_id[uid] = obj.id
        elif entity == "category":
            uid_to_cat_id[uid] = obj.id
        nuovo = obj

    if entity == "transaction" and da_collegare is not None and fields.get("parent_uid"):
        da_collegare.append((nuovo, fields["parent_uid"]))

    return "applied"


def _set_fields(obj, entity, fields, uid_to_wallet_id, uid_to_cat_id):
    """Imposta i campi di un record dai valori sync (fields dict)."""
    # Metadati sync (sempre presenti)
    obj.rev = fields.get("rev", 1)
    obj.updated_at = _parse_dt(fields.get("updated_at"))
    obj.deleted = bool(fields.get("deleted", False))

    if entity == "wallet":
        obj.nome = fields.get("nome", "")
        obj.tipo = fields.get("tipo", "altro")
        obj.saldo_iniziale = fields.get("saldo_iniziale", 0.0)
        obj.note = fields.get("note", "")
        obj.ordine = fields.get("ordine", 0)
        obj.archiviato = bool(fields.get("archiviato", False))
        obj.colore = fields.get("colore", "")

    elif entity == "category":
        obj.nome = fields.get("nome", "")
        obj.kind = fields.get("kind", "")
        obj.archiviato = bool(fields.get("archiviato", False))

    elif entity == "transaction":
        obj.tipo = fields.get("tipo", "uscita")
        obj.data = _parse_dt(fields.get("data")) or tempo.adesso()
        obj.importo = fields.get("importo", 0.0)
        obj.descrizione = fields.get("descrizione", "")
        obj.giro_id = fields.get("giro_id", "")
        obj.giro_aperta = bool(fields.get("giro_aperta", False))
        obj.importo_ricevuto = fields.get("importo_ricevuto")
        obj.data_ricevuto = _parse_dt(fields.get("data_ricevuto"))
        obj.controparte = fields.get("controparte", "")
        obj.origine = fields.get("origine", "")
        # Risolvi FK: uid → id locale
        w_uid = fields.get("wallet_uid")
        obj.wallet_id = uid_to_wallet_id.get(w_uid) if w_uid else None
        wt_uid = fields.get("wallet_to_uid")
        obj.wallet_to_id = uid_to_wallet_id.get(wt_uid) if wt_uid else None
        cat_uid = fields.get("categoria_uid")
        obj.category_id = uid_to_cat_id.get(cat_uid) if cat_uid else None
        # il genitore può ancora non esistere (arriva dopo nella stessa lista):
        # si collega in seconda passata, vedi _collega_genitori
        if not fields.get("parent_uid"):
            obj.parent_tx_id = None


def _collega_genitori(db, coppie) -> None:
    """Seconda passata: lega ogni riga generata al suo movimento padre, cercandolo
    per uid. Serve perché nella lista di operazioni la figlia può arrivare prima
    del genitore, e un padre non ancora inserito non ha un id da puntare.
    Una figlia il cui genitore non arriva resta senza legame: meglio una riga
    visibile di troppo che una riga invisibile e impossibile da cancellare."""
    from finance.models import Transaction
    if not coppie:
        return
    db.flush()
    uids = {p for _, p in coppie if p}
    mappa = {t.uid: t.id for t in db.execute(
        select(Transaction).where(Transaction.uid.in_(uids))).scalars().all()}
    for obj, parent_uid in coppie:
        obj.parent_tx_id = mappa.get(parent_uid)


# ── snapshot ────────────────────────────────────────────────────────────────

def build_snapshot() -> dict:
    """Fotografia completa di tutti i dati sync (wallet + categorie + movimenti).
    Usata per il primo avvio di un dispositivo nuovo."""
    from finance.models import Wallet, Category, Transaction
    with SessionLocal() as db:
        ws = list(db.execute(select(Wallet)).scalars().all())
        cs = list(db.execute(select(Category)).scalars().all())
        ts = list(db.execute(select(Transaction)).scalars().all())
        wallets = [_wallet_to_fields(w) for w in ws]
        categorie = [_category_to_fields(c) for c in cs]
        movimenti = [_transaction_to_fields(t, db) for t in ts]
    return {
        "schema": SCHEMA_VERSION,
        "device_id": get_device_id(),
        "ts": tempo.adesso().isoformat(),
        "diary_lines": diary_lines_count(),   # cursore iniziale per il client
        "wallets": wallets,
        "categorie": categorie,
        "movimenti": movimenti,
    }


def apply_snapshot(data: dict) -> dict:
    """Applica uno snapshot (primo avvio): tratta ogni record come un upsert."""
    if _schema_troppo_nuovo(data.get("schema")):
        settings_store.set_setting("sync_needs_update", "1")
        return {"applied": 0, "skipped": 0, "errors": 0, "future": 1}

    ops = []
    for entity, key in (("wallet", "wallets"), ("category", "categorie"),
                        ("transaction", "movimenti")):
        for fields in data.get(key, []):
            ops.append({
                "schema": SCHEMA_VERSION,
                "uid": fields.get("uid", ""),
                "entity": entity,
                "op": "delete" if fields.get("deleted") else "upsert",
                "fields": fields,
                "rev": fields.get("rev", 1),
                "updated_at": fields.get("updated_at", ""),
                "device_id": data.get("device_id", ""),
                "ts": data.get("ts", ""),
            })
    return import_ops(ops, source_device_id=data.get("device_id", ""))


# ── export/import file (fallback manuale) ───────────────────────────────────

def export_bundle() -> dict:
    """Bundle completo: snapshot + diario. Per backup o sync manuale."""
    return {
        "schema": SCHEMA_VERSION,
        "type": "bundle",
        "snapshot": build_snapshot(),
        "diary": export_diary(since_line=0),
    }


def import_bundle(data: dict) -> dict:
    """Importa un bundle (snapshot + diario) da un altro dispositivo."""
    result = {"applied": 0, "skipped": 0, "errors": 0, "future": 0}
    # Prima lo snapshot (crea i record base)
    snap = data.get("snapshot")
    if snap:
        r = apply_snapshot(snap)
        result["applied"] += r.get("applied", 0)
        result["skipped"] += r.get("skipped", 0)
        result["errors"] += r.get("errors", 0)
        result["future"] += r.get("future", 0)
    # Poi il diario (aggiorna con le modifiche più recenti)
    diary = data.get("diary", [])
    if diary:
        r = import_ops(diary)
        result["applied"] += r.get("applied", 0)
        result["skipped"] += r.get("skipped", 0)
        result["errors"] += r.get("errors", 0)
        result["future"] += r.get("future", 0)
    return result


# ── Fase 3: copia a SPECCHIO (sostituzione totale) ──────────────────────────

def build_mirror() -> dict:
    """Contenuto di mirror.json: la fotografia completa + un'etichetta."""
    snap = build_snapshot()
    snap["type"] = "mirror"
    return snap


def backup_bundle_to_file() -> Path:
    """Scrive un bundle completo (snapshot + diario) su file, PRIMA di una
    sostituzione distruttiva. Serve da rete di sicurezza recuperabile."""
    backup_dir = BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = tempo.adesso().strftime("%Y-%m-%dT%H-%M-%S")
    path = backup_dir / f"pre-scarica-{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(export_bundle(), f, ensure_ascii=False, default=str, indent=2)
    return path


def replace_all_from_snapshot(data: dict) -> dict:
    """Sostituzione TOTALE (non fusione): svuota wallet/categorie/movimenti e
    ricarica esattamente ciò che c'è nello snapshot. Usata dallo «Scarica» a
    specchio. Il chiamante DEVE aver già fatto un backup (backup_bundle_to_file).

    Ritorna {ok, count} oppure {ok: False, future: 1} se lo schema è più nuovo.
    """
    from finance.models import Wallet, Category, Transaction

    if _schema_troppo_nuovo(data.get("schema")):
        settings_store.set_setting("sync_needs_update", "1")
        return {"ok": False, "future": 1}

    with importing():   # niente ri-timbratura di rev/updated_at, niente diario
        with SessionLocal() as db:
            # Svuota in ordine di FK: prima i movimenti, poi categorie e wallet.
            db.execute(delete(Transaction))
            db.execute(delete(Category))
            db.execute(delete(Wallet))
            db.flush()

            uid_to_wallet_id: dict[str, int] = {}
            for fields in data.get("wallets", []):
                w = Wallet()
                w.uid = fields.get("uid") or uuid.uuid4().hex
                _set_fields(w, "wallet", fields, {}, {})
                db.add(w)
                db.flush()
                uid_to_wallet_id[w.uid] = w.id

            uid_to_cat_id: dict[str, int] = {}
            for fields in data.get("categorie", []):
                c = Category()
                c.uid = fields.get("uid") or uuid.uuid4().hex
                _set_fields(c, "category", fields, {}, {})
                db.add(c)
                db.flush()
                uid_to_cat_id[c.uid] = c.id

            n = 0
            da_collegare = []
            for fields in data.get("movimenti", []):
                t = Transaction()
                t.uid = fields.get("uid") or uuid.uuid4().hex
                _set_fields(t, "transaction", fields, uid_to_wallet_id, uid_to_cat_id)
                db.add(t)
                if fields.get("parent_uid"):
                    da_collegare.append((t, fields["parent_uid"]))
                n += 1

            _collega_genitori(db, da_collegare)
            db.commit()

    return {"ok": True, "count": n}


# ── inizializzazione (chiamata all'import del modulo) ───────────────────────
_setup_diary_hooks()

