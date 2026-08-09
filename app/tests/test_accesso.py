"""La serratura: biglietti di sessione, lista di chi può entrare, codice a 6 cifre.

Qui i test importanti sono i **no**. Un login che lascia entrare chi deve entrare
si vede subito; uno che lascia entrare anche gli altri non si vede mai.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from shared import sicurezza


class FintaRichiesta:
    """Il minimo che serve ad auth: i cookie che il browser rimanda."""

    def __init__(self, cookies=None):
        self.cookies = cookies or {}


# ── il biglietto di sessione ────────────────────────────────────────────────

def test_il_biglietto_torna_indietro_intero():
    b = sicurezza.firma_biglietto({"email": "tizio@example.com"}, "chiave-x", 60)
    corpo = sicurezza.leggi_biglietto(b, "chiave-x")
    assert corpo["email"] == "tizio@example.com"


def test_un_biglietto_modificato_non_vale():
    """Il contenuto è leggibile da chiunque: la difesa è la firma, non il segreto."""
    b = sicurezza.firma_biglietto({"email": "tizio@example.com"}, "chiave-x", 60)
    grezzo, _, firma = b.rpartition(".")
    # provo a spacciarmi per un altro riusando la firma buona
    altro = sicurezza.firma_biglietto({"email": "ladro@example.com"}, "altra", 60)
    falso = altro.rpartition(".")[0] + "." + firma
    assert sicurezza.leggi_biglietto(falso, "chiave-x") is None


def test_con_la_chiave_sbagliata_non_vale():
    b = sicurezza.firma_biglietto({"email": "tizio@example.com"}, "chiave-x", 60)
    assert sicurezza.leggi_biglietto(b, "chiave-y") is None


def test_un_biglietto_scaduto_non_vale():
    b = sicurezza.firma_biglietto({"email": "tizio@example.com"}, "chiave-x", -1)
    assert sicurezza.leggi_biglietto(b, "chiave-x") is None


@pytest.mark.parametrize("spazzatura", ["", "  ", "senza-punto", "a.b", "...",
                                        "eyJhIjoxfQ.firma-inventata"])
def test_la_spazzatura_non_fa_esplodere_niente(spazzatura):
    """Chi arriva da fuori manda quello che vuole: deve tornare «non sei
    entrato», non un errore del server."""
    assert sicurezza.leggi_biglietto(spazzatura, "chiave-x") is None


# ── il codice a sei cifre ───────────────────────────────────────────────────

def test_il_codice_giusto_passa_e_gli_altri_no():
    s = sicurezza.nuovo_segreto()
    ora = time.time()
    assert sicurezza.codice_valido(s, sicurezza.codice_atteso(s, ora), ora)
    assert not sicurezza.codice_valido(s, "000000", ora)
    assert not sicurezza.codice_valido(s, "12345", ora)      # troppo corto
    assert not sicurezza.codice_valido(s, "abcdef", ora)
    assert not sicurezza.codice_valido(s, "", ora)


def test_tollera_gli_orologi_un_po_sfasati_ma_non_troppo():
    s = sicurezza.nuovo_segreto()
    ora = time.time()
    prima = sicurezza.codice_atteso(s, ora - sicurezza.PASSO)
    assert sicurezza.codice_valido(s, prima, ora), "una finestra indietro va accettata"
    vecchio = sicurezza.codice_atteso(s, ora - sicurezza.PASSO * 5)
    assert not sicurezza.codice_valido(s, vecchio, ora), "un codice vecchio no"


def test_due_segreti_diversi_non_si_aprono_a_vicenda():
    a, b = sicurezza.nuovo_segreto(), sicurezza.nuovo_segreto()
    ora = time.time()
    assert not sicurezza.codice_valido(b, sicurezza.codice_atteso(a, ora), ora)


def test_il_codice_e_di_sei_cifre():
    s = sicurezza.nuovo_segreto()
    c = sicurezza.codice_atteso(s)
    assert len(c) == 6 and c.isdigit()


# ── chi può entrare ─────────────────────────────────────────────────────────

def _auth_con(monkeypatch, chiave="", ammessi=()):
    """Ricarica auth con una configurazione decisa dal test."""
    import shared.config as cfg
    import shared.auth as auth
    monkeypatch.setattr(cfg, "SESSION_KEY", chiave)
    monkeypatch.setattr(cfg, "EMAIL_CONSENTITE", list(ammessi))
    monkeypatch.setattr(auth, "SESSION_KEY", chiave)
    monkeypatch.setattr(auth, "EMAIL_CONSENTITE", list(ammessi))
    return auth


def test_senza_chiave_siamo_sul_pc_di_casa(monkeypatch):
    """Nessuna chiave configurata = nessun login, come è sempre stato."""
    auth = _auth_con(monkeypatch, chiave="")
    assert auth.richiede_accesso() is False
    assert auth.utente_da_richiesta(FintaRichiesta()).is_local


def test_online_senza_biglietto_non_si_entra(monkeypatch):
    auth = _auth_con(monkeypatch, chiave="k", ammessi=["lorenzo@example.com"])
    assert auth.richiede_accesso() is True
    assert auth.utente_da_richiesta(FintaRichiesta()) is None


def test_lista_vuota_vuol_dire_nessuno(monkeypatch):
    """Se l'app è online e la lista è vuota non entra nemmeno il proprietario:
    meglio chiusi fuori che aperti a tutti."""
    auth = _auth_con(monkeypatch, chiave="k", ammessi=[])
    biglietto = auth.crea_biglietto("lorenzo@example.com", completo=True)
    req = FintaRichiesta({auth.NOME_COOKIE: biglietto})
    assert auth.utente_da_richiesta(req) is None


def test_un_altro_account_google_non_entra(monkeypatch):
    """È il punto del discorso: avere un account Google non basta."""
    auth = _auth_con(monkeypatch, chiave="k", ammessi=["lorenzo@example.com"])
    biglietto = auth.crea_biglietto("sconosciuto@gmail.com", completo=True)
    req = FintaRichiesta({auth.NOME_COOKIE: biglietto})
    assert auth.utente_da_richiesta(req) is None


def test_chi_e_nella_lista_entra(monkeypatch):
    auth = _auth_con(monkeypatch, chiave="k", ammessi=["Lorenzo@Example.com"])
    biglietto = auth.crea_biglietto("lorenzo@example.com", completo=True)
    u = auth.utente_da_richiesta(FintaRichiesta({auth.NOME_COOKIE: biglietto}))
    assert u is not None and u.email == "lorenzo@example.com"


def test_il_biglietto_a_meta_non_apre_l_app(monkeypatch):
    """Passato Google ma non ancora il codice: non è «essere entrati»."""
    auth = _auth_con(monkeypatch, chiave="k", ammessi=["lorenzo@example.com"])
    mezzo = auth.crea_biglietto("lorenzo@example.com", completo=False)
    req = FintaRichiesta({auth.NOME_COOKIE: mezzo})
    assert auth.utente_da_richiesta(req) is None
    assert auth.email_parziale(req) == "lorenzo@example.com"


def test_togliere_dalla_lista_chiude_fuori_subito(monkeypatch):
    """Chi ha già il biglietto in tasca non deve restare dentro per due
    settimane dopo essere stato tolto dalla lista."""
    auth = _auth_con(monkeypatch, chiave="k", ammessi=["lorenzo@example.com"])
    biglietto = auth.crea_biglietto("lorenzo@example.com", completo=True)
    req = FintaRichiesta({auth.NOME_COOKIE: biglietto})
    assert auth.utente_da_richiesta(req) is not None
    monkeypatch.setattr(auth, "EMAIL_CONSENTITE", [])
    assert auth.utente_da_richiesta(req) is None


# ── la porta: cosa resta aperto ─────────────────────────────────────────────

@pytest.mark.parametrize("percorso", [
    "/", "/finanze", "/finanze/movimenti", "/portafoglio", "/portafoglio/38",
    "/pac", "/analisi", "/notizie", "/impostazioni",
    "/finanze/movimenti/1/dettaglio", "/api/qualcosa", "/pagina-inventata",
])
def test_le_pagine_dell_app_sono_chiuse(percorso):
    """Il cuore della cosa: tutto quello che mostra dati sta dietro la porta.
    Se un domani si aggiunge una pagina, questa lista non va aggiornata —
    nasce chiusa da sola, perché l'elenco è di ciò che si APRE."""
    from shared import auth
    assert auth.percorso_libero(percorso) is False, f"{percorso} risulta aperta!"


@pytest.mark.parametrize("percorso", [
    "/salute", "/accedi", "/accedi/google", "/accedi/google/ritorno",
    "/accedi/attiva", "/accedi/codice", "/esci",
    "/static/app.js", "/static/img/logo.png",
])
def test_solo_l_ingresso_e_la_grafica_sono_aperti(percorso):
    from shared import auth
    assert auth.percorso_libero(percorso) is True, f"{percorso} risulta chiusa!"


def test_il_segreto_del_secondo_fattore_non_esce_dal_server():
    """Il backup porta via movimenti, conti e categorie — non le impostazioni.
    È così oggi, e questo test esiste perché resti così: il giorno in cui
    qualcuno aggiungesse le impostazioni alla fotografia, il segreto del secondo
    fattore finirebbe dentro un file scaricabile, e il gradino in più smetterebbe
    di essere un gradino."""
    import json
    from shared import auth, backup
    auth.imposta_segreto_totp(sicurezza.nuovo_segreto())
    fotografia = json.dumps(backup.build_snapshot(), default=str)
    assert auth.CHIAVE_TOTP not in fotografia
    assert auth.segreto_totp() not in fotografia


def test_i_lavori_passano_ma_hanno_la_loro_serratura():
    """Lo chiama un programma, non una persona: non può fare il login. Passa
    la porta ma poi deve mostrare la sua parola d'ordine (shared/lavori.py)."""
    from shared import auth, lavori
    assert auth.percorso_libero("/lavori/giornaliero") is True
    assert lavori.token_valido("") is False


def test_un_altro_account_google_non_entra_bis(monkeypatch):
    """Ripetuto qui di proposito: è il rifiuto che conta di più."""
    auth = _auth_con(monkeypatch, chiave="k", ammessi=["lorenzo@example.com"])
    for estraneo in ("mario@gmail.com", "lorenzo@example.com.attacco.it",
                     "LORENZO@EXAMPLE.COM.evil.com", " ", ""):
        b = auth.crea_biglietto(estraneo, completo=True)
        assert auth.utente_da_richiesta(FintaRichiesta({auth.NOME_COOKIE: b})) is None


def test_un_biglietto_di_un_altra_installazione_non_vale(monkeypatch):
    """Firmato con un'altra chiave: da noi non deve aprire niente."""
    auth = _auth_con(monkeypatch, chiave="chiave-nostra", ammessi=["lorenzo@example.com"])
    estraneo = sicurezza.firma_biglietto(
        {"email": "lorenzo@example.com", "completo": True}, "chiave-loro", 3600)
    req = FintaRichiesta({auth.NOME_COOKIE: estraneo})
    assert auth.utente_da_richiesta(req) is None
