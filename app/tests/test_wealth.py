"""Il grafico del patrimonio: la somma di serie che non hanno le stesse date.

Ogni titolo torna da Yahoo con la sua griglia di timestamp (borse diverse,
festivi diversi, buchi diversi). Sommarle richiede di decidere cosa vale un
titolo in un istante in cui non ha una chiusura — e la risposta giusta è
«l'ultima nota», non zero: uno zero farebbe crollare il grafico ogni volta che
una borsa è chiusa, disegnando un tracollo che non è mai successo.

Qui si difendono quella regola e il campionamento. Niente rete.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import portfolio.wealth as W


def test_le_serie_si_sommano_sulla_griglia_unione():
    a = [(1, 10.0), (3, 20.0)]
    b = [(2, 5.0)]
    tot = W._grid_totale([a, b], extra_flat=0.0)
    assert [t for t, _ in tot] == [1, 2, 3]


def test_fra_due_chiusure_vale_l_ultima_nota_non_zero():
    """La regola che evita i tracolli finti quando una borsa è chiusa."""
    a = [(1, 10.0), (5, 12.0)]     # non ha punti a 2, 3, 4
    b = [(2, 100.0), (3, 100.0)]
    tot = dict(W._grid_totale([a, b], extra_flat=0.0))
    assert tot[2] == 110.0         # A vale ancora 10, non 0
    assert tot[3] == 110.0
    assert tot[5] == 112.0


def test_prima_del_primo_punto_si_usa_il_primo_valore():
    """Un titolo con storia più corta non deve valere zero all'inizio del
    grafico: varrebbe un patrimonio più basso di quello vero."""
    lungo = [(1, 50.0), (2, 50.0)]
    corto = [(2, 30.0)]
    tot = dict(W._grid_totale([lungo, corto], extra_flat=0.0))
    assert tot[1] == 80.0


def test_i_titoli_senza_storia_entrano_come_valore_costante():
    tot = W._grid_totale([[(1, 10.0), (2, 10.0)]], extra_flat=7.0)
    assert [v for _, v in tot] == [17.0, 17.0]


def test_senza_nessuna_serie_non_si_inventa_una_griglia():
    assert W._grid_totale([], extra_flat=99.0) == []


def test_il_campionamento_tiene_i_capi_della_serie():
    """Il downsample serve a non spedire 3000 punti alla pagina, ma il primo e
    l'ultimo valore decidono la percentuale mostrata: non si possono perdere."""
    punti = [(i, float(i)) for i in range(1000)]
    ridotti = W._downsample(punti)
    assert len(ridotti) == W.MAX_POINTS
    assert ridotti[0] == punti[0]
    assert ridotti[-1] == punti[-1]
    assert [t for t, _ in ridotti] == sorted(t for t, _ in ridotti)


def test_una_serie_corta_non_viene_toccata():
    punti = [(1, 1.0), (2, 2.0)]
    assert W._downsample(punti) == punti
