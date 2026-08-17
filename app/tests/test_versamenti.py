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
from shared import tempo          # che ora è: il fuso scelto, non l'orologio del PC
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
    vid = versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set())
    assert vid is not None

    a, b, c = _pos(Session, ids["A"]), _pos(Session, ids["B"]), _pos(Session, ids["C"])
    # €50/€30/€20 a prezzo 10 -> 5/3/2 quote; versato = gli euro
    assert (a.versato_totale, b.versato_totale, c.versato_totale) == (50.0, 30.0, 20.0)
    assert (round(a.quantita, 6), round(b.quantita, 6), round(c.quantita, 6)) == (5.0, 3.0, 2.0)

    # secondo PAC identico: le quantità si SOMMANO (una sola posizione, PMC)
    versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set())
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
    versamenti.salva(100.0, tempo.oggi(), "TR", esclusi={ids["C"]})
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
    versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set())
    with Session() as db:
        tot = sum(p.versato_totale for p in db.execute(select(Position)).scalars())
    assert round(tot, 2) == 100.0


def test_elimina_ripristina(test_db):
    Session = test_db
    ids = _seed(Session)
    vid = versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set())
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
    vid = versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set(), ora="09:30")
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


# ─────────────── l'ora di OGNI titolo (TR non esegue tutto insieme) ───────────
def test_normalizza_ora_prende_le_cifre():
    """Trentotto orari si battono solo se battere i due punti non serve."""
    n = versamenti.normalizza_ora
    assert n("0935") == "09:35"
    assert n("935") == "09:35"          # tre cifre: manca lo zero davanti
    assert n("09:35") == "09:35"
    assert n("9") == "09:00"            # ora tonda
    assert n("17") == "17:00"
    assert n("") == "" and n(None) == ""
    assert n("boh") == ""
    assert n("2599") == "" and n("9999") == ""   # non è un'ora: non si inventa


def test_ogni_titolo_tiene_la_sua_ora(test_db):
    Session = test_db
    ids = _seed(Session)
    vid = versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set(), ora="09:00",
                           orari={ids["A"]: "0912", ids["B"]: "17:40"})
    with Session() as db:
        ore = {r.ticker: r.ora for r in db.execute(select(VersamentoRiga)).scalars()}
    # A e B la loro, C quella del versamento — e "0912" è diventato "09:12"
    assert ore == {"A": "09:12", "B": "17:40", "C": "09:00"}
    # modificare il PAC del mese scorso li ritrova tutti al loro posto
    assert versamenti.dettaglio(vid)["orari"] == {
        ids["A"]: "09:12", ids["B"]: "17:40", ids["C"]: "09:00"}


def test_il_prezzo_di_ogni_titolo_e_quello_della_sua_ora(test_db, monkeypatch):
    """Il punto di tutta la funzione: se le ore per titolo non arrivassero fino
    al prezzo, si comprerebbe tutto al prezzo di un istante solo."""
    Session = test_db
    ids = _seed(Session)
    chiamate = {}

    def registra(p, data, qmap, oggi, ora=""):
        chiamate[p.ticker] = ora
        return 10.0, "test"

    monkeypatch.setattr(versamenti, "_prezzo_eur_alla_data", registra)
    versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set(), ora="09:00",
                     orari={ids["A"]: "0912", ids["B"]: "17:40"})
    assert chiamate == {"A": "09:12", "B": "17:40", "C": "09:00"}


def test_l_anteprima_mostra_l_ora_che_userà(test_db):
    Session = test_db
    ids = _seed(Session)
    a = versamenti.anteprima(100.0, tempo.oggi(), esclusi=set(), ora="09:00",
                             orari={ids["A"]: "935"})
    ore = {r["ticker"]: r["ora"] for r in a["righe"]}
    assert ore == {"A": "09:35", "B": "09:00", "C": "09:00"}


def test_lo_storico_dice_dal_primo_all_ultimo(test_db):
    Session = test_db
    ids = _seed(Session)
    versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set(), ora="09:00",
                     orari={ids["A"]: "09:12", ids["B"]: "17:40"})
    v = versamenti.lista()[0]
    assert v["ora_span"] == "09:00–17:40" and v["n_orari"] == 3


def test_lo_storico_dice_una_sola_ora_se_e_una_sola(test_db):
    Session = test_db
    _seed(Session)
    versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set(), ora="09:30")
    v = versamenti.lista()[0]
    assert v["ora_span"] == "09:30" and v["n_orari"] == 1


def test_riprendi_gli_orari_dal_pac_precedente(test_db):
    """TR esegue più o meno negli stessi momenti ogni mese: il mese dopo si
    parte da quelli invece di ribattere trentotto orari."""
    Session = test_db
    ids = _seed(Session)
    versamenti.salva(100.0, date(2026, 7, 16), "TR", esclusi=set(),
                     orari={ids["A"]: "09:12", ids["B"]: "17:40"})
    vid2 = versamenti.salva(100.0, date(2026, 8, 16), "TR", esclusi=set())
    # il PAC di agosto non ha orari: «l'ultimo che ne aveva» è quello di luglio
    assert versamenti.ultimi_orari()[ids["A"]] == "09:12"
    # e modificando quello di luglio non si propone sé stesso
    vid_luglio = [v["id"] for v in versamenti.lista() if v["id"] != vid2][0]
    assert versamenti.ultimi_orari(escludi_vid=vid_luglio) == {}


def test_senza_orari_niente_cambia(test_db):
    """La strada di prima resta identica: nessun'ora sulle righe, e il
    versamento tiene la sua."""
    Session = test_db
    _seed(Session)
    vid = versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set())
    with Session() as db:
        assert {r.ora for r in db.execute(select(VersamentoRiga)).scalars()} == {""}
    assert versamenti.dettaglio(vid)["orari"] == {}
    assert versamenti.lista()[0]["ora_span"] == ""


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

    versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set())
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
    a = versamenti.anteprima(100.0, tempo.oggi(), esclusi=set())
    assert a["n_inclusi"] == 3
    assert "EGLN" not in [r["ticker"] for r in a["righe"]]


# ── acquisti FUORI dal piano mensile (l'ETC oro comprato coi saveback) ───────
#
# L'oro ha `pct_target = 0` di proposito: non fa parte della ripartizione dei
# 100 € mensili, lo compra la banca con saveback e arrotondamenti. Ma è una
# posizione vera e gli acquisti vanno registrati — e prima non si poteva:
# `_riparti` scartava i titoli senza quota e il modulo rispondeva «nessun
# titolo incluso», cioè l'app sapeva dell'oro e non dava modo di comprarlo.

def _seed_con_oro(Session):
    """I tre soliti titoli con quota, più l'oro che di quota non ne ha."""
    with Session() as db:
        db.add_all([
            Position(nome="Alpha", ticker="A", pct_target=50.0, ordine=0),
            Position(nome="Beta", ticker="B", pct_target=30.0, ordine=1),
            Position(nome="Gamma", ticker="C", pct_target=20.0, ordine=2),
            Position(nome="iShares Physical Gold", ticker="EGLN", pct_target=0.0, ordine=3),
        ])
        db.commit()
        return {p.ticker: p.id for p in db.execute(select(Position)).scalars()}


def test_un_titolo_senza_quota_scelto_da_solo_si_prende_tutto(test_db):
    """Il caso vero: 0,07 € di oro comprati dalla banca coi saveback."""
    Session = test_db
    ids = _seed_con_oro(Session)
    altri = {i for t, i in ids.items() if t != "EGLN"}
    a = versamenti.anteprima(0.07, tempo.oggi(), esclusi=altri)
    assert a["n_inclusi"] == 1
    assert a["righe"][0]["ticker"] == "EGLN"
    assert a["righe"][0]["euro"] == pytest.approx(0.07)
    assert a["righe"][0]["pct"] == pytest.approx(100.0)   # non «0%», che era il target
    assert a["totale"] == pytest.approx(0.07)


def test_il_pac_normale_non_cambia_di_una_virgola(test_db):
    """Il ramo nuovo si accende SOLO dove prima non usciva niente. Con dei
    titoli a quota fra i scelti comandano le quote, come sempre."""
    Session = test_db
    ids = _seed_con_oro(Session)
    a = versamenti.anteprima(100.0, tempo.oggi(), esclusi=set())
    per_ticker = {r["ticker"]: r["euro"] for r in a["righe"]}
    assert per_ticker == {"A": 50.0, "B": 30.0, "C": 20.0}
    assert "EGLN" not in per_ticker        # quota 0: nel piano non c'e'
    assert a["totale"] == pytest.approx(100.0)


def test_un_titolo_senza_quota_non_ruba_niente_agli_altri(test_db):
    """Sceglierlo INSIEME ai titoli del piano non deve dargli una fetta che nel
    piano non gli spetta: basta che uno degli scelti abbia una quota perche'
    comandino le quote."""
    Session = test_db
    ids = _seed_con_oro(Session)
    a = versamenti.anteprima(100.0, tempo.oggi(), esclusi={ids["C"]})
    per_ticker = {r["ticker"]: r["euro"] for r in a["righe"]}
    assert "EGLN" not in per_ticker
    # 50 e 30 rinormalizzati su 80 -> 62,50 e 37,50, e il totale resta esatto
    assert per_ticker == {"A": 62.5, "B": 37.5}
    assert a["totale"] == pytest.approx(100.0)


def test_piu_titoli_senza_quota_si_dividono_in_parti_uguali(test_db):
    """Non e' il caso di tutti i giorni, ma la regola dev'essere dichiarata: se
    NESSUNO degli scelti ha una quota, l'unica risposta neutra e' meta' e meta'
    — e si vede nell'anteprima prima di confermare."""
    Session = test_db
    with Session() as db:
        db.add_all([Position(nome="Oro", ticker="EGLN", pct_target=0.0, ordine=0),
                    Position(nome="Argento", ticker="SLVR", pct_target=0.0, ordine=1)])
        db.commit()
    a = versamenti.anteprima(10.0, tempo.oggi(), esclusi=set())
    assert sorted(r["euro"] for r in a["righe"]) == [5.0, 5.0]
    assert a["totale"] == pytest.approx(10.0)


def test_l_acquisto_di_solo_oro_viene_marcato_fuori_piano(test_db):
    Session = test_db
    ids = _seed_con_oro(Session)
    altri = {i for t, i in ids.items() if t != "EGLN"}
    vid = versamenti.salva(0.07, tempo.oggi(), "Nascosti", esclusi=altri)
    assert vid is not None
    voci = {v["id"]: v for v in versamenti.lista()}
    assert voci[vid]["fuori_piano"] is True


def test_il_pac_del_mese_non_e_fuori_piano(test_db):
    Session = test_db
    _seed_con_oro(Session)
    vid = versamenti.salva(100.0, tempo.oggi(), "TR", esclusi=set())
    assert versamenti.lista()[0]["fuori_piano"] is False


# ── il promemoria del PAC non si fa zittire da 7 centesimi d'oro ─────────────
# E' il punto in cui questa funzione poteva rompere qualcosa di gia' fatto:
# `promemoria()` tace se in questo mese c'e' gia' UN versamento qualsiasi.

def test_un_acquisto_fuori_piano_non_spegne_il_promemoria(test_db):
    """Senza il flag, 0,07 € d'oro il 3 del mese direbbero «la rata di questo
    mese e' fatta» e il promemoria non comparirebbe piu' fino al mese dopo."""
    Session = test_db
    ids = _seed_con_oro(Session)
    altri = {i for t, i in ids.items() if t != "EGLN"}

    # una storia di PAC veri nei mesi scorsi, tutti il giorno 16
    for mese in (4, 5, 6, 7):
        versamenti.salva(100.0, date(2026, mese, 16), "TR", esclusi=set())
    # ...e in agosto, per ora, solo un acquisto d'oro
    versamenti.salva(0.07, date(2026, 8, 3), "Nascosti", esclusi=altri)

    p = versamenti.promemoria(oggi=date(2026, 8, 20))
    assert p is not None, "il PAC di agosto NON e' stato fatto: il promemoria deve uscire"
    assert p["giorno"] == 16                       # la mediana non e' scivolata al 3
    assert p["importo_tipico"] == pytest.approx(100.0)   # ne' l'importo a 0,07
    assert p["n_versamenti"] == 4                  # i quattro veri, non cinque


def test_col_pac_del_mese_registrato_il_promemoria_tace(test_db):
    """Il rovescio: se la rata c'e' davvero, non si deve ricordare niente."""
    Session = test_db
    _seed_con_oro(Session)
    for mese in (4, 5, 6, 7):
        versamenti.salva(100.0, date(2026, mese, 16), "TR", esclusi=set())
    versamenti.salva(100.0, date(2026, 8, 16), "TR", esclusi=set())
    assert versamenti.promemoria(oggi=date(2026, 8, 20)) is None


# ── il modulo: le caselle dell'ora si chiamano con l'id del titolo ────────────
def test_gli_orari_arrivano_dal_modulo():
    """I campi sono tanti quanti i titoli e i loro nomi dipendono dal database,
    quindi non si possono dichiarare come parametri: si leggono dal form."""
    from starlette.datastructures import FormData
    from portfolio.routes import _orari_dal_modulo

    form = FormData([("importo", "100"), ("incl", "3"), ("incl", "7"),
                     ("ora_3", "0912"), ("ora_7", "  "), ("ora_x", "10:00"),
                     ("ora", "09:00")])
    # solo i campi `ora_<numero>` e non vuoti; `ora` da sola è quella generale
    assert _orari_dal_modulo(form) == {3: "0912"}


def test_la_casella_dell_ora_sta_fuori_dall_etichetta():
    """Un `<label>` gira i clic sulla sua casella di spunta: con dentro anche la
    casella dell'ora, toccarla per scrivere spegnerebbe il titolo. Sono due
    cose separate nel modulo, e devono restarci."""
    import re
    testo = (Path(__file__).resolve().parent.parent / "templates"
             / "portfolio_versamento.html").read_text(encoding="utf-8")
    dentro_le_etichette = re.findall(r"<label>(.*?)</label>", testo, re.S)
    assert dentro_le_etichette, "il modulo non ha più etichette: controlla il template"
    assert not any("pac-ora" in blocco for blocco in dentro_le_etichette)
    assert 'class="pac-ora"' in testo
