"""Il database su cui girano i test: SQLite di default, PostgreSQL a richiesta.

Di default i test usano un file SQLite temporaneo: veloci, senza niente da
installare. Ma l'app, su un server, gira su PostgreSQL — e dei test verdi su un
motore diverso da quello vero non dimostrano granché. Con

    MYMONEY_TEST_PG_URL="postgresql+psycopg://utente:password@host:porta/dbprova"

la stessa identica suite gira su PostgreSQL, senza cambiare una riga dei test.

Isolamento: su SQLite ogni test ha il suo file; su PostgreSQL ogni test ha il suo
«scomparto» (uno schema), creato vuoto e buttato via all'inizio. Serve anche a
test_multidevice, che finge due dispositivi e quindi ha bisogno di due database
separati nello stesso momento.
"""
import hashlib
import os

from sqlalchemy import create_engine, text

PG_URL = os.environ.get("MYMONEY_TEST_PG_URL", "").strip()


def su_postgres() -> bool:
    """True se i test stanno girando su PostgreSQL."""
    return bool(PG_URL)


def _nome_scomparto(chiave: str) -> str:
    """Nome breve, valido e stabile per lo schema di questo test."""
    return "t_" + hashlib.sha1(chiave.encode("utf-8")).hexdigest()[:16]


def aggancia(engine) -> None:
    """Fa usare QUESTO motore a tutta l'app, non solo a chi lo riceve.

    Serve perché dieci moduli (finance/service.py, portfolio/service.py,
    shared/settings_store.py e altri) scrivono `from shared.db import
    SessionLocal`: si prendono una **copia** del riferimento nel momento in cui
    vengono importati. Sostituire `shared.db.SessionLocal` non li tocca — loro
    hanno già la loro copia, che punta ancora al database vero.

    Il trucco è non sostituire l'oggetto ma **riconfigurarlo**: `configure()`
    cambia il motore dentro l'oggetto, e siccome le copie sono lo stesso
    oggetto, tutti seguono insieme. Senza questo, un test che si dimentica di
    patchare un modulo legge (e potrebbe scrivere) sul database personale, e
    passa solo perché lì le tabelle esistono davvero.
    """
    import shared.db as db_mod
    db_mod.SessionLocal.configure(bind=engine)
    db_mod.engine = engine


def stato_motore() -> tuple:
    """Motore e collegamento attuali, per rimetterli a posto dopo il test."""
    import shared.db as db_mod
    return db_mod.engine, db_mod.SessionLocal.kw.get("bind")


def ripristina_motore(stato: tuple) -> None:
    import shared.db as db_mod
    engine, bind = stato
    db_mod.engine = engine
    db_mod.SessionLocal.configure(bind=bind)


def engine_di_prova(percorso, **kwargs):
    """Motore per un test. `percorso` è il file SQLite da usare (di solito
    tmp_path/'test.db'): serve anche da chiave per lo scomparto PostgreSQL,
    così due database distinti nello stesso test restano distinti.

    Aggancia anche tutta l'app a questo motore (vedi `aggancia`), così nessun
    pezzo va a finire sul database vero.
    """
    chiave = str(percorso)
    if not PG_URL:
        kwargs.setdefault("connect_args", {"check_same_thread": False})
        eng = create_engine(f"sqlite:///{percorso}", **kwargs)
        aggancia(eng)
        return eng

    scomparto = _nome_scomparto(chiave)
    base = create_engine(PG_URL)
    with base.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{scomparto}" CASCADE'))
        c.execute(text(f'CREATE SCHEMA "{scomparto}"'))
    base.dispose()
    # `search_path` dice a PostgreSQL di lavorare dentro quello scomparto: le
    # tabelle si creano e si leggono lì, senza pestare i piedi agli altri test.
    kwargs.pop("connect_args", None)
    eng = create_engine(PG_URL, pool_pre_ping=True,
                        connect_args={"options": f"-csearch_path={scomparto}"},
                        **kwargs)
    aggancia(eng)
    return eng
