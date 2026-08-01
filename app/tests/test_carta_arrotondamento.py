"""La carta che arrotonda: una spesa, tre fatti diversi.

Paghi 7,60 € alla Coop con la carta Trade Republic e succedono tre cose che NON
sono la stessa cosa:
  · 7,60 € di consumo          -> uscita        (non sono più tuoi)
  · 0,40 € di arrotondamento   -> trasferimento (tuoi, cambiano tasca)
  · 0,07 € di saveback         -> entrata       (della banca, prima non c'erano)
Dal conto escono 8,00 €, ma di spesa ne hai fatta 7,60. Segnare 8,00 falserebbe
i consumi; segnare 7,60 lascerebbe il conto scoperto di 40 centesimi. Qui si
difende il fatto che tornino ENTRAMBE le cose, più le regole della carta
verificate dall'utente il 30/07/2026 (prossimo euro anche sulle cifre tonde,
1% troncato ai centesimi, tetto di 15 €/mese).

Niente rete.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
from finance.models import (Wallet, Transaction,
                            TIPO_USCITA, TIPO_ENTRATA, TIPO_TRASFERIMENTO)
import shared.settings_store  # noqa: F401
import shared.sync            # noqa: F401
import finance.service as fin


ANNO, MESE = 2026, 7
QUANDO = datetime(ANNO, MESE, 30, 12, 15)


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'test.db'}",
                           connect_args={"check_same_thread": False})
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    import shared.db as db_mod
    for mod in (db_mod, fin):
        monkeypatch.setattr(mod, "SessionLocal", TestSession)
    with TestSession() as db:
        db.add(Wallet(id=1, nome="Trade Republic", tipo="carta", saldo_iniziale=100.0,
                      arrotonda=True, saveback_pct=1.0, saveback_tetto=15.0))
        db.add(Wallet(id=2, nome=fin.NOME_WALLET_NASCOSTI, tipo="altro",
                      saldo_iniziale=0.0))
        db.add(Wallet(id=3, nome="Hype", tipo="carta", saldo_iniziale=0.0))
        db.commit()
    yield TestSession


def _spesa(importo=7.60, **kw):
    return fin.crea_uscita_carta(data=QUANDO, importo=importo, wallet_id=1,
                                 categoria_nome="Spesa", **kw)


# --------------------------- le regole della carta ---------------------------
def test_arrotonda_al_prossimo_euro():
    assert fin.arrotondamento(7.60) == 0.40
    assert fin.arrotondamento(0.01) == 0.99
    assert fin.arrotondamento(12.34) == 0.66


def test_anche_una_cifra_tonda_sale_al_prossimo_euro():
    """Verificato dall'utente: 8,00 € diventano 9,00. Non è math.ceil, che su una
    cifra tonda non farebbe niente e lascerebbe il conto scoperto di 1 €."""
    assert fin.arrotondamento(8.0) == 1.0
    assert fin.arrotondamento(1.0) == 1.0


def test_niente_arrotondamento_su_importo_nullo():
    assert fin.arrotondamento(0) == 0.0
    assert fin.arrotondamento(None) == 0.0


def test_il_saveback_e_troncato_non_arrotondato():
    """7,60 × 1% = 0,076 e la banca accredita 0,07. Arrotondando si darebbe 0,08:
    un centesimo che non arriverà mai, ripetuto a ogni spesa."""
    assert fin.saveback_dovuto(7.60, 1.0) == 0.07
    assert fin.saveback_dovuto(12.50, 1.0) == 0.12       # 0,125 -> 0,12
    assert fin.saveback_dovuto(3.00, 1.0) == 0.03        # niente errori di virgola
    assert fin.saveback_dovuto(0.99, 1.0) == 0.0         # meno di un centesimo


def test_il_saveback_si_ferma_al_tetto_del_mese():
    assert fin.saveback_dovuto(100.0, 1.0, tetto=15.0, gia_maturato=14.60) == 0.40
    assert fin.saveback_dovuto(100.0, 1.0, tetto=15.0, gia_maturato=15.0) == 0.0
    # senza tetto non si ferma
    assert fin.saveback_dovuto(100.0, 1.0, tetto=0.0, gia_maturato=99.0) == 1.0


def test_il_tetto_gia_maturato_si_legge_dai_movimenti(test_db):
    _spesa(7.60)
    assert fin.saveback_maturato(ANNO, MESE) == 0.07
    _spesa(7.60)
    assert fin.saveback_maturato(ANNO, MESE) == 0.14


# --------------------------- le tre righe ---------------------------
def test_una_spesa_fa_tre_righe(test_db):
    tid = _spesa(7.60)
    with test_db() as db:
        righe = db.execute(select(Transaction).order_by(Transaction.id)).scalars().all()
        assert len(righe) == 3
        spesa, arr, sav = righe
        assert (spesa.tipo, spesa.importo, spesa.parent_tx_id) == (TIPO_USCITA, 7.60, None)
        assert (arr.tipo, arr.importo, arr.parent_tx_id) == (TIPO_TRASFERIMENTO, 0.40, tid)
        assert (arr.wallet_id, arr.wallet_to_id) == (1, 2)     # carta -> Nascosti
        assert (sav.tipo, sav.importo, sav.parent_tx_id) == (TIPO_ENTRATA, 0.07, tid)
        assert sav.wallet_id == 2                              # nasce già nel salvadanaio


def test_dal_conto_escono_8_euro_ma_la_spesa_e_760(test_db):
    """Le due cose che dovevano tornare insieme, e che a mano non tornavano mai."""
    _spesa(7.60)
    saldi = {r["w"].nome: r["saldo"] for r in fin.saldi()["righe"]}
    assert saldi["Trade Republic"] == 92.00        # 100 − 7,60 − 0,40
    assert saldi[fin.NOME_WALLET_NASCOSTI] == 0.47
    assert fin.riepilogo_mese(ANNO, MESE)["uscite"] == 7.60


def test_il_saveback_e_un_entrata_del_mese(test_db):
    """Se non lo fosse, il patrimonio crescerebbe di 7 centesimi che nessun
    movimento spiega."""
    _spesa(7.60)
    assert fin.riepilogo_mese(ANNO, MESE)["entrate"] == 0.07


def test_i_nascosti_contano_nel_patrimonio_ma_non_fra_i_liquidi(test_db):
    _spesa(7.60)
    s = fin.saldi()
    assert s["bloccato"] == 0.47
    assert s["liquido"] == 92.00                  # senza il salvadanaio
    assert s["totale"] == 92.47                   # col salvadanaio: sono soldi tuoi
    riga = next(r for r in s["righe"] if r["w"].nome == fin.NOME_WALLET_NASCOSTI)
    assert riga["bloccato"] is True


def test_il_patrimonio_scende_di_753_non_di_760(test_db):
    """Hai consumato 7,60 e ti hanno regalato 0,07."""
    prima = fin.saldi()["totale"]
    _spesa(7.60)
    assert round(fin.saldi()["totale"] - prima, 2) == -7.53


# --------------------------- quando NON deve scattare ---------------------------
def test_un_trasferimento_dalla_carta_non_arrotonda(test_db):
    """Il PAC da 100 € parte proprio da questa carta: non è un pagamento."""
    fin.crea_movimento(TIPO_TRASFERIMENTO, QUANDO, 100.0, 1, wallet_to_id=3)
    with test_db() as db:
        assert db.query(Transaction).count() == 1


def test_una_carta_senza_regole_non_genera_niente(test_db):
    fin.crea_movimento(TIPO_USCITA, QUANDO, 7.60, 3, categoria_nome="Spesa")
    with test_db() as db:
        assert db.query(Transaction).count() == 1
    assert fin.regole_carta(3) is None


def test_senza_salvadanaio_non_si_inventa_un_portafoglio(test_db):
    """Se cancelli i Nascosti, meglio nessuna riga generata che una riga finita
    in un posto a caso."""
    with test_db() as db:
        db.get(Wallet, 2).deleted = True
        db.commit()
    _spesa(7.60)
    with test_db() as db:
        assert db.query(Transaction).count() == 1


# --------------------------- il registro ---------------------------
def test_il_registro_mostra_una_riga_sola(test_db):
    _spesa(7.60)
    righe = fin.lista_movimenti()
    assert len(righe) == 1
    r = righe[0]
    assert r["t"].importo == 7.60          # il numero grande resta la spesa
    assert r["addebito"] == 8.00           # quello che vedi sull'estratto
    assert [f["t"].origine for f in r["figlie"]] == [fin.ORIGINE_ARROTONDAMENTO,
                                                     fin.ORIGINE_SAVEBACK]


def test_cancellare_la_spesa_porta_via_le_figlie(test_db):
    """Da sole non vorrebbero dire niente, e nel registro non si vedono nemmeno:
    resterebbero due movimenti fantasma impossibili da trovare."""
    tid = _spesa(7.60)
    fin.elimina_movimento(tid)
    assert fin.lista_movimenti() == []
    with test_db() as db:
        vive = db.query(Transaction).filter(Transaction.deleted.is_(False)).count()
        assert vive == 0
    assert fin.saldi()["totale"] == 100.0


# --------------------------- la modifica ---------------------------
def test_correggere_l_importo_rifa_i_conti(test_db):
    tid = _spesa(7.60)
    fin.aggiorna_movimento(tid, TIPO_USCITA, QUANDO, 7.90, 1, categoria_nome="Spesa")
    fin.risincronizza_figlie(tid, 7.60)
    imp = {f.origine: f.importo for f in fin.figlie(tid)}
    assert imp[fin.ORIGINE_ARROTONDAMENTO] == 0.10
    assert imp[fin.ORIGINE_SAVEBACK] == 0.07          # 7,90% -> 0,079 -> 0,07


def test_un_importo_corretto_a_mano_non_viene_riscritto(test_db):
    """Un numero che hai scritto tu vale più di uno calcolato: riscriverlo
    sarebbe disfare una tua decisione."""
    tid = _spesa(7.60, arr=0.90)                      # 0,90 al posto di 0,40
    fin.aggiorna_movimento(tid, TIPO_USCITA, QUANDO, 7.90, 1, categoria_nome="Spesa")
    fin.risincronizza_figlie(tid, 7.60)
    imp = {f.origine: f.importo for f in fin.figlie(tid)}
    assert imp[fin.ORIGINE_ARROTONDAMENTO] == 0.90    # invariato
    assert imp[fin.ORIGINE_SAVEBACK] == 0.07          # questo sì, era automatico


def test_puoi_azzerare_una_delle_due(test_db):
    tid = _spesa(7.60, sav=0.0)
    assert [f.origine for f in fin.figlie(tid)] == [fin.ORIGINE_ARROTONDAMENTO]


# --------------------------- la proposta ---------------------------
def test_la_proposta_tiene_conto_di_quanto_e_gia_maturato(test_db):
    with test_db() as db:
        db.get(Wallet, 1).saveback_tetto = 0.10       # tetto finto, basso
        db.commit()
    _spesa(7.60)                                      # matura 0,07
    e = fin.extra_carta(1, 7.60, QUANDO)
    assert e["saveback"] == 0.03                      # quello che manca a 0,10
    assert e["arrotondamento"] == 0.40


# --------------------------- dove è finito il mese ---------------------------
def _niente_pac(monkeypatch):
    """Il PAC vive nel Portafoglio, che qui non c'entra: lo azzero."""
    import portfolio.versamenti as v
    monkeypatch.setattr(v, "lista", lambda: [])


def test_i_soldi_accantonati_non_sono_rimasti_liquidi(test_db, monkeypatch):
    """Senza la voce «accantonato» quei 47 centesimi finirebbero in «rimasto»,
    cioè l'app direbbe che puoi spenderli. Non puoi: sono chiusi nel salvadanaio
    finché la banca non compra."""
    _niente_pac(monkeypatch)
    fin.crea_movimento(TIPO_ENTRATA, QUANDO, 1000.0, 1, categoria_nome="Paghetta")
    _spesa(7.60)

    d = fin.destinazioni_mese(ANNO, MESE)
    val = {v["key"]: v["val"] for v in d["voci"]}
    assert val["speso"] == 7.60
    assert val["accantonato"] == 0.47
    # le voci fanno ESATTAMENTE le entrate, saveback compreso
    assert round(sum(val.values()), 2) == d["entrate"] == 1000.07
    # ...e «rimasto» è davvero la liquidità in più del mese: 1000 − 7,60 − 0,40
    assert val["rimasto"] == 992.00


def test_senza_salvadanaio_la_voce_non_compare(test_db, monkeypatch):
    """Un «accantonato 0,00 €» fisso sarebbe rumore per chi non usa una carta
    che arrotonda."""
    _niente_pac(monkeypatch)
    fin.crea_movimento(TIPO_ENTRATA, QUANDO, 500.0, 1, categoria_nome="Paghetta")
    d = fin.destinazioni_mese(ANNO, MESE)
    assert "accantonato" not in [v["key"] for v in d["voci"]]
    assert fin.accantonato_mese(ANNO, MESE) == 0.0


def test_quando_la_banca_compra_il_salvadanaio_si_svuota(test_db, monkeypatch):
    """Il giorno dell'acquisto i soldi escono dai Nascosti: l'accantonato del
    mese torna a zero e il patrimonio non fa un salto."""
    _niente_pac(monkeypatch)
    fin.crea_movimento(TIPO_ENTRATA, QUANDO, 1000.0, 1, categoria_nome="Paghetta")
    _spesa(7.60)
    assert fin.accantonato_mese(ANNO, MESE) == 0.47

    # la banca investe: dal salvadanaio al conto degli investimenti (qui Hype)
    fin.crea_movimento(TIPO_TRASFERIMENTO, QUANDO, 0.47, 2, wallet_to_id=3)
    assert fin.accantonato_mese(ANNO, MESE) == 0.0
    assert fin.saldi()["bloccato"] == 0.0


# --------------------------- la modifica dal modulo ---------------------------
def test_in_modifica_valgono_i_numeri_che_hai_davanti(test_db):
    """Nel modulo di modifica i due importi si vedono: quello che leggi è quello
    che vale. Niente euristiche quando la decisione è sotto i tuoi occhi."""
    tid = _spesa(7.60)
    fin.imposta_figlie(tid, arr=0.90, sav=0.0)
    imp = {f.origine: f.importo for f in fin.figlie(tid)}
    assert imp == {fin.ORIGINE_ARROTONDAMENTO: 0.90}      # il saveback è sparito


def test_la_modifica_puo_riaggiungere_una_riga_tolta(test_db):
    tid = _spesa(7.60, sav=0.0)
    assert len(fin.figlie(tid)) == 1
    fin.imposta_figlie(tid, arr=0.40, sav=0.07)
    imp = {f.origine: f.importo for f in fin.figlie(tid)}
    assert imp == {fin.ORIGINE_ARROTONDAMENTO: 0.40, fin.ORIGINE_SAVEBACK: 0.07}


def test_il_modulo_di_modifica_ripropone_gli_importi_veri(test_db):
    """Riaprire una spesa corretta a mano non deve farti perdere la correzione,
    e il modulo deve dire al JS quali numeri erano tuoi."""
    tid = _spesa(7.60, arr=0.90)
    ed = fin.dati_modifica(tid)
    assert (ed["extra_arr"], ed["extra_sav"]) == ("0,90", "0,07")
    assert ed["extra_arr_mio"] is True        # 0,90 non è quello che calcolerebbe l'app
    assert ed["extra_sav_mio"] is False       # 0,07 sì


def test_il_saveback_del_movimento_non_riempie_il_tetto_a_se_stesso(test_db):
    """Riaprendo la stessa spesa, il suo saveback è già nel totale del mese: se
    non lo si escludesse, la proposta calerebbe a ogni riapertura."""
    with test_db() as db:
        db.get(Wallet, 1).saveback_tetto = 0.10
        db.commit()
    tid = _spesa(7.60)                                    # matura 0,07
    e = fin.extra_carta(1, 7.60, QUANDO, escludi_tx=tid)
    assert e["saveback"] == 0.07                          # non 0,03


# ==================== partite di giro ====================
# Una spesa da farsi rimborsare resta una spesa fatta con la carta: la banca
# arrotonda lo stesso. Il rimborso riguarda la spesa, non l'arrotondamento —
# quei soldi restano nel salvadanaio anche quando i soldi tornano indietro.
def _giro(importo=7.60, **kw):
    s = {"importo": importo, "wallet_id": 1, "categoria": "Regali",
         "descrizione": "pagato io", "data": QUANDO}
    s.update(kw)
    return fin.crea_giro(spese=[s], aperta=True)


def test_anche_una_spesa_da_rimborsare_arrotonda(test_db):
    gid = _giro(7.60)
    with test_db() as db:
        gamba = db.query(Transaction).filter(Transaction.giro_id == gid).one()
        figlie = db.query(Transaction).filter(
            Transaction.parent_tx_id == gamba.id).order_by(Transaction.id).all()
    assert [(f.tipo, f.importo, f.origine) for f in figlie] == [
        (TIPO_TRASFERIMENTO, 0.40, fin.ORIGINE_ARROTONDAMENTO),
        (TIPO_ENTRATA, 0.07, fin.ORIGINE_SAVEBACK)]
    saldi = {r["w"].nome: r["saldo"] for r in fin.saldi()["righe"]}
    assert saldi["Trade Republic"] == 92.00        # 100 − 7,60 − 0,40
    assert saldi[fin.NOME_WALLET_NASCOSTI] == 0.47


def test_il_rimborso_non_restituisce_l_arrotondamento(test_db):
    """Ti ridanno i 7,60 della spesa, non i 40 centesimi finiti nell'oro."""
    gid = _giro(7.60)
    fin.chiudi_giro(gid, importo=7.60, wallet_id=1, controparte="babbo", data=QUANDO)
    saldi = {r["w"].nome: r["saldo"] for r in fin.saldi()["righe"]}
    assert saldi["Trade Republic"] == 99.60        # rientrati 7,60, non 8,00
    assert saldi[fin.NOME_WALLET_NASCOSTI] == 0.47


def test_una_gamba_su_una_carta_senza_regole_non_genera_niente(test_db):
    gid = fin.crea_giro(aperta=True, spese=[
        {"importo": 7.60, "wallet_id": 1, "data": QUANDO},     # carta TR
        {"importo": 5.00, "wallet_id": 3, "data": QUANDO},     # Hype, niente regole
    ])
    with test_db() as db:
        gambe = db.query(Transaction).filter(Transaction.giro_id == gid).all()
        figlie = db.query(Transaction).filter(Transaction.parent_tx_id.isnot(None)).all()
    assert len(gambe) == 2 and len(figlie) == 2                # solo quelle della TR
    assert {f.parent_tx_id for f in figlie} == {
        next(g.id for g in gambe if g.wallet_id == 1)}


def test_il_tetto_si_consuma_anche_fra_le_gambe(test_db):
    """Due spese nella stessa partita non possono maturare, insieme, più di quel
    che resta del tetto: nascono nello stesso istante e nessuna delle due vede
    ancora l'altra scritta."""
    with test_db() as db:
        db.get(Wallet, 1).saveback_tetto = 0.10
        db.commit()
    fin.crea_giro(aperta=True, spese=[
        {"importo": 7.60, "wallet_id": 1, "data": QUANDO},     # 0,07
        {"importo": 7.60, "wallet_id": 1, "data": QUANDO},     # ne restano 0,03
    ])
    assert fin.saveback_maturato(ANNO, MESE) == 0.10


def test_gli_importi_scritti_a_mano_valgono_anche_nel_giro(test_db):
    gid = _giro(7.60, arr=0.90, sav=0.0)
    with test_db() as db:
        gamba = db.query(Transaction).filter(Transaction.giro_id == gid).one()
        figlie = db.query(Transaction).filter(Transaction.parent_tx_id == gamba.id).all()
    assert [(f.importo, f.origine) for f in figlie] == [(0.90, fin.ORIGINE_ARROTONDAMENTO)]


def test_il_registro_mostra_la_gamba_una_volta_sola(test_db):
    _giro(7.60)
    righe = fin.lista_movimenti()
    assert len(righe) == 1
    assert righe[0]["addebito"] == 8.00
    assert len(righe[0]["figlie"]) == 2


def test_cancellare_la_partita_porta_via_anche_le_figlie(test_db):
    """Il buco che le uscite non avevano più ma i giri sì: elimina_movimento
    marcava tutte le gambe e si dimenticava delle righe generate, che restavano
    vive e invisibili — col salvadanaio pieno di soldi senza spiegazione."""
    gid = _giro(7.60)
    with test_db() as db:
        gamba = db.query(Transaction).filter(Transaction.giro_id == gid).one()
        tid = gamba.id
    fin.elimina_movimento(tid)
    assert fin.lista_movimenti() == []
    with test_db() as db:
        assert db.query(Transaction).filter(Transaction.deleted.is_(False)).count() == 0
    assert fin.saldi()["totale"] == 100.0


def test_modificare_la_partita_rifa_le_figlie_senza_lasciarne_di_orfane(test_db):
    """aggiorna_giro rifà le gambe da zero: le vecchie figlie devono morire con
    loro, altrimenti restano appese a un genitore cancellato."""
    gid = _giro(7.60)
    fin.aggiorna_giro(gid, aperta=True, spese=[
        {"importo": 12.34, "wallet_id": 1, "categoria": "Regali", "data": QUANDO}])
    with test_db() as db:
        vive = db.query(Transaction).filter(Transaction.deleted.is_(False)).all()
        figlie = [t for t in vive if t.parent_tx_id]
        gambe = [t for t in vive if not t.parent_tx_id]
    assert len(gambe) == 1 and gambe[0].importo == 12.34
    assert sorted((f.importo, f.origine) for f in figlie) == [
        (0.12, fin.ORIGINE_SAVEBACK), (0.66, fin.ORIGINE_ARROTONDAMENTO)]
    assert all(f.parent_tx_id == gambe[0].id for f in figlie)
    assert fin.saldi()["righe"] and fin.accantonato_mese(ANNO, MESE) == 0.78


def test_il_modulo_di_modifica_ripropone_le_figlie_di_ogni_gamba(test_db):
    gid = _giro(7.60, arr=0.90)
    with test_db() as db:
        tid = db.query(Transaction).filter(Transaction.giro_id == gid).one().id
    ed = fin.dati_modifica(tid)
    assert ed["kind"] == "giro"
    s = ed["spese"][0]
    assert (s["arr"], s["sav"]) == ("0,90", "0,07")
    assert s["arr_mio"] is True and s["sav_mio"] is False
