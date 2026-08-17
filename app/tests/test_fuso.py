"""Il fuso orario: quale ora è «adesso» per questa app.

Il rischio che questi test difendono non è un orario sbagliato di un'ora — quello
non sposta nessun conto. È il **giorno**: una spesa fatta alle 23:30 a Dublino,
letta con l'orologio italiano, finisce nel giorno dopo, e a fine mese magari nel
mese dopo. Da lì in avanti ogni riepilogo è sbagliato e nessuno se ne accorge.

L'altro rischio, più subdolo, è il MISCUGLIO: due date scritte da due orologi
diversi (il PC, il telefono, il server in UTC) e poi confrontate fra loro.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from shared import settings_store, tempo


# ── la scelta ───────────────────────────────────────────────────────────────

def test_senza_impostazione_si_sta_in_italia(fuso_vero):
    """Il ripiego è l'Italia, non UTC: in locale è quello che l'app ha sempre
    fatto, e sul server evita il caso peggiore — un fuso che non è di nessuno."""
    assert tempo.nome_fuso() == "Europe/Rome"


def test_si_puo_cambiare_e_l_ora_cambia_davvero(fuso_vero):
    prima = tempo.adesso()
    assert tempo.imposta("Europe/Dublin") is True
    assert tempo.nome_fuso() == "Europe/Dublin"
    # Dublino sta un'ora indietro rispetto a Roma (tutto l'anno: cambiano
    # l'ora legale nello stesso giorno)
    scarto = (prima - tempo.adesso()).total_seconds()
    assert 3500 < scarto < 3700, f"scarto inatteso: {scarto}s"


@pytest.mark.parametrize("inventato", ["", "   ", "Europa/Roma", "Mars/Olympus",
                                       "UTC+2", "Europe/Dublin; DROP TABLE"])
def test_un_fuso_inventato_non_viene_accettato(fuso_vero, inventato):
    """Il valore può arrivare dal browser (il pulsante «usa quello del
    dispositivo»): deve essere rifiutato senza lasciare l'app senza orologio."""
    tempo.imposta("Europe/Dublin")
    assert tempo.imposta(inventato) is False
    assert tempo.nome_fuso() == "Europe/Dublin", "il fuso buono è stato perso"


def test_un_valore_marcio_nel_database_non_ferma_l_app(fuso_vero):
    """Scritto a mano, o rimasto da una versione futura: si torna al ripiego,
    non si spegne l'app perché non si sa che ore sono."""
    settings_store.set_setting(tempo.CHIAVE, "Qualcosa/DiRotto")
    tempo.scarta_cache()
    assert tempo.nome_fuso() == "Europe/Rome"
    assert isinstance(tempo.adesso(), datetime)


def test_senza_database_si_usa_l_ambiente(fuso_vero, monkeypatch):
    """Le routine delle email girano fuori dall'app, dove il database non c'è:
    devono comunque sapere che ore sono."""
    def esplode(*a, **k):
        raise RuntimeError("nessun database qui")
    monkeypatch.setattr(settings_store, "get_setting", esplode)
    monkeypatch.setenv("MYMONEY_FUSO", "Europe/Dublin")
    tempo.scarta_cache()
    assert tempo.nome_fuso() == "Europe/Dublin"


def test_l_etichetta_e_leggibile():
    assert tempo.etichetta("Europe/Dublin") == "Irlanda"
    assert tempo.etichetta("America/Sao_Paulo") == "America/Sao Paulo"  # non in elenco


# ── il giorno, che è la cosa che conta ──────────────────────────────────────

def in_ora_locale(epoch):
    """Un istante universale letto nell'ora del fuso scelto, come fa l'app.

    Prima esisteva `tempo.da_epoch` che faceva questo; l'unico posto che la
    usava — il prezzo orario del PAC — adesso confronta istanti universali fra
    loro, senza convertirli a metà strada, e la funzione è rimasta senza
    chiamanti. Quello che va difeso però resta: che `fuso()` porti davvero
    l'ora giusta, ora legale compresa. Quindi la conversione la fa il test.
    """
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(
        tempo.fuso()).replace(tzinfo=None)



def test_a_mezzanotte_e_mezza_italiana_a_dublino_e_ancora_ieri(fuso_vero):
    """Il cuore della faccenda. Lo stesso istante, due Paesi, due GIORNI diversi:
    è il motivo per cui il fuso non può restare quello dell'orologio di turno."""
    istante = datetime(2026, 8, 20, 22, 30, tzinfo=timezone.utc).timestamp()

    tempo.imposta("Europe/Rome")
    a_roma = in_ora_locale(istante)
    tempo.imposta("Europe/Dublin")
    a_dublino = in_ora_locale(istante)

    assert (a_roma.day, a_roma.hour) == (21, 0)      # già il 21, mezzanotte e mezza
    assert (a_dublino.day, a_dublino.hour) == (20, 23)   # ancora il 20, le 23:30
    assert a_roma.date() != a_dublino.date()


def test_oggi_e_il_giorno_del_fuso_scelto(fuso_vero, monkeypatch):
    """`oggi()` non è il giorno del server: è il giorno di dove hai detto di essere."""
    monkeypatch.setattr(tempo, "adesso", lambda: datetime(2026, 8, 20, 23, 30))
    assert tempo.oggi() == date(2026, 8, 20)


def test_le_date_restano_senza_fuso_attaccato(fuso_vero):
    """Tutto il database è naive. Una data «con fuso» che ci finisse dentro non
    darebbe un risultato sbagliato: farebbe esplodere ogni confronto."""
    assert tempo.adesso().tzinfo is None
    assert in_ora_locale(1_786_093_200).tzinfo is None


# ── il miscuglio: date scritte da orologi diversi ───────────────────────────

def test_la_z_del_telefono_viene_riportata_nel_fuso_scelto(fuso_vero):
    """Il telefono manda UTC con la 'Z'. Se il PC e il server la convertissero
    ciascuno col proprio orologio, lo stesso movimento finirebbe a due ore di
    distanza — e sono queste date a decidere «vince il più recente»."""
    con_fuso = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

    tempo.imposta("Europe/Rome")
    assert tempo.a_naive(con_fuso) == datetime(2026, 8, 20, 14, 0)
    tempo.imposta("Europe/Dublin")
    assert tempo.a_naive(con_fuso) == datetime(2026, 8, 20, 13, 0)


def test_una_data_gia_nuda_passa_intatta(fuso_vero):
    nuda = datetime(2026, 8, 20, 12, 0)
    assert tempo.a_naive(nuda) == nuda
    assert tempo.a_naive(None) is None


def test_il_ripristino_legge_le_date_col_fuso_scelto(fuso_vero):
    """Stesso controllo, ma sulla porta da cui le date entrano davvero: un file
    di backup, che può portarsi dietro la 'Z' di UTC."""
    from shared import backup
    tempo.imposta("Europe/Dublin")
    assert backup._parse_dt("2026-08-20T12:00:00Z") == datetime(2026, 8, 20, 13, 0)
    assert backup._parse_dt("2026-08-20T12:00:00") == datetime(2026, 8, 20, 12, 0)
    assert backup._parse_dt("non è una data") is None


# ── l'ora legale: perché non si scrive a mano ───────────────────────────────

def test_l_ora_legale_la_conosce_il_sistema(fuso_vero):
    """D'inverno l'Italia è a +1, d'estate a +2. Una regola scritta a mano vale
    per l'Europa e sbaglia altrove: negli Stati Uniti i cambi cadono in date
    diverse. Qui la sa l'elenco dei fusi, che è aggiornato per tutto il mondo."""
    tempo.imposta("Europe/Rome")
    luglio = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).timestamp()
    dicembre = datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc).timestamp()
    assert in_ora_locale(luglio).hour == 14
    assert in_ora_locale(dicembre).hour == 13

    tempo.imposta("America/New_York")
    assert in_ora_locale(luglio).hour == 8       # EDT, -4
    assert in_ora_locale(dicembre).hour == 7     # EST, -5


# ── dal modulo all'app vera ─────────────────────────────────────────────────

def test_un_movimento_senza_data_prende_l_ora_del_fuso(fuso_vero, monkeypatch):
    """Il giro completo: quello che finisce nel database è l'ora scelta."""
    from shared.db import SessionLocal
    from finance import service as fin
    from finance.models import TIPO_USCITA, Wallet

    tempo.imposta("Europe/Dublin")
    finto = datetime(2026, 8, 20, 23, 30)      # le 23:30 a Dublino: è ancora il 20
    monkeypatch.setattr(tempo, "adesso", lambda: finto)

    with SessionLocal() as db:
        w = Wallet(nome="Prova", tipo="conto", saldo_iniziale=100.0)
        db.add(w)
        db.commit()
        wid = w.id

    tid = fin.crea_movimento(TIPO_USCITA, None, 10.0, wid, descrizione="birra")
    mov = fin.movimento(tid)

    assert mov["t"].data == finto, "la data non viene dal fuso scelto"
    assert mov["t"].data.date() == date(2026, 8, 20), "finito nel giorno sbagliato"


def test_il_modulo_si_precompila_con_l_ora_scelta(fuso_vero, monkeypatch):
    from finance import routes as fr

    monkeypatch.setattr(tempo, "adesso", lambda: datetime(2026, 8, 20, 23, 30))
    assert fr._oggi_local() == "2026-08-20T23:30"


def test_la_pagina_impostazioni_rifiuta_i_fusi_inventati(fuso_vero):
    """La route è il punto in cui arriva roba da fuori."""
    from shared import settings_routes as sr

    tempo.imposta("Europe/Rome")
    risposta = sr.salva_fuso(fuso="Mars/Olympus")
    assert risposta.status_code == 303
    assert "salvato=0" in risposta.headers["location"]
    assert tempo.nome_fuso() == "Europe/Rome"

    risposta = sr.salva_fuso(fuso="Europe/Dublin")
    assert "salvato=1" in risposta.headers["location"]
    assert tempo.nome_fuso() == "Europe/Dublin"


# ── la cache (esiste per non chiedere il database durante un salvataggio) ───

def test_la_cache_non_congela_la_scelta(fuso_vero):
    """Cambiare fuso deve avere effetto subito, senza riavviare l'app."""
    tempo.imposta("Europe/Rome")
    assert tempo.nome_fuso() == "Europe/Rome"
    tempo.imposta("Europe/Dublin")
    assert tempo.nome_fuso() == "Europe/Dublin"


def test_la_cache_scade_da_sola(fuso_vero, monkeypatch):
    """Se il fuso lo cambia un'altra copia dell'app (sul server ce ne può essere
    più di una), questa se ne accorge da sola entro un minuto."""
    tempo.imposta("Europe/Rome")
    assert tempo.nome_fuso() == "Europe/Rome"

    settings_store.set_setting(tempo.CHIAVE, "Europe/Dublin")   # scritto "da fuori"
    assert tempo.nome_fuso() == "Europe/Rome", "senza scadenza non se ne accorgerebbe"

    finto = tempo._adesso_monotono() + tempo._SCADENZA + 1
    monkeypatch.setattr(tempo, "_adesso_monotono", lambda: finto)
    assert tempo.nome_fuso() == "Europe/Dublin"


# ── «oggi» / «ieri» sulla home del telefono ─────────────────────────────────
#
# La home mostra le ultime righe con l'etichetta relativa invece della data.
# È comoda e per questo è pericolosa: un'etichetta sbagliata non si vede — «ieri»
# sembra sempre plausibile — mentre una data sbagliata salterebbe all'occhio.

def test_oggi_e_ieri_guardano_il_giorno_non_le_ore(monkeypatch):
    """Trentacinque minuti prima possono essere «ieri», ventitré ore prima
    «oggi»: conta il giorno, non quanto tempo è passato."""
    monkeypatch.setattr(tempo, "oggi", lambda: date(2026, 8, 9))
    assert tempo.etichetta_giorno(datetime(2026, 8, 9, 0, 5)) == "dash.today"
    assert tempo.etichetta_giorno(datetime(2026, 8, 8, 23, 55)) == "dash.yesterday"
    assert tempo.etichetta_giorno(datetime(2026, 8, 9, 23, 59)) == "dash.today"


def test_l_etichetta_segue_il_fuso_scelto_non_l_orologio_della_macchina(monkeypatch):
    """Il guardiano vero: la data di riferimento arriva da `oggi()`, che sa qual è
    il fuso scelto. Scritta con `date.today()` questa funzione leggerebbe
    l'orologio di chi esegue il codice — il PC di casa, o il server in UTC — e
    vicino a mezzanotte direbbe il giorno di qualcun altro."""
    lontano = date(2020, 3, 15)                       # non è oggi, e non lo sarà mai più
    monkeypatch.setattr(tempo, "oggi", lambda: lontano)
    assert tempo.etichetta_giorno(datetime(2020, 3, 15, 10, 0)) == "dash.today"
    assert tempo.etichetta_giorno(datetime(2020, 3, 14, 10, 0)) == "dash.yesterday"


def test_piu_vecchio_di_ieri_vuole_la_data(monkeypatch):
    """None non è un caso limite: è il segnale al template di scrivere «07/08».
    Senza, la home direbbe «ieri» a movimenti di un mese fa."""
    monkeypatch.setattr(tempo, "oggi", lambda: date(2026, 8, 9))
    assert tempo.etichetta_giorno(datetime(2026, 8, 7, 12, 0)) is None
    assert tempo.etichetta_giorno(datetime(2026, 7, 9, 12, 0)) is None


def test_accetta_sia_una_data_sia_una_data_con_ora(monkeypatch):
    """I movimenti hanno l'ora, altre cose no: passare una `date` non deve
    esplodere."""
    monkeypatch.setattr(tempo, "oggi", lambda: date(2026, 8, 9))
    assert tempo.etichetta_giorno(date(2026, 8, 9)) == "dash.today"
    assert tempo.etichetta_giorno(datetime(2026, 8, 9, 7, 0)) == "dash.today"


def test_le_chiavi_che_restituisce_esistono_in_tutte_le_lingue(monkeypatch):
    """Restituisce chiavi i18n: se una non esistesse, la home stamperebbe la
    chiave nuda al posto della parola, e solo in una lingua su sei."""
    from shared import i18n
    monkeypatch.setattr(tempo, "oggi", lambda: date(2026, 8, 9))
    chiavi = {tempo.etichetta_giorno(datetime(2026, 8, 9)),
              tempo.etichetta_giorno(datetime(2026, 8, 8))}
    for k in chiavi:
        assert k in i18n.STRINGS, f"chiave assente: {k}"
        for lingua in ("it", "en", "es", "fr", "de", "uk"):
            assert i18n.STRINGS[k].get(lingua), f"{k} manca in {lingua}"


# ── la sentinella: nessuno legga l'orologio della macchina ───────────────────
# Tutto il resto di questo file difende UN punto per volta. Questo difende la
# REGOLA, e lo fa leggendo il codice invece che eseguendolo: `shared/tempo.py`
# è l'unico posto autorizzato a chiedere che ore sono, perché è l'unico che
# passa dal fuso scelto in Impostazioni.
#
# Serve perché la regola si dimentica. In produzione era già rispettata; nei
# test no, e per un motivo che sembrava innocuo — «tanto è una data di prova».
# Ma il codice sotto test confronta con `tempo.adesso()`, e con l'app impostata
# su Roma e il PC in Irlanda i due orologi distano un'ora: fra le 23 e
# mezzanotte `date.today()` e `tempo.oggi()` sono due giorni diversi, e il test
# falliva a seconda dell'ora in cui lo si lanciava. Un test che fallisce a caso
# è peggio di un test che manca: insegna a rilanciare invece che a guardare.
#
# Guarda l'ALBERO SINTATTICO, non il testo. Prima cercava le due stringhe riga
# per riga, e il primo colpevole era una frase in italiano dentro una docstring
# qui sopra, che di orologi ne legge zero. Un controllo che accusa la prosa lo
# si finisce per zittire, e zittito non difende più niente. Con `ast` restano
# solo le chiamate vere: commenti, docstring e nomi di variabile non lo toccano.

def test_solo_tempo_py_puo_guardare_l_orologio_della_macchina():
    import ast
    from pathlib import Path

    # (oggetto, metodo) delle chiamate che leggono l'orologio di sistema.
    VIETATE = {("date", "today"), ("datetime", "now")}
    APP = Path(__file__).resolve().parent.parent
    ESENTI = {APP / "shared" / "tempo.py"}       # l'unica fonte autorizzata

    colpevoli = []
    for f in sorted(APP.rglob("*.py")):
        if f in ESENTI or ".venv" in f.parts or "__pycache__" in f.parts:
            continue
        albero = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for nodo in ast.walk(albero):
            if not isinstance(nodo, ast.Call):
                continue
            fn = nodo.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)):
                continue
            if (fn.value.id, fn.attr) not in VIETATE:
                continue
            # `datetime.now(fuso())` con un fuso ESPLICITO è un'altra cosa: è
            # quello che fa tempo.adesso(), e non guarda l'orologio di sistema.
            if nodo.args or nodo.keywords:
                continue
            colpevoli.append(f"{f.relative_to(APP)}:{nodo.lineno}  "
                             f"{fn.value.id}.{fn.attr}()")

    assert not colpevoli, (
        "l'ora va chiesta a shared/tempo.py (adesso/oggi), non alla macchina:\n"
        + "\n".join(colpevoli))
