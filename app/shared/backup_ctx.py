"""Il flag «sto ricaricando da un backup», da solo e senza dipendenze.

Quando si ricarica un file di backup, i marcatori dei record (uid, revisione,
data di modifica) NON vanno rifatti: sono quelli scritti nel file, e ritimbrarli
vorrebbe dire dire che ogni movimento è stato modificato oggi.

Sta in un file suo perché chi deve saperlo è `finance/models.py`, e importare
`shared/backup.py` da lì sarebbe un giro all'indietro. Storicamente c'era un
motivo ancora più duro: `shared/sync.py` registrava agganci su SQLAlchemy
appena importato, e un import capitato *durante* un salvataggio faceva fallire
il salvataggio («deque mutated during iteration»). Quegli agganci non ci sono
più, ma la separazione resta giusta: qui dentro non c'è niente da registrare,
quindi importarlo è sempre sicuro.
"""
import threading
from contextlib import contextmanager

_ctx = threading.local()


def _is_importing() -> bool:
    return getattr(_ctx, "importing", False)


@contextmanager
def importing():
    """Context manager: dentro questo blocco il before_flush di finance/models.py
    NON ri-timbra rev/updated_at."""
    _ctx.importing = True
    try:
        yield
    finally:
        _ctx.importing = False
