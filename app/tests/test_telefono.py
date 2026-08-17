"""Quello che il TELEFONO ha aggiunto al server.

Il grosso del lavoro sul telefono è CSS e template, e quello si misura nel
browser (l'ho fatto: Finanze da 18,1 a 4 schermate). Ma due pezzi sono finiti
nel server, e quelli vanno difesi come tutto il resto:

1. **Il raggruppamento per giorno** (`_per_giorno`). L'elenco dei movimenti
   scriveva la data su ognuna delle 58 righe; adesso la scrive una volta sola
   in testa al giorno, col totale di quel giorno accanto. Un totale sbagliato
   è peggio di nessun totale: si legge di sfuggita e ci si crede.

2. **Dove si torna dopo aver salvato** (`_torna_a`). Il «＋» si preme da
   qualunque pagina e alla fine deve riportarti lì. Lo dice il `Referer`, che
   è roba del browser: se lo si prendesse per buono, il modulo rimanderebbe
   dove vuole chi ha scritto il link.

Niente `TestClient`: le rotte e le funzioni si chiamano come funzioni normali,
con una `Request` costruita a mano — stessa scelta di test_accesso_pagine.py.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from starlette.requests import Request

from shared import tempo          # che ora è: il fuso scelto, non l'orologio del PC
import finance.routes as rotte
import finance.service as service
from finance.models import TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO


# ── impianto ────────────────────────────────────────────────────────────────

def _mov(tipo, quando, importo, giro=None):
    """Un movimento nella forma in cui `_per_giorno` lo riceve dal service:
    un dizionario con dentro l'oggetto della riga."""
    class Riga:
        pass
    r = Riga()
    r.tipo = tipo
    r.data = quando
    r.importo = importo
    r.giro_importo_display = giro
    return {"t": r}


def richiesta(referer=None, host="mymoney.example.app"):
    intestazioni = [(b"host", host.encode())]
    if referer is not None:
        intestazioni.append((b"referer", referer.encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "https", "path": "/finanze/nuovo", "raw_path": b"/finanze/nuovo",
        "query_string": b"", "root_path": "", "headers": intestazioni,
        "client": ("203.0.113.7", 5555), "server": (host, 443),
    })


# ── il raggruppamento per giorno ────────────────────────────────────────────

def test_i_movimenti_dello_stesso_giorno_stanno_insieme():
    g = datetime(2026, 8, 9, 9, 0)
    righe = [_mov(TIPO_USCITA, g.replace(hour=20), 10.0),
             _mov(TIPO_USCITA, g.replace(hour=13), 5.0),
             _mov(TIPO_USCITA, datetime(2026, 8, 8, 11, 0), 7.0)]
    giorni = rotte._per_giorno(righe)
    assert [x["giorno"].day for x in giorni] == [9, 8]
    assert [len(x["righe"]) for x in giorni] == [2, 1]


def test_l_ordine_dei_movimenti_dentro_il_giorno_non_cambia():
    """Arrivano già dal database in ordine di data decrescente. Se il
    raggruppamento li rimescolasse, l'elenco direbbe che hai preso il caffè
    prima di uscire di casa."""
    g = datetime(2026, 8, 9)
    righe = [_mov(TIPO_USCITA, g.replace(hour=20), 10.0),
             _mov(TIPO_USCITA, g.replace(hour=13), 5.0),
             _mov(TIPO_USCITA, g.replace(hour=8), 3.0)]
    giorni = rotte._per_giorno(righe)
    assert [r["t"].data.hour for r in giorni[0]["righe"]] == [20, 13, 8]


def test_lo_stesso_giorno_in_due_mesi_diversi_non_si_fonde():
    """Il confronto è sulla DATA intera, non sul numero del giorno: il 9 luglio
    e il 9 agosto sono due giorni, e un `.day` distratto li sommerebbe."""
    righe = [_mov(TIPO_USCITA, datetime(2026, 8, 9, 10, 0), 10.0),
             _mov(TIPO_USCITA, datetime(2026, 7, 9, 10, 0), 20.0)]
    giorni = rotte._per_giorno(righe)
    assert len(giorni) == 2
    assert [x["totale"] for x in giorni] == [-10.0, -20.0]


def test_il_totale_del_giorno_e_entrate_meno_uscite():
    g = datetime(2026, 8, 9, 12, 0)
    righe = [_mov(TIPO_ENTRATA, g, 100.0), _mov(TIPO_USCITA, g, 30.0)]
    assert rotte._per_giorno(righe)[0]["totale"] == pytest.approx(70.0)


def test_i_trasferimenti_non_entrano_nel_totale_del_giorno():
    """Spostare 500 € da un conto all'altro non è un giorno in cui hai speso
    500 €. Contarli farebbe apparire in rosso il giorno in cui metti da parte,
    che è esattamente il contrario di quello che è successo."""
    g = datetime(2026, 8, 9, 12, 0)
    righe = [_mov(TIPO_TRASFERIMENTO, g, 500.0), _mov(TIPO_USCITA, g, 12.0)]
    giorni = rotte._per_giorno(righe)
    assert giorni[0]["totale"] == pytest.approx(-12.0)
    assert len(giorni[0]["righe"]) == 2        # si vede, ma non si somma


def test_l_etichetta_del_giorno_passa_dal_fuso_scelto(monkeypatch):
    """«oggi»/«ieri» le decide shared.tempo, non l'orologio della macchina:
    è la stessa regola della home, e qui si difende che valga anche qui."""
    monkeypatch.setattr(tempo, "oggi", lambda: datetime(2026, 8, 9).date())
    righe = [_mov(TIPO_USCITA, datetime(2026, 8, 9, 10, 0), 1.0),
             _mov(TIPO_USCITA, datetime(2026, 8, 8, 10, 0), 1.0),
             _mov(TIPO_USCITA, datetime(2026, 7, 1, 10, 0), 1.0)]
    giorni = rotte._per_giorno(righe)
    assert [x["etichetta"] for x in giorni] == ["dash.today", "dash.yesterday", None]


def test_senza_movimenti_non_ci_sono_giorni():
    assert rotte._per_giorno([]) == []


# ── dove si torna dopo aver salvato ─────────────────────────────────────────
# `Referer` lo scrive il browser e lo può scrivere chiunque. Questi test sono
# soprattutto RIFIUTI: quello che conta è cosa NON passa.

def test_si_torna_alla_pagina_da_cui_e_stato_premuto_il_piu():
    assert rotte._torna_a(richiesta("https://mymoney.example.app/portafoglio")) == "/portafoglio"
    assert rotte._torna_a(richiesta("https://mymoney.example.app/")) == "/"


def test_un_referer_di_un_altro_sito_viene_buttato():
    """Senza questo il campo `next` del modulo diventerebbe un trampolino: si
    salva un movimento e ci si ritrova su un sito di qualcun altro."""
    assert rotte._torna_a(richiesta("https://evil.example/rubo")) == ""
    assert rotte._torna_a(richiesta("http://evil.example/")) == ""


def test_anche_senza_schema_un_indirizzo_assoluto_viene_buttato():
    """`//evil.example/x` per urlparse è un percorso, per il browser è un
    indirizzo assoluto. È la forma con cui questo controllo si aggira."""
    assert rotte._torna_a(richiesta("//evil.example/x")) == ""


def test_gli_schemi_strani_vengono_buttati():
    assert rotte._torna_a(richiesta("javascript:alert(1)")) == ""
    assert rotte._torna_a(richiesta("data:text/html,<b>x</b>")) == ""


def test_senza_referer_non_si_inventa_niente():
    assert rotte._torna_a(richiesta(None)) == ""
    assert rotte._torna_a(richiesta("")) == ""


def test_non_si_torna_al_modulo_stesso():
    """Salvare e ritrovarsi il modulo vuoto davanti sembra che non sia successo
    niente — ed è il momento in cui uno registra la spesa una seconda volta."""
    assert rotte._torna_a(richiesta("https://mymoney.example.app/finanze/nuovo")) == ""


def test_il_ripiego_e_finanze():
    """Quello che `_torna_a` scarta diventa «/finanze» nel contesto, non una
    stringa vuota: un campo `next` vuoto manderebbe il salvataggio alla radice
    del sito, che è un posto in cui non si voleva andare."""
    assert rotte._ctx_modulo("")["next_url"] == "/finanze"
    assert rotte._ctx_modulo("/portafoglio")["next_url"] == "/portafoglio"


# ── i conti a zero non si mostrano (griglia del telefono) ───────────────────
# Quattro riquadri che dicono «€ 0,00» si prendevano metà della griglia per non
# dire niente. Nascosti, non spariti: la pagina li elenca sotto per nome.

def _conto(nome, saldo):
    return {"w": type("W", (), {"nome": nome, "colore": ""})(), "saldo": saldo}


def test_i_conti_a_zero_finiscono_fra_i_vuoti():
    visti, vuoti = rotte._conti_da_mostrare(
        [_conto("Trade Republic", 1183.0), _conto("AIB", 0.0), _conto("Hype", 26.28)])
    assert [r["w"].nome for r in visti] == ["Trade Republic", "Hype"]
    assert [r["w"].nome for r in vuoti] == ["AIB"]


def test_un_conto_in_rosso_non_e_un_conto_vuoto():
    """Un saldo negativo è la cosa che si deve vedere per PRIMA. Un controllo
    scritto come «non è positivo» lo nasconderebbe proprio quando serve."""
    visti, vuoti = rotte._conti_da_mostrare([_conto("Contanti", -12.40)])
    assert [r["w"].nome for r in visti] == ["Contanti"]
    assert vuoti == []


def test_gli_spiccioli_sotto_il_centesimo_contano_come_zero():
    """0,004 € si scrive comunque «€ 0,00»: mostrarlo sarebbe la stessa riga
    vuota con una scusa in più."""
    visti, vuoti = rotte._conti_da_mostrare([_conto("PayPal", 0.004)])
    assert visti == []
    assert [r["w"].nome for r in vuoti] == ["PayPal"]


def test_un_centesimo_vero_invece_si_vede():
    visti, _ = rotte._conti_da_mostrare([_conto("PayPal", 0.01)])
    assert [r["w"].nome for r in visti] == ["PayPal"]


def test_l_ordine_dei_conti_non_cambia():
    """Arrivano ordinati per saldo dal service: rimescolarli vorrebbe dire due
    idee diverse di «conto principale» fra la Home e Finanze."""
    visti, _ = rotte._conti_da_mostrare(
        [_conto("A", 100.0), _conto("B", 0.0), _conto("C", 50.0), _conto("D", 10.0)])
    assert [r["w"].nome for r in visti] == ["A", "C", "D"]


def test_un_saldo_mancante_conta_come_zero():
    """Meglio nasconderlo che stampare «€ 0,00» per un dato che non c'è."""
    visti, vuoti = rotte._conti_da_mostrare([{"w": type("W", (), {"nome": "X"})(), "saldo": None}])
    assert visti == []
    assert len(vuoti) == 1


# ---------------------------------------------------------------------------
#  I FOGLI DI STILE DEL TELEFONO
#
#  Un CSS rotto non e' un errore: il browser scarta in SILENZIO tutto quello
#  che viene dopo il punto in cui si e' perso. E' successo davvero — una riga
#  di prosa finita fuori dal commento ha buttato via meta' di `modulo.css`, e
#  dal browser sembrava semplicemente che la regola «non ci fosse».
#
#  E vanno agganciati UNO PER UNO con la loro impronta: quando erano tirati
#  dentro da `@import` il file che li importava cambiava indirizzo a ogni
#  deploy ma loro no, e il browser continuava a servire i vecchi.
# ---------------------------------------------------------------------------
FOGLI_TEL = ["token.css", "guscio.css", "componenti.css", "modulo.css", "agente.css"]


def _static(*p):
    return Path(__file__).resolve().parent.parent / "static" / Path(*p)


@pytest.mark.parametrize("nome", FOGLI_TEL)
def test_i_fogli_del_telefono_sono_sani(nome):
    testo = _static("telefono", nome).read_text(encoding="utf-8")
    # commenti aperti e chiusi in pari: e' cosi' che si e' rotto
    assert testo.count("/*") == testo.count("*/"), f"{nome}: commenti sbilanciati"
    # niente prosa fuori dai commenti: tolgo i commenti e non devono restare
    # frasi con l'accento o la chiusura orfana
    import re
    senza = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    assert "*/" not in senza, f"{nome}: c'e' una chiusura di commento orfana"
    # graffe in pari
    assert senza.count("{") == senza.count("}"), f"{nome}: graffe sbilanciate"


def test_i_fogli_sono_agganciati_uno_per_uno_con_la_loro_impronta():
    base = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(encoding="utf-8")
    for nome in FOGLI_TEL:
        atteso = f'/static/telefono/{nome}?v={{{{ V }}}}'
        assert atteso in base, f"{nome} non e' agganciato con la sua impronta"
    # e nessuno si affida piu' al vecchio ingresso con gli @import
    assert "/static/telefono.css?v=" not in base


# ---------------------------------------------------------------------------
#  IL DESIGN FREEZE NON SI TOCCA
#
#  `styles.css`, `mymoney.css` e i `tokens/` sono copiati verbatim
#  dall'handoff e si sostituiscono IN BLOCCO quando il design cambia. Quello
#  che ci viene scritto dentro se ne va con loro: senza un errore, senza un
#  test rosso, senza che nessuno se ne accorga finche' non guarda la pagina.
#
#  Era gia' successo: il 23/07/2026 quarantuno righe per la leggibilita' del
#  testo dell'agente erano finite dentro `mymoney.css`. Ora stanno in
#  `aggiunte.css`, che e' nostro. Questo test e' il guardiano.
# ---------------------------------------------------------------------------
FOGLI_FREEZE = [
    ("mymoney.css", ""), ("styles.css", ""),
    ("colors.css", "tokens"), ("fonts.css", "tokens"), ("glass.css", "tokens"),
    ("motion.css", "tokens"), ("radii.css", "tokens"), ("scenes.css", "tokens"),
    ("shadows.css", "tokens"), ("spacing.css", "tokens"), ("typography.css", "tokens"),
]


@pytest.mark.parametrize("nome,sotto", FOGLI_FREEZE)
def test_il_freeze_e_identico_all_handoff(nome, sotto):
    radice = Path(__file__).resolve().parent.parent.parent
    handoff = radice / "design_handoff_mymoney" / "styles" / sotto / nome
    nostro = radice / "app" / "static" / sotto / nome
    if not handoff.exists():
        pytest.skip(f"{nome} non e' nell'handoff")
    a = handoff.read_text(encoding="utf-8")
    b = nostro.read_text(encoding="utf-8")
    assert a == b, (
        f"{nome} si e' allontanato dall'handoff. Se serve una regola nuova va "
        f"in app/static/aggiunte.css, non qui dentro: questo file verra' "
        f"sostituito in blocco e la regola sparirebbe in silenzio."
    )


def test_le_aggiunte_sono_agganciate():
    base = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(encoding="utf-8")
    assert "/static/aggiunte.css?v={{ V }}" in base


# ---------------------------------------------------------------------------
#  LO STILE SCRITTO NEL TAG NON SI PUO' CORREGGERE
#  Terza volta che costa tempo: un `style="..."` vince su qualunque foglio,
#  sempre, e le regole del telefono restano scritte senza fare niente. Nella
#  partita di giro era la ✕ appoggiata sopra il primo campo — una regola
#  esisteva dal 14/08 e non ha mai avuto effetto.
# ---------------------------------------------------------------------------
def _modulo_movimento() -> str:
    return (Path(__file__).resolve().parent.parent / "templates"
            / "finance_movement_form.html").read_text(encoding="utf-8")


def test_le_scatole_del_giro_non_hanno_stile_nel_tag():
    html = _modulo_movimento()
    for riga in html.splitlines():
        if "giro-row" in riga or "giro-rm" in riga or "giro-griglia" in riga:
            assert "style=" not in riga, (
                "Le misure di questa scatola vanno in app/static/telefono/"
                "modulo.css: scritte qui il telefono non puo' cambiarle.\n"
                + riga.strip()
            )


def test_il_foglio_del_modulo_le_definisce():
    css = (Path(__file__).resolve().parent.parent / "static" / "telefono"
           / "modulo.css").read_text(encoding="utf-8")
    for classe in (".giro-row", ".giro-rm", ".giro-griglia", ".tel-tipo-tendina"):
        assert classe in css, f"{classe} non e' definita da nessuna parte"
