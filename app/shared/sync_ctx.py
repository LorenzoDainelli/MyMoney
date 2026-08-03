"""Il flag «sto importando dal sync», da solo e senza dipendenze.

Sta in un file suo per un motivo preciso. Quando il sync applica i dati arrivati
da un altro dispositivo, i marcatori (uid, revisione, data di modifica) NON vanno
rifatti: sono quelli del dispositivo che li ha creati. Chi deve saperlo è
finance/models.py, che però non può chiederlo a shared/sync.py: quel modulo, non
appena viene importato, registra i suoi agganci su SQLAlchemy — e se l'import
capita *durante* un salvataggio, SQLAlchemy si trova la lista degli agganci che
cambia mentre la sta percorrendo, e fallisce con «deque mutated during
iteration».

Qui dentro non c'è niente da registrare, quindi importarlo è sempre sicuro.
"""
import threading
from contextlib import contextmanager

_ctx = threading.local()


def _is_importing() -> bool:
    return getattr(_ctx, "importing", False)


@contextmanager
def importing():
    """Context manager: dentro questo blocco i before_flush / after_commit NON
    registrano nel diario e NON ri-timbrano rev/updated_at."""
    _ctx.importing = True
    try:
        yield
    finally:
        _ctx.importing = False
