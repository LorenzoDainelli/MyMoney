"""La memoria dell'agente: non ripetersi, e restare correggibile.

Il punto delicato non è tecnico ma di fiducia: una conclusione sbagliata, se
resta invisibile, si fossilizza. Quindi qui si difende soprattutto che ogni
ricordo sia cancellabile e che i doppioni non si accumulino.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
import shared.ai_memory as mem
import shared.ai as ai
from motore import engine_di_prova


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    import shared.db as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(mem, "SessionLocal", TestSession)
    yield TestSession


# --------------------------- ricordi ---------------------------
def test_ricordo_salvato_e_cancellabile():
    assert mem.aggiungi_ricordo("Il PAC parte il 16 di ogni mese.", "dedotto in dashboard")
    rs = mem.ricordi()
    assert len(rs) == 1 and "16 di ogni mese" in rs[0].testo
    assert mem.dimentica(rs[0].id) is True
    assert mem.ricordi() == []


def test_niente_doppioni_ne_briciole():
    assert mem.aggiungi_ricordo("Le spese per regali sono stagionali.")
    assert mem.aggiungi_ricordo("le spese per REGALI sono stagionali.") is False  # uguale
    assert mem.aggiungi_ricordo("ok") is False                                    # troppo corto
    assert len(mem.ricordi()) == 1


def test_il_profilo_non_cresce_all_infinito():
    for i in range(mem.MAX_RICORDI + 5):
        mem.aggiungi_ricordo(f"Abitudine numero {i} dell'utente.")
    assert len(mem.ricordi()) <= mem.MAX_RICORDI


def test_dimentica_tutto_solo_del_tipo_chiesto():
    mem.aggiungi_ricordo("Un ricordo qualsiasi dell'utente.")
    mem.salva_lettura("dashboard", "Una lettura.", chiavi=["cat:bar:sopra"])
    assert mem.dimentica_tutto(mem.TIPO_RICORDO) == 1
    assert mem.ricordi() == []
    assert len(mem.ultime_letture()) == 1        # le letture restano


# --------------------------- letture ---------------------------
def test_le_chiavi_gia_dette_tornano_indietro():
    mem.salva_lettura("dashboard", "Testo", chiavi=["cat:bar:sopra", "pac:troppo_presto"])
    assert mem.chiavi_gia_dette("dashboard") == {"cat:bar:sopra", "pac:troppo_presto"}


def test_le_osservazioni_vecchie_scadono(test_db):
    mem.salva_lettura("dashboard", "Vecchia", chiavi=["cat:vecchia:sopra"])
    with test_db() as db:
        riga = db.query(mem.MemoriaAI).first()
        riga.quando = datetime.now() - timedelta(days=mem.GIORNI_NON_RIPETERE + 3)
        db.commit()
    assert mem.chiavi_gia_dette("dashboard") == set()   # oltre la finestra: si può ridire


def test_storico_letture_potato():
    for i in range(mem.MAX_LETTURE + 6):
        mem.salva_lettura("dashboard", f"Lettura {i}")
    assert len(mem.ultime_letture(n=200)) <= mem.MAX_LETTURE


# --------------------------- il marcatore RICORDA ---------------------------
def test_la_riga_ricorda_non_finisce_sotto_gli_occhi_dell_utente():
    testo, ricordo = mem.estrai_ricordo(
        "Gli abbonamenti sono raddoppiati.\nRICORDA: paga gli abbonamenti a inizio mese.")
    assert testo == "Gli abbonamenti sono raddoppiati."
    assert ricordo == "paga gli abbonamenti a inizio mese."


def test_senza_marcatore_il_testo_resta_intero():
    testo, ricordo = mem.estrai_ricordo("Riga uno.\nRiga due.")
    assert testo == "Riga uno.\nRiga due." and ricordo == ""


# --------------------------- integrazione col motore ---------------------------
def test_genera_con_memoria_registra_e_impara(monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    monkeypatch.setattr(ai, "_call", lambda *a, **k: (
        "Gli abbonamenti sono raddoppiati.\n"
        "RICORDA: gli abbonamenti si rinnovano a inizio mese.\n"
        "Confidenza: media"))
    import shared.insights as insights
    fatti = [insights.Fatto(chiave="cat:abbonamenti:sopra", testo="...", forza=80.0)]

    res = ai._genera("dashboard", fatti=fatti, memoria=True)
    assert "RICORDA" not in res["text"]                       # mai mostrato all'utente
    assert res["text"] == "Gli abbonamenti sono raddoppiati."
    assert any("rinnovano a inizio mese" in r.testo for r in mem.ricordi())
    assert mem.chiavi_gia_dette("dashboard") == {"cat:abbonamenti:sopra"}


def test_la_memoria_entra_nel_prompt(monkeypatch):
    visto = {}
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    monkeypatch.setattr(ai, "_call",
                        lambda p, **k: visto.setdefault("p", p) and "" or "ok\nConfidenza: alta")
    mem.aggiungi_ricordo("Il PAC parte il 16 di ogni mese.")
    mem.salva_lettura("dashboard", "SNDK segna +3445% a 12 mesi.",
                      chiavi=["pac:andamento"])

    ai._genera("dashboard", fatti=[], memoria=True)
    p = visto["p"]
    assert "Il PAC parte il 16 di ogni mese." in p     # i ricordi servono
    assert "pac:andamento" in p                        # gli argomenti già trattati
    # ...ma il TESTO della lettura precedente no: rimandarlo indietro faceva
    # riscrivere al modello la stessa lettura, numeri vecchi compresi
    assert "+3445%" not in p
    assert "SNDK" not in p
    assert "lettura NUOVA" in p


def test_senza_memoria_il_prompt_resta_pulito(monkeypatch):
    visto = {}
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    monkeypatch.setattr(ai, "_call",
                        lambda p, **k: visto.setdefault("p", p) and "" or "ok\nConfidenza: alta")
    mem.aggiungi_ricordo("Un ricordo che non deve comparire qui.")
    ai._genera("titolo", contesto="ETF globale")
    assert "non deve comparire" not in visto["p"]
    assert mem.ultime_letture() == []            # e non registra nulla
