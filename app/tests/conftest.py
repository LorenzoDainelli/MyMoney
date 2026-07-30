"""Impianto comune dei test: un database usa-e-getta, uguale per tutti.

Serviva perché l'esito di un test dipendeva dall'ORDINE in cui si lanciavano i
file. Due trappole, entrambe nate dal fatto che `from shared.db import ...`
fotografa un valore al momento dell'import:

1. **Tabelle mancanti.** `Base.metadata` conosce solo i modelli già importati.
   Un file che non importa `finance.models` creava un database temporaneo senza
   le tabelle `finance_*`: bastava che il codice sotto test ci arrivasse (per
   esempio l'import pigro dentro `portfolio/versamenti.py::_sync_finanze`) per
   ottenere «no such table: finance_wallets». Nella suite intera un altro file
   aveva già fatto quell'import e il guaio spariva.

2. **Sessione fotografata all'import.** Ogni modulo si tiene la `SessionLocal`
   che esisteva quando è stato importato. Le fixture ne sostituivano una
   manciata — quelle che ci si ricordava di elencare — e le altre continuavano
   a parlare col database VERO, `app/data/finanza.db`. Diversi file passavano
   solo perché quel database esiste su questo PC: in una copia pulita del repo
   fallivano.

C'era anche un terzo effetto, figlio dello stesso import pigro: importare
`finance.models` durante un flush registrava un listener `before_flush` mentre
SQLAlchemy stava scorrendo la lista dei listener, da cui «RuntimeError: deque
mutated during iteration».

Qui si tolgono alla radice, senza toccare il codice di produzione:

- si importano SUBITO tutti i moduli che definiscono tabelle o che catturano
  `SessionLocal`, così `Base.metadata` è sempre completa e nessun import resta
  pigro (né i listener si registrano a metà flush);
- in quei moduli `SessionLocal` diventa un rimando risolto a ogni chiamata:
  sostituire la sola `shared.db.SessionLocal` sposta tutta l'app;
- una fixture autouse punta `shared.db` a un database temporaneo, nuovo per
  ogni test e con tutte le tabelle già create.

Le fixture dei singoli file restano valide così come sono: quando sostituiscono
`shared.db.SessionLocal` con la propria sessione, il rimando le segue.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import shared.db as db_mod
from shared.db import Base

# Tutti i moduli con modelli o con `SessionLocal`. `main.py` NON va importato:
# all'import fa `create_all` sul database vero.
import finance.models          # noqa: F401  (tabelle finance_*)
import finance.service
import portfolio.market
import portfolio.models        # noqa: F401  (tabelle portfolio_*)
import portfolio.routes
import portfolio.seed
import portfolio.service
import portfolio.versamenti
import shared.ai_memory
import shared.settings_store
import shared.storico
import shared.sync

# Moduli che hanno fotografato `SessionLocal` all'import (`shared.db` no: è la
# sorgente, e rimandare a sé stessa sarebbe un giro infinito).
_MODULI_CON_SESSIONE = (
    finance.service, portfolio.market, portfolio.routes, portfolio.seed,
    portfolio.service, portfolio.versamenti, shared.ai_memory,
    shared.settings_store, shared.storico, shared.sync,
)

# Moduli che hanno fotografato anche `engine` (lo usano per le migrazioni in SQL
# grezzo). Nessun test le chiama, ma senza questo puntano al database vero.
_MODULI_CON_ENGINE = (finance.service, portfolio.seed)


class _SessioneCorrente:
    """Sta al posto di `SessionLocal` dentro i moduli e gira la chiamata a
    `shared.db.SessionLocal` NEL MOMENTO in cui serve, non all'import."""

    def __call__(self, *args, **kwargs):
        return db_mod.SessionLocal(*args, **kwargs)

    def __repr__(self) -> str:      # per leggere gli errori dei test
        return f"<SessionLocal -> {db_mod.SessionLocal!r}>"


_RIMANDO = _SessioneCorrente()
for _mod in _MODULI_CON_SESSIONE:
    _mod.SessionLocal = _RIMANDO


@pytest.fixture(autouse=True)
def database_usa_e_getta(tmp_path, monkeypatch):
    """Un database vuoto, completo di schema e tutto suo, per OGNI test.

    Autouse anche per i test che col database non c'entrano nulla: sono proprio
    quelli che, senza fixture, finivano di straforo sul `finanza.db` vero.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'conftest.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal",
                        sessionmaker(bind=engine, autoflush=False, autocommit=False))
    for mod in _MODULI_CON_ENGINE:
        monkeypatch.setattr(mod, "engine", engine)
    # Il diario del sync scrive su file a ogni commit: fuori dai dati veri.
    monkeypatch.setattr(shared.sync, "SYNC_DIR", tmp_path / "sync")

    yield
    engine.dispose()
