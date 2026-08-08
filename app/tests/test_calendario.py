"""Il calendario delle spese (Finanze): un quadratino per giorno del mese.

Due cose da difendere:
1. il calendario e il riquadro "Uscite del mese" devono raccontare lo stesso
   mese — stessa definizione di uscita, partite di giro comprese;
2. l'intensità va per RANGO, non in proporzione al massimo: con una spesa
   grossa e il resto piccolo, una scala lineare accende un quadrato e spegne
   tutti gli altri — vero, e illeggibile.

Niente rete.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
from finance.models import Wallet, Category, Transaction, TIPO_USCITA, TIPO_ENTRATA
import shared.settings_store  # noqa: F401
import shared.sync            # noqa: F401
import finance.service as fin
from motore import engine_di_prova


ANNO, MESE = 2026, 7


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    import shared.db as db_mod
    for mod in (db_mod, fin):
        monkeypatch.setattr(mod, "SessionLocal", TestSession)
    with TestSession() as db:
        db.add(Wallet(id=1, nome="Contanti", tipo="contanti", saldo_iniziale=0.0))
        db.add(Category(id=1, nome="Spesa"))
        db.commit()
    yield TestSession


def _uscita(db, giorno, importo):
    db.add(Transaction(tipo=TIPO_USCITA, data=datetime(ANNO, MESE, giorno, 12, 0),
                       importo=importo, wallet_id=1, category_id=1))


def test_ogni_giorno_del_mese_ha_la_sua_casella(test_db):
    cal = fin.calendario_spese(ANNO, MESE)
    assert len(cal["giorni"]) == 31                 # luglio
    assert [g["g"] for g in cal["giorni"]] == list(range(1, 32))
    # 1 luglio 2026 è un mercoledì: due caselle vuote prima (lun, mar)
    assert cal["vuote_prima"] == 2


def test_le_spese_dello_stesso_giorno_si_sommano(test_db):
    with test_db() as db:
        _uscita(db, 10, 20.0)
        _uscita(db, 10, 5.5)
        db.commit()
    cal = fin.calendario_spese(ANNO, MESE)
    assert cal["giorni"][9]["tot"] == 25.5
    assert cal["con_spesa"] == 1


def test_le_entrate_non_colorano_il_calendario(test_db):
    """È un calendario delle USCITE: uno stipendio non è un giorno speso."""
    with test_db() as db:
        db.add(Transaction(tipo=TIPO_ENTRATA, data=datetime(ANNO, MESE, 5, 9, 0),
                           importo=1500.0, wallet_id=1))
        db.commit()
    cal = fin.calendario_spese(ANNO, MESE)
    assert cal["con_spesa"] == 0
    assert all(g["liv"] == 0 for g in cal["giorni"])


def test_l_intensita_va_per_rango_non_in_proporzione_al_massimo(test_db):
    """Il caso vero: un regalo da 75 € e quattro spese sotto i 20. In scala
    lineare i piccoli finirebbero tutti nello stesso livello più basso."""
    with test_db() as db:
        for giorno, importo in [(2, 5.0), (4, 10.0), (6, 15.0), (8, 20.0), (21, 75.0)]:
            _uscita(db, giorno, importo)
        db.commit()
    liv = {g["g"]: g["liv"] for g in fin.calendario_spese(ANNO, MESE)["giorni"]}
    assert liv[2] == 1 and liv[21] == 4           # i due estremi
    assert liv[1] == 0                            # giorno senza spese
    # cinque importi su quattro livelli: qualche pareggio è inevitabile, ma la
    # scala non deve mai invertirsi e i piccoli non devono finire tutti insieme
    ordinati = [liv[g] for g in (2, 4, 6, 8, 21)]
    assert ordinati == sorted(ordinati)
    assert len(set(ordinati)) == 4
    # la prova del nove: in scala lineare sul massimo, 15 e 20 su 75 starebbero
    # entrambi nel livello più basso. Qui no.
    assert liv[6] > 1 and liv[8] > liv[6]


def test_i_giorni_non_ancora_arrivati_non_sono_giorni_senza_spese(monkeypatch, test_db):
    """Marcarli come 'zero speso' racconterebbe un mese finito che non è finito.

    «Adesso» si finge da shared/tempo: l'app non guarda più l'orologio della
    macchina ma il fuso scelto, e un test che sostituisce `datetime` non
    intercetterebbe più niente — passerebbe per finta."""
    monkeypatch.setattr(fin.tempo, "adesso", lambda: datetime(ANNO, MESE, 15, 18, 0))
    cal = fin.calendario_spese(ANNO, MESE)
    assert [g["g"] for g in cal["giorni"] if g["futuro"]] == list(range(16, 32))
    assert [g["g"] for g in cal["giorni"] if g["oggi"]] == [15]


def test_un_altro_mese_non_ha_un_oggi(monkeypatch, test_db):
    monkeypatch.setattr(fin.tempo, "adesso", lambda: datetime(ANNO, 9, 3, 18, 0))
    cal = fin.calendario_spese(ANNO, MESE)
    assert not any(g["oggi"] or g["futuro"] for g in cal["giorni"])


def test_il_calendario_e_le_uscite_del_mese_dicono_lo_stesso_numero(test_db):
    """I due riquadri stanno nella stessa pagina: se non tornano, uno mente."""
    with test_db() as db:
        for giorno, importo in [(3, 12.0), (3, 8.0), (17, 44.5), (28, 9.99)]:
            _uscita(db, giorno, importo)
        db.commit()
    cal = fin.calendario_spese(ANNO, MESE)
    riep = fin.riepilogo_mese(ANNO, MESE)
    assert round(sum(g["tot"] for g in cal["giorni"]), 2) == riep["uscite"]


def test_una_partita_di_giro_chiusa_conta_per_il_netto(test_db):
    """Come nel riepilogo: 100 spesi e 60 rientrati sono 40 di uscita, alla
    data dell'ultima spesa — non 100."""
    fin.crea_giro(
        spese=[{"importo": 100.0, "wallet_id": 1, "categoria": "", "descrizione": "",
                "data": datetime(ANNO, MESE, 12, 10, 0)}],
        rientri=[{"importo": 60.0, "wallet_id": 1, "controparte": "Tizio",
                  "data": datetime(ANNO, MESE, 19, 10, 0)}],
    )
    cal = fin.calendario_spese(ANNO, MESE)
    tot = {g["g"]: g["tot"] for g in cal["giorni"]}
    assert tot[12] == 40.0
    assert tot[19] == 0.0
    assert round(sum(tot.values()), 2) == fin.riepilogo_mese(ANNO, MESE)["uscite"]
