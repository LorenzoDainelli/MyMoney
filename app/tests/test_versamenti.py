"""Test del motore dei versamenti PAC (portfolio/versamenti.py).

Verifica: ripartizione per % (normalizzata, totale esatto), accumulo PMC sulle
quantità, esclusione di un titolo, e annullamento esatto con elimina().
I prezzi sono STUBBATI (nessuna rete): il test guarda la logica, non i mercati.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Base
from portfolio.models import Position, Versamento, VersamentoRiga
import portfolio.service as pf_service
import portfolio.versamenti as versamenti
from motore import engine_di_prova

# la funzione VERA, presa prima che la fixture la sostituisca con lo stub
_PREZZO_REALE = versamenti._prezzo_eur_alla_data


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    engine = engine_di_prova(tmp_path / "test.db")
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    import shared.db as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(pf_service, "SessionLocal", TestSession)
    monkeypatch.setattr(versamenti, "SessionLocal", TestSession)
    # niente rete: prezzo fisso 10€ per tutti, e nessuna quotazione in cache
    monkeypatch.setattr(versamenti.market, "quotes_map", lambda: {})
    monkeypatch.setattr(versamenti, "_prezzo_eur_alla_data",
                        lambda p, data, qmap, oggi, ora="": (10.0, "test"))
    yield TestSession


def _seed(Session):
    """3 titoli con % 50/30/20 (somma 100)."""
    with Session() as db:
        db.add_all([
            Position(nome="Alpha", ticker="A", pct_target=50.0, ordine=0),
            Position(nome="Beta", ticker="B", pct_target=30.0, ordine=1),
            Position(nome="Gamma", ticker="C", pct_target=20.0, ordine=2),
        ])
        db.commit()
        return {p.ticker: p.id for p in db.execute(select(Position)).scalars()}


def _pos(Session, pid):
    with Session() as db:
        return db.get(Position, pid)


def test_riparto_e_accumulo_pmc(test_db):
    Session = test_db
    ids = _seed(Session)
    vid = versamenti.salva(100.0, date.today(), "TR", esclusi=set())
    assert vid is not None

    a, b, c = _pos(Session, ids["A"]), _pos(Session, ids["B"]), _pos(Session, ids["C"])
    # €50/€30/€20 a prezzo 10 -> 5/3/2 quote; versato = gli euro
    assert (a.versato_totale, b.versato_totale, c.versato_totale) == (50.0, 30.0, 20.0)
    assert (round(a.quantita, 6), round(b.quantita, 6), round(c.quantita, 6)) == (5.0, 3.0, 2.0)

    # secondo PAC identico: le quantità si SOMMANO (una sola posizione, PMC)
    versamenti.salva(100.0, date.today(), "TR", esclusi=set())
    a2 = _pos(Session, ids["A"])
    assert round(a2.quantita, 6) == 10.0 and a2.versato_totale == 100.0

    # due versamenti a storico
    with Session() as db:
        assert db.execute(select(Versamento)).scalars().all().__len__() == 2
        assert db.execute(select(VersamentoRiga)).scalars().all().__len__() == 6


def test_esclusione_ridistribuisce(test_db):
    Session = test_db
    ids = _seed(Session)
    # escludo Gamma: l'importo si ridistribuisce fra A(50) e B(30) -> 62.5 / 37.5
    versamenti.salva(100.0, date.today(), "TR", esclusi={ids["C"]})
    a, b, c = _pos(Session, ids["A"]), _pos(Session, ids["B"]), _pos(Session, ids["C"])
    assert round(a.versato_totale + b.versato_totale, 2) == 100.0
    assert round(a.versato_totale, 2) == 62.5 and round(b.versato_totale, 2) == 37.5
    assert (c.quantita in (None, 0)) and c.versato_totale == 0.0


def test_totale_esatto_con_arrotondamenti(test_db):
    Session = test_db
    # % che non dividono bene 100 (33.33/33.33/33.34-ish): il totale deve tornare esatto
    with Session() as db:
        db.add_all([
            Position(nome="X", ticker="X", pct_target=33.0, ordine=0),
            Position(nome="Y", ticker="Y", pct_target=33.0, ordine=1),
            Position(nome="Z", ticker="Z", pct_target=34.0, ordine=2),
        ])
        db.commit()
    versamenti.salva(100.0, date.today(), "TR", esclusi=set())
    with Session() as db:
        tot = sum(p.versato_totale for p in db.execute(select(Position)).scalars())
    assert round(tot, 2) == 100.0


def test_elimina_ripristina(test_db):
    Session = test_db
    ids = _seed(Session)
    vid = versamenti.salva(100.0, date.today(), "TR", esclusi=set())
    assert versamenti.elimina(vid) is True

    a, b, c = _pos(Session, ids["A"]), _pos(Session, ids["B"]), _pos(Session, ids["C"])
    assert (a.quantita, b.quantita, c.quantita) == (0.0, 0.0, 0.0)
    assert (a.versato_totale, b.versato_totale, c.versato_totale) == (0.0, 0.0, 0.0)
    with Session() as db:
        assert db.execute(select(Versamento)).scalars().first() is None
        assert db.execute(select(VersamentoRiga)).scalars().first() is None


# ------------------------- orario del versamento -------------------------
def test_parse_ora():
    """L'ora è facoltativa: se manca o è scritta male, si torna al giorno."""
    from datetime import time
    assert versamenti.parse_ora("09:30") == time(9, 30)
    assert versamenti.parse_ora("  17:05  ") == time(17, 5)
    assert versamenti.parse_ora("") is None
    assert versamenti.parse_ora(None) is None
    assert versamenti.parse_ora("boh") is None


def test_ora_salvata_sul_versamento(test_db):
    Session = test_db
    _seed(Session)
    vid = versamenti.salva(100.0, date.today(), "TR", esclusi=set(), ora="09:30")
    with Session() as db:
        assert db.get(Versamento, vid).ora == "09:30"
    assert versamenti.dettaglio(vid)["ora"] == "09:30"
    assert versamenti.lista()[0]["ora"] == "09:30"


def test_prezzo_usa_la_candela_dell_ora(monkeypatch):
    """Con l'ora indicata si prende l'ultima candela oraria FINO a quel momento,
    non la successiva.

    Le candele sono istanti universali: qui si costruiscono NEL FUSO SCELTO,
    lo stesso in cui l'utente scrive «10:30». Costruirle con l'orologio della
    macchina faceva passare il test in Italia e fallire in Irlanda — e sul
    server, che gira in UTC, avrebbe scelto la candela di due ore prima."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    import portfolio.versamenti as v
    from shared import tempo

    zona = ZoneInfo(tempo.nome_fuso())
    ieri = tempo.oggi() - timedelta(days=1)
    candele = [(datetime.combine(ieri, datetime.min.time().replace(hour=h),
                                 tzinfo=zona).timestamp(), 10.0 + h)
               for h in (9, 10, 11, 12)]
    monkeypatch.setattr(v.market, "history_series", lambda sym, r, i: candele)
    monkeypatch.setattr(v.market, "_yahoo_symbol", lambda tk: tk)
    monkeypatch.setattr(v.market, "_fx_to_eur_rate", lambda cur: 1.0)

    p = Position(nome="Alpha", ticker="A", pct_target=100.0)
    prezzo, fonte = _PREZZO_REALE(p, ieri, {}, tempo.oggi(), "10:30")
    assert (prezzo, fonte) == (20.0, "orario")     # candela delle 10, non delle 11


# ------------------- il titolo a target 0 (l'ETC oro) -------------------
def test_un_titolo_a_target_zero_non_prende_niente_dal_pac(test_db):
    """L'oro riceve solo gli arrotondamenti della carta, mai il PAC mensile.
    Se un giorno entrasse nella ripartizione, i 100 € del PAC si spalmerebbero
    su 38 titoli invece di 37 e ogni percentuale target sarebbe falsa."""
    Session = test_db
    ids = _seed(Session)
    with Session() as db:
        db.add(Position(nome="Oro", ticker="EGLN", isin="IE00B4ND3602",
                        pct_target=0.0, ordine=3))
        db.commit()

    versamenti.salva(100.0, date.today(), "TR", esclusi=set())
    with Session() as db:
        oro = db.execute(select(Position).where(
            Position.isin == "IE00B4ND3602")).scalars().one()
        assert (oro.quantita, oro.versato_totale) == (None, 0.0)
        # e i 100 € sono finiti tutti sugli altri tre
        altri = sum((db.get(Position, pid).versato_totale or 0.0)
                    for pid in ids.values())
        assert round(altri, 2) == 100.0


def test_l_oro_non_entra_nemmeno_nell_anteprima(test_db):
    Session = test_db
    _seed(Session)
    with Session() as db:
        db.add(Position(nome="Oro", ticker="EGLN", isin="IE00B4ND3602",
                        pct_target=0.0, ordine=3))
        db.commit()
    a = versamenti.anteprima(100.0, date.today(), esclusi=set())
    assert a["n_inclusi"] == 3
    assert "EGLN" not in [r["ticker"] for r in a["righe"]]
