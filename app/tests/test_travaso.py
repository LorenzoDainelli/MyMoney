"""Il travaso dei dati dal PC al server.

Questo script gira **una volta sola, su dati veri**. Non c'è un secondo giro in
cui accorgersi di un errore: o è giusto al primo colpo, o ha già fatto il danno.

I test che contano sono due, e nessuno dei due riguarda i movimenti:
1. i **segreti non partono** — la chiave dell'agente e quelle del Drive devono
   restare sul PC, non finire in un database raggiungibile dalla rete;
2. il **secondo fattore del server sopravvive** — sul PC non esiste, quindi un
   travaso ingenuo lo cancellerebbe e la 2FA si azzererebbe in silenzio.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine, insert, select

from scripts import travaso_db as tr
from shared.db import Base
from shared.settings_store import Setting
from finance.models import Wallet, Transaction


def _due_database(tmp_path):
    """Un «PC» pieno e un «server» con dentro solo la sua roba."""
    pc = create_engine(f"sqlite:///{tmp_path / 'pc.db'}")
    server = create_engine(f"sqlite:///{tmp_path / 'server.db'}")
    md = tr._metadata()
    md.create_all(pc)
    md.create_all(server)
    return pc, server


def _scrivi(engine, coppie):
    tab = Setting.__table__
    with engine.begin() as c:
        for k, v in coppie:
            c.execute(insert(tab), [{"chiave": k, "valore": v}])


def _leggi(engine) -> dict:
    tab = Setting.__table__
    with engine.connect() as c:
        return {r.chiave: r.valore for r in c.execute(select(tab))}


@pytest.fixture
def scena(tmp_path):
    pc, server = _due_database(tmp_path)
    _scrivi(pc, [
        ("ui_theme", "dark"),
        ("fuso_orario", "Europe/Dublin"),
        ("gemini_api_key", "CHIAVE-SEGRETA-DELL-AGENTE"),
        ("drive_client_secret", "GOCSPX-segretissimo"),
        ("drive_token", '{"refresh_token": "non-deve-partire"}'),
        ("vertex_service_account_json", '{"private_key": "nemmeno-questa"}'),
        ("sync_device_id", "il-pc-di-lorenzo"),
    ])
    # sul server c'è già il secondo fattore, attivato al primo accesso
    _scrivi(server, [("auth_totp_segreto", "SEGRETO-DEL-TELEFONO")])
    return pc, server


# ── i due test che contano ──────────────────────────────────────────────────

def test_i_segreti_restano_sul_pc(scena):
    pc, server = scena
    tr.travasa(str(pc.url), str(server.url), svuota=True)
    arrivate = _leggi(server)
    for segreto in ("gemini_api_key", "drive_client_secret", "drive_token",
                    "vertex_service_account_json"):
        assert segreto not in arrivate, f"{segreto} è finito nel database in rete!"


def test_il_secondo_fattore_del_server_sopravvive(scena):
    """Sul PC non esiste. Uno svuotamento secco lo porterebbe via, e la 2FA si
    azzererebbe senza che nessuno se ne accorga."""
    pc, server = scena
    tr.travasa(str(pc.url), str(server.url), svuota=True)
    assert _leggi(server).get("auth_totp_segreto") == "SEGRETO-DEL-TELEFONO"


# ── e il resto deve comunque arrivare ───────────────────────────────────────

def test_le_preferenze_viaggiano(scena):
    pc, server = scena
    tr.travasa(str(pc.url), str(server.url), svuota=True)
    arrivate = _leggi(server)
    assert arrivate["ui_theme"] == "dark"
    assert arrivate["fuso_orario"] == "Europe/Dublin", \
        "senza il fuso, sul server i movimenti prenderebbero un'altra data"


def test_l_identita_del_pc_non_diventa_quella_del_server(scena):
    pc, server = scena
    tr.travasa(str(pc.url), str(server.url), svuota=True)
    assert "sync_device_id" not in _leggi(server)


def test_una_chiave_mai_vista_resta_qui_ma_viene_detta(scena, capsys):
    """Il difetto di un elenco di ciò che passa è che una voce nuova resta
    indietro in silenzio. Non deve restarci in silenzio."""
    pc, server = scena
    _scrivi(pc, [("impostazione_inventata_domani", "x")])
    tr.travasa(str(pc.url), str(server.url), svuota=True)
    assert "impostazione_inventata_domani" not in _leggi(server)
    detto = capsys.readouterr().out
    assert "impostazione_inventata_domani" in detto
    assert "ATTENZIONE" in detto


def test_i_movimenti_arrivano_tutti_col_loro_legame(tmp_path):
    """Il resto del travaso non deve essere cambiato dal filtro: una spesa con
    le sue due righe generate deve arrivare intera e ancora collegata."""
    pc, server = _due_database(tmp_path)
    # Tutte le righe con le STESSE chiavi: in un insert multiplo SQLAlchemy
    # compila la frase sul primo dizionario, e quel che c'è solo negli altri
    # sparisce senza dire niente.
    def mov(**kw):
        base = {"id": None, "tipo": "uscita", "importo": 0.0, "wallet_id": 1,
                "parent_tx_id": None, "origine": ""}
        return {**base, **kw}
    with pc.begin() as c:
        c.execute(insert(Wallet.__table__), [
            {"id": 1, "nome": "Trade Republic", "tipo": "carta", "saldo_iniziale": 100.0}])
        c.execute(insert(Transaction.__table__), [
            mov(id=10, tipo="uscita", importo=40.45),
            mov(id=11, tipo="entrata", importo=0.4045, parent_tx_id=10,
                origine="saveback"),
            mov(id=12, tipo="trasferimento", importo=0.55, parent_tx_id=10,
                origine="arrotondamento")])
    assert tr.travasa(str(pc.url), str(server.url), svuota=True) == 0
    with server.connect() as c:
        righe = {r.id: r for r in c.execute(select(Transaction.__table__))}
    assert len(righe) == 3
    assert righe[11].parent_tx_id == 10 and righe[12].parent_tx_id == 10
    assert righe[11].importo == 0.4045      # i decimillesimi del saveback


def test_un_travaso_che_ha_gia_perso_i_segreti_li_denuncia(scena, capsys):
    """Se di là un segreto c'è (travaso vecchio, o qualcosa sfuggito), il
    travaso deve FALLIRE. Un guaio così non si scopre un anno dopo."""
    pc, server = scena
    _scrivi(server, [("gemini_api_key", "arrivata-chissà-come")])
    problemi = tr.travasa(str(pc.url), str(server.url), svuota=True)
    assert problemi > 0
    assert "SEGRETI ARRIVATI" in capsys.readouterr().out
