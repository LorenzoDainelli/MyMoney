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


def engine_di_prova(percorso, **kwargs):
    """Motore per un test. `percorso` è il file SQLite da usare (di solito
    tmp_path/'test.db'): serve anche da chiave per lo scomparto PostgreSQL,
    così due database distinti nello stesso test restano distinti."""
    chiave = str(percorso)
    if not PG_URL:
        kwargs.setdefault("connect_args", {"check_same_thread": False})
        return create_engine(f"sqlite:///{percorso}", **kwargs)

    scomparto = _nome_scomparto(chiave)
    base = create_engine(PG_URL)
    with base.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{scomparto}" CASCADE'))
        c.execute(text(f'CREATE SCHEMA "{scomparto}"'))
    base.dispose()
    # `search_path` dice a PostgreSQL di lavorare dentro quello scomparto: le
    # tabelle si creano e si leggono lì, senza pestare i piedi agli altri test.
    kwargs.pop("connect_args", None)
    return create_engine(PG_URL, pool_pre_ping=True,
                         connect_args={"options": f"-csearch_path={scomparto}"},
                         **kwargs)
