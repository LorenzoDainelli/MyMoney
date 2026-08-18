"""Un pagamento che la banca autorizza e poi storna.

Il caso vero (13/07/2026): una partita di giro da 29,00 € su Trade Republic,
addebitata 30,00 € per via dell'arrotondamento della carta. Il pagamento è stato
annullato dalla banca, che ha restituito anche l'euro dell'arrotondamento e il
saveback. L'app però continuava a contarli, e da lì venivano l'euro di scarto sul
conto Trade Republic e l'1,29 € di troppo nel salvadanaio.

Cancellare la riga non era la risposta: per qualche giorno quei soldi sono usciti
davvero, e senza la riga non si spiegherebbe più. Annullare fa le due cose
insieme — resta scritto che è successo, i conti fanno finta di niente.

Quello che si difende qui:
  · il saldo del portafoglio torna com'era prima del movimento;
  · le righe generate (arrotondamento, saveback) se ne vanno insieme al padre;
  · annullando UNA gamba di una partita si annulla la partita intera;
  · il movimento resta nel registro (barrato), non sparisce;
  · si può tornare indietro, e i numeri tornano quelli di prima.

Niente rete.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
from finance.models import Wallet, Transaction
import shared.settings_store  # noqa: F401
import shared.backup          # noqa: F401
import finance.service as fin
from motore import engine_di_prova


ANNO, MESE = 2026, 7
QUANDO = datetime(ANNO, MESE, 13, 9, 4)


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    import shared.db as db_mod
    for mod in (db_mod, fin):
        monkeypatch.setattr(mod, "SessionLocal", TestSession)
    with TestSession() as db:
        db.add(Wallet(id=1, nome="Trade Republic", tipo="carta", saldo_iniziale=1000.0,
                      arrotonda=True, saveback_pct=1.0, saveback_tetto=15.0))
        db.add(Wallet(id=2, nome=fin.NOME_WALLET_NASCOSTI, tipo="altro", saldo_iniziale=0.0))
        db.add(Wallet(id=3, nome="Hype", tipo="carta", saldo_iniziale=0.0))
        db.commit()
    yield TestSession


def _saldo(wid):
    with fin.SessionLocal() as db:
        return round(fin._saldi_map(db).get(wid, 0.0), 2)


def _giro_franchigia():
    """La partita del 13/07: 29,00 € su TR, che la carta addebita 30,00 €."""
    return fin.crea_giro(spese=[{"importo": 29.00, "wallet_id": 1,
                                 "categoria": "Franchigia", "data": QUANDO}],
                         aperta=True)


def _id_gamba(gid):
    """L'id della gamba SPESA della partita (quella che si apre dal registro)."""
    with fin.SessionLocal() as db:
        for t in db.query(Transaction).filter(
                Transaction.giro_id == gid,
                Transaction.parent_tx_id.is_(None)).order_by(Transaction.id).all():
            if t.giro_kind in ("spesa", "combo"):
                return t.id
    raise AssertionError("partita senza gamba di spesa")


# --------------------------------------------------------------------------
def test_addebito_reale_prima_di_annullare():
    """Prima di tutto: la partita deve costare 30,00 €, non 29,00.
    Se questo non fosse vero, il resto del file starebbe misurando altro."""
    _giro_franchigia()
    # dal conto escono 29,00 di spesa + 1,00 di arrotondamento = 30,00 tondi
    assert _saldo(1) == 970.00
    # nel salvadanaio entrano l'arrotondamento (1,00) e il saveback (0,29):
    # il saveback è denaro della banca, non passa dal conto
    assert _saldo(2) == 1.29


def test_annullare_restituisce_tutto():
    gid = _giro_franchigia()
    fin.annulla_movimento(_id_gamba(gid))
    assert _saldo(1) == 1000.00       # il conto torna esattamente com'era
    assert _saldo(2) == 0.00          # e il salvadanaio pure


def test_le_figlie_seguono_il_padre():
    """L'euro di arrotondamento e i 29 centesimi di saveback sono righe a sé:
    se restassero buone, il salvadanaio continuerebbe a dire 1,29 € di troppo —
    che è esattamente il sintomo da cui è nata questa funzione."""
    gid = _giro_franchigia()
    fin.annulla_movimento(_id_gamba(gid))
    with fin.SessionLocal() as db:
        figlie = db.query(Transaction).filter(Transaction.parent_tx_id.is_not(None)).all()
        assert figlie, "la partita deve aver generato delle righe"
        assert all(f.annullato for f in figlie)


def test_la_partita_si_annulla_intera():
    """Mezza partita annullata sarebbe un rimborso in attesa di una spesa che non
    c'è più."""
    gid = fin.crea_giro(
        spese=[{"importo": 29.00, "wallet_id": 1, "categoria": "Franchigia", "data": QUANDO}],
        rientri=[{"importo": 29.00, "wallet_id": 3, "controparte": "babbo", "data": QUANDO}])
    fin.annulla_movimento(_id_gamba(gid))
    with fin.SessionLocal() as db:
        gambe = db.query(Transaction).filter(Transaction.giro_id == gid).all()
        assert len(gambe) == 2
        assert all(g.annullato for g in gambe)
    assert _saldo(3) == 0.00          # il rimborso non è mai arrivato


def test_resta_nel_registro_barrato():
    """Annullare non è cancellare: la riga si deve ancora vedere, altrimenti non
    spiega più perché il conto quei giorni era più basso."""
    gid = _giro_franchigia()
    fin.annulla_movimento(_id_gamba(gid))
    righe = fin.lista_movimenti()
    assert len(righe) == 1
    assert righe[0]["t"].annullato is True


def test_fuori_dalle_statistiche_del_mese():
    fin.crea_movimento(fin.TIPO_USCITA, QUANDO, 50.0, 1, categoria_nome="Spesa")
    tid = fin.crea_movimento(fin.TIPO_USCITA, QUANDO, 80.0, 1, categoria_nome="Spesa")
    fin.annulla_movimento(tid)
    riep = fin.riepilogo_mese(ANNO, MESE)
    assert round(riep["uscite"], 2) == 50.00
    top = fin.spesa_top(ANNO, MESE)
    assert top is not None and round(top["importo"], 2) == 50.00
    per_cat = fin.uscite_per_categoria_mese(ANNO, MESE)
    assert per_cat["spesa"]["tot"] == 50.00
    assert per_cat["spesa"]["n"] == 1


def test_il_tetto_del_saveback_non_si_consuma():
    """Un pagamento stornato non deve rubare spazio sotto il tetto dei 15 €/mese:
    la banca quel saveback se l'è ripreso."""
    tid = fin.crea_uscita_carta(data=QUANDO, importo=100.0, wallet_id=1,
                                categoria_nome="Spesa")
    assert fin.saveback_maturato(ANNO, MESE) == 1.0
    fin.annulla_movimento(tid)
    assert fin.saveback_maturato(ANNO, MESE) == 0.0


def test_si_torna_indietro():
    gid = _giro_franchigia()
    tid = _id_gamba(gid)
    fin.annulla_movimento(tid)
    assert _saldo(1) == 1000.00
    fin.annulla_movimento(tid, si=False)
    assert _saldo(1) == 970.00
    assert _saldo(2) == 1.29


def test_movimento_inesistente():
    assert fin.annulla_movimento(999999) is False


def test_il_sync_se_lo_porta_dietro():
    """Se il campo non viaggiasse, il telefono e il PC direbbero due saldi
    diversi senza che si capisca perché."""
    import shared.backup as bk
    gid = _giro_franchigia()
    fin.annulla_movimento(_id_gamba(gid))
    with fin.SessionLocal() as db:
        for t in db.query(Transaction).all():
            assert bk._transaction_to_fields(t, db)["annullato"] is True

# --------------------------------------------------------------------------
def test_il_caso_del_13_luglio():
    """La ricostruzione fedele di quello che è successo davvero.

    Una partita di giro chiusa: 29,00 € pagati con la carta Trade Republic e
    29,00 € rientrati sullo stesso conto. Sul conto la partita si annulla da
    sola — esce e rientra la stessa cifra — e l'unico segno che lascia è l'euro
    dell'arrotondamento, finito nel salvadanaio insieme ai 29 centesimi di
    saveback. Poi la banca ha stornato il pagamento e ha restituito tutto.

    Da qui i due numeri che non tornavano: 1,00 € di troppo sul conto Trade
    Republic e 1,29 € di troppo nel salvadanaio. Annullando la partita spariscono
    tutti e due, e non è una coincidenza: sono lo stesso euro visto da due parti.
    """
    prima_tr, prima_nascosti = _saldo(1), _saldo(2)
    gid = fin.crea_giro(
        spese=[{"importo": 29.00, "wallet_id": 1, "categoria": "Franchigia", "data": QUANDO}],
        rientri=[{"importo": 29.00, "wallet_id": 1, "controparte": "banca", "data": QUANDO}])

    # com'era l'app prima di annullare: il conto sotto di 1,00, il salvadanaio
    # sopra di 1,29 — esattamente lo scarto misurato sull'estratto conto
    assert _saldo(1) == round(prima_tr - 1.00, 2)
    assert _saldo(2) == round(prima_nascosti + 1.29, 2)

    fin.annulla_movimento(_id_gamba(gid))

    assert _saldo(1) == prima_tr
    assert _saldo(2) == prima_nascosti
