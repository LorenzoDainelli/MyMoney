"""Il salvataggio della pagina Impostazioni.

Nasce da un difetto vero: la casella «Cerca sul web» stava nel modulo
dell'agente, si spuntava, si salvava — e tornava spenta. Il modulo manda tutto
a `POST /impostazioni`, e quella funzione leggeva modello, modalità, provider e
Vertex, ma di `web` non sapeva niente.

Il tranello è che una casella NON spuntata non manda niente: dal solo `web` il
server non distingue «l'utente l'ha spenta» da «questo modulo non parla di
web». Serve un segnaposto che dica che la domanda è stata posta — ed è
esattamente questo che i test qui sotto tengono fermo.

Niente server e niente `TestClient`: la rotta è una funzione normale e si
chiama con una richiesta finta che sa solo restituire il modulo.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from starlette.datastructures import FormData

from shared import ai
from shared import settings_routes


class RichiestaFinta:
    """Solo quello che serve a `salva`: `await request.form()`."""

    def __init__(self, **campi):
        self._form = FormData(list(campi.items()))

    async def form(self):
        return self._form


def _salva(**campi):
    return asyncio.run(settings_routes.salva(RichiestaFinta(**campi)))


def test_accendere_il_web_resta_acceso():
    """Il difetto originale: spuntata e salvata, tornava spenta."""
    assert ai.usa_web() is False
    _salva(web_presente="1", web="1")
    assert ai.usa_web() is True


def test_spegnere_il_web_lo_spegne():
    """La casella tolta non manda niente: è l'assenza a valere «spenta»."""
    ai.set_usa_web(True)
    _salva(web_presente="1")
    assert ai.usa_web() is False


def test_un_altro_modulo_non_tocca_il_web():
    """Senza il segnaposto la domanda non è stata posta: non si cambia niente.
    Serve perché alla stessa rotta arrivano anche moduli che del web non
    parlano — e quelli non devono spegnere l'agente per omissione."""
    ai.set_usa_web(True)
    _salva(vertex_project="qualcosa")
    assert ai.usa_web() is True


@pytest.mark.parametrize("valore", ["1", ""])
def test_modello_e_modalita_continuano_a_salvarsi(valore):
    """Il campo nuovo non deve aver spostato quelli che già funzionavano."""
    _salva(web_presente=valore, modello="gemini-prova", modalita=ai.MODES[0])
    assert ai.get_model() == "gemini-prova"
    assert ai.get_mode() == ai.MODES[0]
