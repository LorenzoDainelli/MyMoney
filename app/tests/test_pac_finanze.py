"""Il PAC riflesso in Finanze: trasferimento automatico + conto a saldo derivato.

Due regole da difendere:
1. ogni versamento PAC genera UN solo trasferimento (conto scelto -> "PAC
   investimenti"), che si aggiorna con la modifica e sparisce con l'eliminazione;
2. il saldo del conto PAC è quello VIVO del Portafoglio (versato + rivalutazione),
   e le oscillazioni NON diventano mai movimenti.
Nessuna rete: prezzi e vista del portafoglio sono stubbati.
"""
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
from shared import tempo          # che ora è: il fuso scelto, non l'orologio del PC
from portfolio.models import Position, Versamento
from finance.models import Wallet, Transaction, TIPO_TRASFERIMENTO
import shared.settings_store  # noqa: F401  (registra shared_settings, usata dal sync)
import shared.backup          # noqa: F401
import portfolio.service as pf_service
import portfolio.versamenti as versamenti
import finance.service as fin_service
from motore import engine_di_prova


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    import shared.db as db_mod
    for mod in (db_mod, pf_service, versamenti, fin_service):
        monkeypatch.setattr(mod, "SessionLocal", TestSession)
    monkeypatch.setattr(versamenti.market, "quotes_map", lambda: {})
    monkeypatch.setattr(versamenti, "_prezzo_eur_alla_data",
                        lambda p, data, qmap, oggi, ora="": (10.0, "test"))

    with TestSession() as db:
        db.add_all([
            Wallet(nome="Trade Republic", tipo="carta", ordine=0),
            Wallet(nome=fin_service.NOME_WALLET_PAC, tipo="investimento", ordine=1),
            Position(nome="Alpha", ticker="A", pct_target=50.0, ordine=0),
            Position(nome="Beta", ticker="B", pct_target=50.0, ordine=1),
        ])
        db.commit()
    yield TestSession


def _movimenti(Session):
    with Session() as db:
        return list(db.execute(select(Transaction).where(
            Transaction.deleted.is_(False))).scalars().all())


def test_pac_crea_un_solo_trasferimento(test_db):
    Session = test_db
    vid = versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set())

    movs = _movimenti(Session)
    assert len(movs) == 1
    t = movs[0]
    assert t.tipo == TIPO_TRASFERIMENTO and t.importo == 100.0
    src = fin_service.wallet_per_nome("Trade Republic")
    dest = fin_service.wallet_per_nome(fin_service.NOME_WALLET_PAC)
    assert (t.wallet_id, t.wallet_to_id) == (src.id, dest.id)
    with Session() as db:
        assert db.get(Versamento, vid).tx_id == t.id


def test_modifica_aggiorna_lo_stesso_movimento(test_db):
    Session = test_db
    vid = versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set())
    tx_prima = _movimenti(Session)[0].id

    versamenti.salva(150.0, tempo.oggi(), "Trade Republic", esclusi=set(), vid=vid)
    movs = _movimenti(Session)
    assert len(movs) == 1                      # non si duplica
    assert movs[0].id == tx_prima and movs[0].importo == 150.0


def test_elimina_toglie_anche_il_movimento(test_db):
    Session = test_db
    vid = versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set())
    assert versamenti.elimina(vid) is True
    assert _movimenti(Session) == []


def test_conto_pac_ha_saldo_vivo_dal_portafoglio(test_db, monkeypatch):
    Session = test_db
    versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set())

    # il Portafoglio vale 100,42 € (mercato salito): il conto PAC deve seguirlo,
    # SENZA che nasca un movimento per i 42 centesimi.
    def finta_vista():
        with Session() as db:
            righe = [{"p": p} for p in db.execute(select(Position)).scalars().all()]
        return {"righe": righe, "totale": 100.42, "ha_totale": True}

    monkeypatch.setattr(pf_service, "vista_portafoglio", finta_vista)

    res = fin_service.saldi()
    pac = next(r for r in res["righe"]
               if r["w"].nome == fin_service.NOME_WALLET_PAC)
    assert pac["derivato"] is True
    assert (pac["saldo"], pac["versato"], pac["rivalutazione"]) == (100.42, 100.0, 0.42)
    # il trasferimento resta UNO: la rivalutazione non è un movimento
    assert len(_movimenti(Session)) == 1
    # e il conto di partenza è sceso di 100
    tr = next(r for r in res["righe"] if r["w"].nome == "Trade Republic")
    assert tr["saldo"] == -100.0


def test_senza_prezzi_il_saldo_resta_quello_dei_movimenti(test_db, monkeypatch):
    Session = test_db
    versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set())
    monkeypatch.setattr(pf_service, "vista_portafoglio",
                        lambda: {"righe": [], "totale": 0.0, "ha_totale": False})
    pac = next(r for r in fin_service.saldi()["righe"]
               if r["w"].nome == fin_service.NOME_WALLET_PAC)
    assert pac["saldo"] == 100.0 and "derivato" not in pac   # niente valori inventati


def _finta_vista(Session, totale=100.42):
    def vista():
        with Session() as db:
            righe = [{"p": p} for p in db.execute(select(Position)).scalars().all()]
        return {"righe": righe, "totale": totale, "ha_totale": True}
    return vista


def test_il_grafico_e_l_hero_partono_dalla_stessa_liquidita(test_db, monkeypatch):
    """Il numero grande della dashboard usa saldi()['liquido']; il grafico usa
    liquidita_alle_date(). Se le due non escludono le stesse cose, gli stessi
    euro risultano insieme fermi sul conto e già trasformati in titoli — ed è
    esattamente quello che succedeva: il grafico stava 100 € sopra l'hero."""
    from datetime import datetime, timedelta

    Session = test_db
    versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set())
    monkeypatch.setattr(pf_service, "vista_portafoglio", _finta_vista(Session))

    domani = tempo.adesso() + timedelta(days=1)
    assert fin_service.liquidita_alle_date([domani])[0] == fin_service.saldi()["liquido"]


def test_senza_prezzi_i_soldi_del_pac_non_spariscono_dal_grafico(test_db, monkeypatch):
    """Escludere il conto PAC è giusto solo se il suo valore vivo c'è. Senza
    prezzi il Portafoglio vale zero: il saldo dei movimenti è l'unica cosa vera
    che resta di quei soldi, toglierlo li farebbe sparire invece di spostarli."""
    from datetime import datetime, timedelta

    versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set())
    monkeypatch.setattr(pf_service, "vista_portafoglio",
                        lambda: {"righe": [], "totale": 0.0, "ha_totale": False})
    domani = tempo.adesso() + timedelta(days=1)
    assert fin_service.liquidita_alle_date([domani])[0] == 0.0   # -100 da TR, +100 sul PAC


def test_le_quote_valgono_solo_da_quando_le_hai(test_db):
    """L'altra metà del doppio conteggio: il grafico moltiplicava i prezzi di
    ieri per la quantità di OGGI, quindi mostrava i titoli come già tuoi nei
    giorni in cui quei soldi erano ancora sul conto."""
    from portfolio.wealth import _qta_a

    versamenti.salva(100.0, date(2026, 7, 16), "Trade Republic", esclusi=set())
    with test_db() as db:
        pid = db.execute(select(Position)).scalars().first().id

    base, gradini = versamenti.storico_quantita()[pid]
    assert base == 0.0                       # tutto spiegato dal versamento
    assert len(gradini) == 1
    quando, quante = gradini[0]
    assert quante > 0
    assert _qta_a(base, gradini, quando - 1) == 0.0
    assert _qta_a(base, gradini, quando + 1) == quante


def test_la_quantita_messa_a_mano_si_considera_di_sempre(test_db):
    """Se una quantità non viene da nessun versamento non ne conosciamo la data:
    inventargliene una sarebbe peggio che dichiararla posseduta da sempre."""
    with test_db() as db:
        p = db.execute(select(Position)).scalars().first()
        p.quantita = 7.0
        pid = p.id
        db.commit()
    base, gradini = versamenti.storico_quantita()[pid]
    assert (base, gradini) == (7.0, [])


def test_niente_doppio_conteggio_nel_patrimonio(test_db, monkeypatch):
    """Il conto PAC vale quanto il Portafoglio: nel patrimonio va contato UNA volta.
    'liquido' è la liquidità vera (senza i conti derivati)."""
    Session = test_db
    versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set())

    def finta_vista():
        with Session() as db:
            righe = [{"p": p} for p in db.execute(select(Position)).scalars().all()]
        return {"righe": righe, "totale": 100.42, "ha_totale": True}

    monkeypatch.setattr(pf_service, "vista_portafoglio", finta_vista)
    res = fin_service.saldi()
    assert res["liquido"] == -100.0             # i 100 usciti da TR
    assert res["totale"] == 0.42                # liquidità + valore investito
    # patrimonio come lo calcola la dashboard: liquido + portafoglio, non totale
    assert round(res["liquido"] + 100.42, 2) == 0.42


def test_il_trasferimento_parte_col_primo_ordine_eseguito(test_db):
    """Il movimento in Finanze è UNO, ma i titoli vengono eseguiti a ore
    diverse. Senza un'ora del versamento, i soldi hanno lasciato il conto
    quando è partito il primo ordine: è l'unico istante che i dati conoscono.
    Inventarne un altro (mezzanotte) metterebbe il trasferimento in un momento
    in cui non era ancora successo niente."""
    Session = test_db
    with Session() as db:
        ids = {p.ticker: p.id for p in db.execute(select(Position)).scalars()}

    versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set(),
                     orari={ids["A"]: "17:40", ids["B"]: "09:12"})
    t = _movimenti(Session)[0]
    assert (t.data.hour, t.data.minute) == (9, 12)


def test_l_ora_del_versamento_batte_quella_dei_titoli(test_db):
    """Se l'ora del versamento c'è, comanda lei: è quella che hai scritto tu
    pensando al bonifico, non una dedotta dagli ordini."""
    Session = test_db
    with Session() as db:
        ids = {p.ticker: p.id for p in db.execute(select(Position)).scalars()}

    versamenti.salva(100.0, tempo.oggi(), "Trade Republic", esclusi=set(),
                     ora="08:00", orari={ids["A"]: "17:40"})
    t = _movimenti(Session)[0]
    assert (t.data.hour, t.data.minute) == (8, 0)
