"""Backup: portarsi via i dati, e rimetterli dentro.

Questo file era il motore di sincronizzazione multi-dispositivo: diario
append-only per dispositivo, fusione «vince il più recente», canale bidirezionale
col telefono, copia a specchio su Google Drive. Era la risposta giusta a un
problema che non c'è più — tre copie dei dati (PC, telefono, Drive) da tenere
d'accordo. Dalla Fase 5 la copia è **una**, in un database sul cloud, e tutto
quel meccanismo era codice che poteva solo rompersi.

Di quel motore resta la parte che serve ancora, e che anzi serve di più:

- **`build_snapshot`** — la fotografia completa (conti, categorie, movimenti) in
  una struttura leggibile, con i riferimenti espressi per `uid` e non per id
  interno. È quella che si scarica da Impostazioni.
- **`replace_all_from_snapshot`** — la strada del ritorno: svuota e ricarica.
  Non fonde: se sei arrivato a usarla, i dati di adesso sono quelli che vuoi
  buttare.

Sul server il diario era anche un piccolo spreco: scriveva una riga per ogni
salvataggio in un file dentro il container, che su Cloud Run sta in memoria, non
lo leggeva nessuno e spariva a ogni riavvio.
"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from shared.config import APP_DIR
from shared.db import SessionLocal
from shared import settings_store, tempo

log = logging.getLogger("mymoney.backup")

BACKUP_DIR = APP_DIR / "data" / "backups"
# Il numero di schema resta: un file scritto da una versione futura dell'app non
# va applicato alla cieca, va rifiutato dicendolo.
SCHEMA_VERSION = 1

# Il flag «sto ricaricando»: dentro un ripristino i marcatori (uid, revisione,
# data di modifica) NON vanno rifatti, sono quelli del file. Vive in un modulo
# suo perché finance/models.py deve poterlo importare in qualunque momento (il
# perché è scritto lì).
from shared.backup_ctx import _ctx, _is_importing, importing  # noqa: F401,E402


# ── da record a campi ───────────────────────────────────────────────────────

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
    """Campi di un movimento. Le FK (wallet_id, category_id) vengono risolte in
    uid del record referenziato: l'id interno non finisce mai in un backup, o
    ricaricandolo su un database vuoto punterebbe a righe che non esistono."""
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
        # 4 decimali e non 2: il saveback della carta ha i decimillesimi
        # (0,4045 €). Tagliandoli qui, un ripristino li riporterebbe a 0,40 e
        # quei centesimi sarebbero persi per sempre.
        "importo": round(t.importo or 0.0, 4),
        "wallet_uid": w_uid, "wallet_to_uid": wt_uid,
        "categoria_uid": cat_uid,
        "descrizione": t.descrizione or "",
        "giro_id": t.giro_id or "", "giro_aperta": bool(t.giro_aperta),
        "importo_ricevuto": (round(t.importo_ricevuto, 2)
                             if t.importo_ricevuto is not None else None),
        "data_ricevuto": _iso(t.data_ricevuto),
        "controparte": t.controparte or "",
        # righe generate (arrotondamento/saveback): il legame col movimento che
        # le ha prodotte viaggia per UID. Senza, un ripristino le farebbe
        # rinascere orfane — visibili nel registro come movimenti a sé e non più
        # cancellate insieme alla spesa.
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


def _parse_dt(s):
    """Parse ISO-8601 → datetime NAIVE (None se invalido). Tollera il suffisso
    'Z' (UTC) e lo riporta nell'ora del fuso SCELTO, così la colonna resta
    omogenea: mai un mix naive/aware, che sporcherebbe i confronti fra date."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return tempo.a_naive(dt)


def _schema_troppo_nuovo(s) -> bool:
    """True se il file è di uno schema più nuovo di quello che capiamo."""
    try:
        return int(s) > SCHEMA_VERSION
    except (TypeError, ValueError):
        return False   # schema assente/illeggibile = trattalo come 1 (legacy)


# ── la fotografia ───────────────────────────────────────────────────────────

def build_snapshot() -> dict:
    """Tutto quello che è tuo: conti, categorie, movimenti (tombstone compresi).

    I movimenti cancellati restano dentro apposta: sono lapidi con la loro data,
    e buttarle vorrebbe dire che un ripristino resusciterebbe quel che avevi
    tolto."""
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
        "type": "backup",
        "ts": tempo.adesso().isoformat(),
        "wallets": wallets,
        "categorie": categorie,
        "movimenti": movimenti,
    }


def _fotografia(data: dict) -> dict:
    """La fotografia dentro un file di backup.

    I file scritti prima della Fase 5 (e il vecchio `mirror.json` del Drive)
    erano «pacchetti»: la fotografia stava sotto la chiave `snapshot`, insieme al
    diario. Quei file esistono ancora sul disco di Lorenzo, e un ripristino che
    non li sapesse leggere sarebbe una rete di sicurezza con un buco."""
    if isinstance(data.get("snapshot"), dict):
        return data["snapshot"]
    return data


def scrivi_su_file() -> Path:
    """Scrive la fotografia su file PRIMA di una sostituzione distruttiva."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = tempo.adesso().strftime("%Y-%m-%dT%H-%M-%S")
    path = BACKUP_DIR / f"prima-del-ripristino-{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_snapshot(), f, ensure_ascii=False, default=str, indent=2)
    return path


# ── la strada del ritorno ───────────────────────────────────────────────────

def _set_fields(obj, entity, fields, uid_to_wallet_id, uid_to_cat_id):
    """Riempie un record con i valori del file."""
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
    per uid. Serve perché nella lista la figlia può arrivare prima del genitore,
    e un padre non ancora inserito non ha un id da puntare.
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


def replace_all_from_snapshot(data: dict) -> dict:
    """Sostituzione TOTALE: svuota conti, categorie e movimenti e ricarica
    esattamente ciò che c'è nel file. Non fonde niente, di proposito — chi arriva
    qui vuole buttare via lo stato di adesso, non mescolarlo.

    Il chiamante DEVE aver già scritto un backup (`scrivi_su_file`).
    Ritorna {ok, count}, oppure {ok: False, future: 1} se il file è più nuovo
    dell'app che lo sta leggendo.
    """
    from finance.models import Wallet, Category, Transaction

    data = _fotografia(data)
    if _schema_troppo_nuovo(data.get("schema")):
        return {"ok": False, "future": 1}

    with importing():   # niente ri-timbratura di rev/updated_at
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
