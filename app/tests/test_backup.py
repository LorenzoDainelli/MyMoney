"""Il backup: portarsi via i dati, e rimetterli dentro.

Prima della Fase 5 questa rete di sicurezza era implicita: i dati erano un file
sul PC, e il file lo copiavi. Ora stanno in un database in una regione di Google,
e l'unico modo di averne una copia tua è il tasto in Impostazioni. Se quel giro
perde qualcosa, non se ne accorge nessuno finché non serve — cioè nel giorno
peggiore.

Quindi si controlla il giro COMPLETO, non le due metà separate: fotografia →
sostituzione totale → gli stessi dati di prima. E si controllano le tre cose che
un giro del genere perde tipicamente:
- i **legami** fra un movimento e le righe che ha generato (arrotondamento,
  saveback), che viaggiano per uid e non per id;
- i **decimali** del saveback, che sono quattro e non due;
- le **lapidi**, cioè i movimenti cancellati: se sparissero dalla fotografia, un
  ripristino resusciterebbe quello che avevi tolto.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared.backup as backup
from shared.db import SessionLocal
from finance.models import Wallet, Category, Transaction
from finance.service import ORIGINE_SAVEBACK


def _popola():
    """Un piccolo mondo realistico: due conti, una categoria, una spesa con la
    sua riga di saveback attaccata, e un movimento cancellato."""
    with SessionLocal() as db:
        tr = Wallet(nome="Trade Republic", tipo="conto", saldo_iniziale=1000.0,
                    colore="#A6DA47", ordine=1)
        hype = Wallet(nome="Hype", tipo="conto", saldo_iniziale=50.0, ordine=2)
        cat = Category(nome="Svago", kind="uscita")
        db.add_all([tr, hype, cat])
        db.commit()

        spesa = Transaction(tipo="uscita", data=datetime(2026, 8, 8, 13, 28),
                            importo=40.45, wallet_id=tr.id, category_id=cat.id,
                            descrizione="Cinema")
        db.add(spesa)
        db.commit()

        # la riga generata: 1% ESATTO della spesa, quattro decimali
        saveback = Transaction(tipo="entrata", data=datetime(2026, 8, 8, 13, 28),
                               importo=0.4045, wallet_id=tr.id,
                               origine=ORIGINE_SAVEBACK, parent_tx_id=spesa.id,
                               descrizione="Saveback")
        morto = Transaction(tipo="uscita", data=datetime(2026, 8, 1, 9, 0),
                            importo=99.0, wallet_id=hype.id,
                            descrizione="Sbagliato", deleted=True)
        db.add_all([saveback, morto])
        db.commit()
        return {"spesa_uid": spesa.uid, "saveback_uid": saveback.uid,
                "morto_uid": morto.uid}


def _conta():
    with SessionLocal() as db:
        return (db.query(Wallet).count(), db.query(Category).count(),
                db.query(Transaction).count())


# ── la fotografia ───────────────────────────────────────────────────────────

def test_la_fotografia_prende_tutto():
    uids = _popola()
    snap = backup.build_snapshot()

    assert len(snap["wallets"]) == 2
    assert len(snap["categorie"]) == 1
    assert len(snap["movimenti"]) == 3
    assert snap["schema"] == backup.SCHEMA_VERSION


def test_i_riferimenti_sono_uid_e_mai_id_interni():
    """Un id interno dentro un backup punterebbe a righe che su un database
    vuoto non esistono: il ripristino ricostruirebbe legami a caso."""
    _popola()
    snap = backup.build_snapshot()
    for m in snap["movimenti"]:
        assert "wallet_id" not in m and "category_id" not in m
        assert "parent_tx_id" not in m
    con_padre = [m for m in snap["movimenti"] if m["parent_uid"]]
    assert len(con_padre) == 1, "il legame col movimento padre non è nella fotografia"


def test_le_lapidi_restano_nella_fotografia():
    """Un movimento cancellato è una lapide con la sua data. Buttarla vorrebbe
    dire che un ripristino ti riporta indietro anche gli errori che avevi tolto."""
    uids = _popola()
    snap = backup.build_snapshot()
    morti = [m for m in snap["movimenti"] if m["deleted"]]
    assert [m["uid"] for m in morti] == [uids["morto_uid"]]


# ── il giro completo ────────────────────────────────────────────────────────

def test_il_giro_completo_riporta_esattamente_gli_stessi_dati():
    uids = _popola()
    snap = backup.build_snapshot()
    prima = _conta()

    # sporca il database con roba diversa, come se fosse un'altra installazione
    with SessionLocal() as db:
        db.add(Wallet(nome="Robaccia", tipo="conto", saldo_iniziale=7.0))
        db.commit()
    assert _conta() != prima

    esito = backup.replace_all_from_snapshot(snap)
    assert esito["ok"] is True
    assert esito["count"] == 3
    assert _conta() == prima

    with SessionLocal() as db:
        nomi = {w.nome for w in db.query(Wallet).all()}
        assert nomi == {"Trade Republic", "Hype"}, "la robaccia è sopravvissuta"
        tr = db.query(Wallet).filter_by(nome="Trade Republic").one()
        assert tr.colore == "#A6DA47" and tr.saldo_iniziale == 1000.0


def test_il_saveback_torna_con_tutti_i_suoi_quattro_decimali():
    """0,4045 e non 0,40. Sono i decimillesimi dell'1% esatto: arrotondarli qui
    li cancellerebbe per sempre, e in silenzio."""
    _popola()
    snap = backup.build_snapshot()
    backup.replace_all_from_snapshot(snap)

    with SessionLocal() as db:
        sb = db.query(Transaction).filter_by(origine=ORIGINE_SAVEBACK).one()
        assert sb.importo == 0.4045


def test_la_riga_generata_resta_legata_alla_sua_spesa():
    """Se il legame si perde, il saveback rinasce come movimento a sé: si vede
    due volte nel registro e non se ne va più insieme alla spesa."""
    _popola()
    snap = backup.build_snapshot()
    backup.replace_all_from_snapshot(snap)

    with SessionLocal() as db:
        spesa = db.query(Transaction).filter_by(descrizione="Cinema").one()
        sb = db.query(Transaction).filter_by(origine=ORIGINE_SAVEBACK).one()
        assert sb.parent_tx_id == spesa.id


def test_la_figlia_si_lega_anche_se_nel_file_viene_prima_del_genitore():
    """L'ordine dentro il file non è garantito. Una figlia che arriva prima del
    genitore non ha ancora un id da puntare: si collega in seconda passata."""
    _popola()
    snap = backup.build_snapshot()
    snap["movimenti"].sort(key=lambda m: 0 if m["parent_uid"] else 1)
    assert snap["movimenti"][0]["parent_uid"], "la prova non sta provando niente"

    backup.replace_all_from_snapshot(snap)
    with SessionLocal() as db:
        spesa = db.query(Transaction).filter_by(descrizione="Cinema").one()
        sb = db.query(Transaction).filter_by(origine=ORIGINE_SAVEBACK).one()
        assert sb.parent_tx_id == spesa.id


def test_il_ripristino_non_ritimbra_le_date_di_modifica():
    """Ritimbrarle direbbe che ogni movimento è stato toccato oggi, e la prima
    cosa che si guarda dopo un ripristino è proprio «cos'è cambiato»."""
    _popola()
    snap = backup.build_snapshot()
    quando = {m["uid"]: m["updated_at"] for m in snap["movimenti"]}
    revisioni = {m["uid"]: m["rev"] for m in snap["movimenti"]}

    backup.replace_all_from_snapshot(snap)
    with SessionLocal() as db:
        for t in db.query(Transaction).all():
            assert t.updated_at.isoformat() == quando[t.uid]
            assert t.rev == revisioni[t.uid]


# ── i rifiuti ───────────────────────────────────────────────────────────────

def test_un_file_di_una_versione_futura_non_distrugge_niente():
    """Se il file capisce più cose di quante ne capisca l'app, applicarlo a metà
    è peggio che non applicarlo: si perde ciò che l'app non sa leggere."""
    _popola()
    prima = _conta()
    esito = backup.replace_all_from_snapshot({"schema": 999, "wallets": [],
                                              "categorie": [], "movimenti": []})
    assert esito.get("future") == 1
    assert esito.get("ok") is False
    assert _conta() == prima, "ha svuotato il database rifiutando il file"


def test_legge_anche_i_file_del_vecchio_formato():
    """Prima della Fase 5 il file era un «pacchetto»: la fotografia stava sotto
    la chiave `snapshot`, insieme al diario. Quei file sono ancora sul disco di
    Lorenzo — una rete di sicurezza che non li leggesse avrebbe un buco."""
    _popola()
    snap = backup.build_snapshot()
    prima = _conta()
    vecchio = {"schema": 1, "type": "bundle", "snapshot": snap, "diary": []}

    esito = backup.replace_all_from_snapshot(vecchio)
    assert esito["ok"] is True
    assert _conta() == prima
