"""Motore del database (SQLite sul PC, PostgreSQL su un server).

SQLite = un singolo file sul tuo PC, zero server da installare. SQLAlchemy fa da
traduttore: la logica dell'app è scritta una volta sola e vale per entrambi i
motori. Le poche differenze che restano sono qui sotto e in shared/schema.py.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from shared.config import DB_URL, IS_SQLITE

if IS_SQLITE:
    # check_same_thread=False: necessario perché il server web usa più thread.
    # È un'opzione che esiste SOLO in SQLite: passarla a PostgreSQL è un errore.
    _kwargs = {"connect_args": {"check_same_thread": False}}
else:
    # pool_pre_ping: un server (o Cloud SQL) chiude le connessioni ferme da un po';
    # senza questo, la prima pagina dopo una pausa fallirebbe. Con questo, la
    # connessione morta viene scartata e rifatta senza che l'utente se ne accorga.
    _kwargs = {"pool_pre_ping": True, "pool_recycle": 1800}

engine = create_engine(DB_URL, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Classe madre di tutte le tabelle."""
    pass


def get_db():
    """Fornisce una sessione di database a una pagina e la chiude alla fine."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
