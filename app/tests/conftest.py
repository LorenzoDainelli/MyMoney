"""Regole valide per tutti i test.

Una sola, ma importante: **nessun test tocca il database personale.**

Due problemi, una fixture sola.

1. Dieci moduli dell'app si prendono una copia del collegamento al database nel
   momento in cui vengono importati (`from shared.db import SessionLocal`).
   Sostituire `shared.db.SessionLocal` non li tocca: loro hanno già la loro
   copia. `tests/motore.py` risolve riconfigurando quell'oggetto invece di
   sostituirlo, così tutte le copie seguono insieme.

2. Alcuni test non creano un database proprio (test_insights, test_wealth...):
   prima finivano su quello di casa e passavano solo perché lì le tabelle
   esistono. Ora ognuno parte con un database **vuoto e suo**; chi poi si crea
   il proprio, semplicemente lo sostituisce.

Alla fine di ogni test si rimette tutto com'era, così nessuno si porta dietro il
database del test precedente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from motore import engine_di_prova, ripristina_motore, stato_motore


@pytest.fixture(autouse=True)
def _database_di_prova(tmp_path, monkeypatch):
    stato = stato_motore()

    from shared.db import Base
    import finance.models        # noqa: F401  registrano le tabelle su Base
    import portfolio.models      # noqa: F401
    import portfolio.market      # noqa: F401
    import shared.ai_memory      # noqa: F401
    import shared.settings_store  # noqa: F401
    import shared.storico        # noqa: F401
    import shared.sync as sync_mod

    # Anche i file: il diario del sync e i suoi backup vanno nella cartella del
    # test, non in app/data/. Chi lo fa già per conto suo lo rifà e va bene.
    monkeypatch.setattr(sync_mod, "SYNC_DIR", tmp_path / "sync")
    monkeypatch.setattr(sync_mod, "BACKUP_DIR", tmp_path / "backups")

    engine = engine_di_prova(tmp_path / "_vuoto.db")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        ripristina_motore(stato)
