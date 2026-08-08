"""Le pagine della porta: /accedi, il ritorno da Google, il codice, l'uscita.

`test_accesso.py` prova le **regole** (biglietti, lista, codici). Qui si provano
le **rotte**, cioè il punto in cui quelle regole vengono davvero applicate: una
regola giusta chiamata nel posto sbagliato non protegge niente.

Niente server e niente `TestClient`: le rotte sono funzioni normali e si
chiamano come tali, con una `Request` vera costruita a mano. Così i test non
hanno bisogno di `httpx` — una libreria in più installata solo per loro — e
restano veloci.

Come sopra, quello che conta sono i rifiuti.
"""
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from starlette.requests import Request

from shared import accesso, accesso_routes as rotte, auth, sicurezza

CHIAVE = "chiave-di-prova"
IO = "lorenzo@example.com"
CLIENT = "123.apps.googleusercontent.com"


# ── impianto ────────────────────────────────────────────────────────────────

@pytest.fixture
def online(monkeypatch):
    """L'app come sta sul server: chiave di sessione, lista di uno, Google
    configurato. Le costanti vanno rimesse in TUTTI i moduli che se ne sono
    presi una copia all'import — sono tre, e dimenticarne uno fa passare un
    test che non prova niente."""
    import shared.config as cfg
    for mod, nome, val in (
        (cfg, "SESSION_KEY", CHIAVE), (auth, "SESSION_KEY", CHIAVE),
        (rotte, "SESSION_KEY", CHIAVE),
        (cfg, "EMAIL_CONSENTITE", [IO]), (auth, "EMAIL_CONSENTITE", [IO]),
        (cfg, "OAUTH_CLIENT_ID", CLIENT), (accesso, "OAUTH_CLIENT_ID", CLIENT),
        (cfg, "OAUTH_CLIENT_SECRET", "segreto"),
        (accesso, "OAUTH_CLIENT_SECRET", "segreto"),
        (cfg, "BASE_URL", "https://mymoney.example.app"),
        (accesso, "BASE_URL", "https://mymoney.example.app"),
    ):
        monkeypatch.setattr(mod, nome, val)
    return True


def richiesta(percorso="/accedi", cookies=None, https=True):
    """Una `Request` di Starlette autentica, costruita dal suo scope."""
    intestazioni = [(b"host", b"mymoney.example.app")]
    if https:
        intestazioni.append((b"x-forwarded-proto", b"https"))
    if cookies:
        biscotti = "; ".join(f"{k}={v}" for k, v in cookies.items())
        intestazioni.append((b"cookie", biscotti.encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": percorso, "raw_path": percorso.encode(),
        "query_string": b"", "root_path": "", "headers": intestazioni,
        "client": ("203.0.113.7", 5555), "server": ("mymoney.example.app", 443),
    })


def cookie_messi(resp) -> dict:
    """I cookie che la risposta dice al browser di tenere: {nome: valore}."""
    fuori = {}
    for chiave, valore in resp.raw_headers:
        if chiave.lower() != b"set-cookie":
            continue
        primo = valore.decode().split(";")[0]
        nome, _, val = primo.partition("=")
        fuori[nome.strip()] = val.strip()
    return fuori


def dove_manda(resp) -> str:
    return resp.headers.get("location", "")


def biglietto_giro(**dati) -> str:
    return sicurezza.firma_biglietto(dati, CHIAVE, 600)


# Chiamando le rotte come funzioni normali, i valori dei campi del modulo li
# passiamo noi: `Form("/")` è un default che riempie FastAPI, non Python.
def manda_codice(req, codice, next="/"):
    return rotte.verifica_codice(req, codice=codice, next=next)


def manda_attiva(req, codice, next="/"):
    return rotte.conferma_attiva(req, codice=codice, next=next)


# ── /accedi ─────────────────────────────────────────────────────────────────

def test_sul_pc_di_casa_la_porta_non_esiste(monkeypatch):
    """Senza chiave di sessione non c'è login: /accedi non deve mostrare una
    serratura finta, deve mandare dentro."""
    monkeypatch.setattr(auth, "SESSION_KEY", "")
    resp = rotte.accedi(richiesta())
    assert resp.status_code == 303 and dove_manda(resp) == "/"


def test_chi_e_gia_dentro_non_rivede_la_porta(online):
    req = richiesta(cookies={auth.NOME_COOKIE: auth.crea_biglietto(IO, True)})
    assert dove_manda(rotte.accedi(req)) == "/"


def test_la_porta_si_apre_a_chi_non_e_entrato(online):
    resp = rotte.accedi(richiesta())
    assert resp.status_code == 200


def test_senza_le_credenziali_di_google_non_si_parte(online, monkeypatch):
    """Meglio dire «non si può» che mandare l'utente su un indirizzo rotto."""
    monkeypatch.setattr(accesso, "OAUTH_CLIENT_ID", "")
    resp = rotte.accedi_google(richiesta())
    assert dove_manda(resp) == "/accedi?errore=1"


def test_il_giro_parte_e_si_porta_dietro_lo_stato(online):
    resp = rotte.accedi_google(richiesta())
    verso = dove_manda(resp)
    assert verso.startswith(accesso.AUTH_URL)
    # il «dove torno» deve essere IDENTICO a quello registrato su Google
    assert "redirect_uri=https%3A%2F%2Fmymoney.example.app%2Faccedi%2Fgoogle%2Fritorno" in verso
    assert "code_challenge_method=S256" in verso
    # lo stato viaggia in un cookie firmato, non in memoria: su Cloud Run il
    # ritorno può bussare a un'altra istanza
    giro = sicurezza.leggi_biglietto(cookie_messi(resp)[rotte.NOME_COOKIE_GIRO], CHIAVE)
    assert giro["state"] and giro["verifier"] and giro["nonce"]
    assert f"state={giro['state']}" in verso


def test_il_segreto_di_pkce_non_esce_mai_verso_google(online):
    """A Google va l'impronta, non l'originale. Se uscisse il verifier, PKCE
    smetterebbe di servire a qualcosa."""
    resp = rotte.accedi_google(richiesta())
    giro = sicurezza.leggi_biglietto(cookie_messi(resp)[rotte.NOME_COOKIE_GIRO], CHIAVE)
    assert giro["verifier"] not in dove_manda(resp)


# ── il ritorno da Google ────────────────────────────────────────────────────

def _torna(monkeypatch, esito=(IO, ""), **kw):
    """Il ritorno da Google, con la risposta di Google decisa dal test."""
    monkeypatch.setattr(accesso, "chi_e_tornato", lambda *a, **k: esito)
    return rotte.ritorno_da_google(**kw)


def test_senza_giro_aperto_non_si_entra(online, monkeypatch):
    """Un ritorno che arriva senza che noi abbiamo iniziato niente."""
    resp = _torna(monkeypatch, request=richiesta(), code="x", state="s")
    assert dove_manda(resp) == "/accedi?errore=1"


def test_uno_stato_che_non_torna_non_si_entra(online, monkeypatch):
    """È il controllo che respinge un giro confezionato da qualcun altro."""
    req = richiesta(cookies={rotte.NOME_COOKIE_GIRO: biglietto_giro(
        state="mio", verifier="v", nonce="n")})
    resp = _torna(monkeypatch, request=req, code="x", state="suo")
    assert dove_manda(resp) == "/accedi?errore=1"
    assert auth.NOME_COOKIE not in cookie_messi(resp)


def test_uno_stato_vuoto_non_passa_per_caso(online, monkeypatch):
    """Se il confronto fosse scritto male, due vuoti sarebbero «uguali»."""
    req = richiesta(cookies={rotte.NOME_COOKIE_GIRO: biglietto_giro(
        state="", verifier="v", nonce="n")})
    resp = _torna(monkeypatch, request=req, code="x", state="")
    assert dove_manda(resp) == "/accedi?errore=1"


def test_un_altro_account_google_viene_respinto(online, monkeypatch):
    """Il rifiuto che conta di più: Google l'ha riconosciuto davvero, ma non è
    nella lista. Un account Google ce l'ha mezzo mondo."""
    req = richiesta(cookies={rotte.NOME_COOKIE_GIRO: biglietto_giro(
        state="s", verifier="v", nonce="n")})
    resp = _torna(monkeypatch, esito=("estraneo@gmail.com", ""),
                  request=req, code="x", state="s")
    assert dove_manda(resp) == "/accedi?errore=1"
    assert auth.NOME_COOKIE not in cookie_messi(resp)


def test_chi_e_nella_lista_arriva_al_codice_e_non_oltre(online, monkeypatch):
    auth.imposta_segreto_totp(sicurezza.nuovo_segreto())
    req = richiesta(cookies={rotte.NOME_COOKIE_GIRO: biglietto_giro(
        state="s", verifier="v", nonce="n")})
    resp = _torna(monkeypatch, request=req, code="x", state="s")
    assert dove_manda(resp).startswith("/accedi/codice")
    # il biglietto consegnato è PARZIALE: non apre l'app
    dato = cookie_messi(resp)[auth.NOME_COOKIE]
    assert auth.utente_da_richiesta(richiesta(cookies={auth.NOME_COOKIE: dato})) is None
    assert auth.email_parziale(richiesta(cookies={auth.NOME_COOKIE: dato})) == IO


def test_la_prima_volta_si_passa_dall_attivazione(online, monkeypatch):
    req = richiesta(cookies={rotte.NOME_COOKIE_GIRO: biglietto_giro(
        state="s", verifier="v", nonce="n")})
    resp = _torna(monkeypatch, request=req, code="x", state="s")
    assert dove_manda(resp).startswith("/accedi/attiva")


def test_chi_dice_no_a_google_torna_indietro_senza_allarmi(online, monkeypatch):
    """Aver cambiato idea non è un tentativo di intrusione."""
    resp = _torna(monkeypatch, request=richiesta(), error="access_denied")
    assert dove_manda(resp) == "/accedi"


# ── il documento che Google ci consegna ─────────────────────────────────────

def _id_token(**campi) -> str:
    corpo = {"aud": CLIENT, "iss": "https://accounts.google.com",
             "exp": time.time() + 300, "email_verified": True,
             "email": IO, "nonce": "n"}
    corpo.update(campi)
    grezzo = base64.urlsafe_b64encode(
        json.dumps(corpo).encode()).decode().rstrip("=")
    return f"testa.{grezzo}.firma"


def test_il_documento_in_regola_dice_chi_sei(online):
    assert accesso._leggi_id_token(_id_token(), "n") == (IO, "")


@pytest.mark.parametrize("campi, motivo", [
    ({"aud": "un-altra-app.apps.googleusercontent.com"}, "destinatario"),
    ({"iss": "https://accounts.evil.com"}, "emittente"),
    ({"exp": time.time() - 10}, "scaduto"),
    ({"nonce": "di-un-altro-giro"}, "giro"),
    ({"email_verified": False}, "nonverificata"),
    ({"email": ""}, "senzaemail"),
])
def test_un_documento_storto_non_dice_niente(online, campi, motivo):
    """`email_verified` falso è il caso subdolo: l'indirizzo c'è, ma Google non
    l'ha mai controllato — e la nostra serratura è una lista di indirizzi."""
    email, err = accesso._leggi_id_token(_id_token(**campi), "n")
    assert email == "" and err == motivo


@pytest.mark.parametrize("spazzatura", ["", "niente-punti", "a.b", "a.!!!.c",
                                        "a." + base64.urlsafe_b64encode(b"[1,2]").decode() + ".c"])
def test_la_spazzatura_al_posto_del_documento_non_esplode(online, spazzatura):
    assert accesso._leggi_id_token(spazzatura, "n")[0] == ""


# ── il codice a sei cifre ───────────────────────────────────────────────────

def _con_parziale(percorso="/accedi/codice"):
    return richiesta(percorso, cookies={
        auth.NOME_COOKIE: auth.crea_biglietto(IO, completo=False)})


def test_al_codice_non_ci_si_arriva_dalla_strada(online):
    """Senza biglietto parziale la pagina del codice non esiste."""
    auth.imposta_segreto_totp(sicurezza.nuovo_segreto())
    assert dove_manda(rotte.pagina_codice(richiesta())) == "/accedi"
    assert dove_manda(manda_codice(richiesta(), "000000")) == "/accedi"


def test_un_biglietto_completo_non_vale_come_parziale(online):
    """Chi è già dentro non deve poter rigiocare la pagina del codice."""
    auth.imposta_segreto_totp(sicurezza.nuovo_segreto())
    req = richiesta(cookies={auth.NOME_COOKIE: auth.crea_biglietto(IO, True)})
    assert dove_manda(rotte.pagina_codice(req)) == "/accedi"


def test_il_codice_sbagliato_lascia_fuori(online):
    auth.imposta_segreto_totp(sicurezza.nuovo_segreto())
    resp = manda_codice(_con_parziale(), "000000")
    assert dove_manda(resp).endswith("errore=1")
    assert auth.NOME_COOKIE not in cookie_messi(resp)


def test_il_codice_giusto_fa_entrare(online):
    segreto = sicurezza.nuovo_segreto()
    auth.imposta_segreto_totp(segreto)
    resp = manda_codice(_con_parziale(), sicurezza.codice_atteso(segreto), "/finanze")
    assert dove_manda(resp) == "/finanze"
    dato = cookie_messi(resp)[auth.NOME_COOKIE]
    utente = auth.utente_da_richiesta(richiesta(cookies={auth.NOME_COOKIE: dato}))
    assert utente is not None and utente.email == IO


def test_dopo_troppi_errori_si_aspetta(online):
    """Un milione di combinazioni si provano, se nessuno lo impedisce — e nel
    frattempo il server lo paghiamo noi."""
    segreto = sicurezza.nuovo_segreto()
    auth.imposta_segreto_totp(segreto)
    for _ in range(accesso.MAX_TENTATIVI):
        manda_codice(_con_parziale(), "000000")
    assert accesso.in_pausa() > 0
    # ora nemmeno quello giusto passa
    resp = manda_codice(_con_parziale(), sicurezza.codice_atteso(segreto))
    assert auth.NOME_COOKIE not in cookie_messi(resp)


def test_entrare_azzera_il_conto_degli_errori(online):
    segreto = sicurezza.nuovo_segreto()
    auth.imposta_segreto_totp(segreto)
    manda_codice(_con_parziale(), "000000")
    manda_codice(_con_parziale(), sicurezza.codice_atteso(segreto))
    assert accesso._tentativi() == {}


# ── l'attivazione del secondo fattore ───────────────────────────────────────

def test_non_si_puo_riattivare_il_secondo_fattore(online):
    """Il buco più grosso possibile: se questa pagina restasse aperta, chi ha
    superato Google potrebbe attaccarci il PROPRIO telefono e saltare il
    secondo fattore invece di superarlo."""
    vecchio = sicurezza.nuovo_segreto()
    auth.imposta_segreto_totp(vecchio)
    req = _con_parziale("/accedi/attiva")
    assert dove_manda(rotte.pagina_attiva(req)) == "/accedi/codice"
    resp = manda_attiva(req, "000000")
    assert dove_manda(resp) == "/accedi/codice"
    assert auth.segreto_totp() == vecchio          # non è stato toccato


def test_all_attivazione_non_ci_si_arriva_dalla_strada(online):
    assert dove_manda(rotte.pagina_attiva(richiesta())) == "/accedi"
    assert dove_manda(manda_attiva(richiesta(), "0")) == "/accedi"


def test_il_segreto_si_salva_solo_se_il_telefono_lo_sa_fare(online):
    """Salvarlo prima della prova vuol dire poter restare chiusi fuori da casa
    propria per un errore di copiatura."""
    resp = rotte.pagina_attiva(_con_parziale("/accedi/attiva"))
    proposto = sicurezza.leggi_biglietto(
        cookie_messi(resp)[rotte.NOME_COOKIE_ATTIVA], CHIAVE)["segreto"]
    assert auth.segreto_totp() == ""               # ancora niente nel database

    req = richiesta("/accedi/attiva", cookies={
        auth.NOME_COOKIE: auth.crea_biglietto(IO, completo=False),
        rotte.NOME_COOKIE_ATTIVA: cookie_messi(resp)[rotte.NOME_COOKIE_ATTIVA]})
    assert dove_manda(manda_attiva(req, "000000")).endswith("errore=1")
    assert auth.segreto_totp() == ""               # nemmeno adesso

    buona = manda_attiva(req, sicurezza.codice_atteso(proposto))
    assert auth.segreto_totp() == proposto
    assert dove_manda(buona) == "/"


def test_senza_il_cookie_del_segreto_non_si_attiva_niente(online):
    """Il codice giusto per un segreto che non abbiamo mai proposto noi."""
    altrui = sicurezza.nuovo_segreto()
    resp = manda_attiva(_con_parziale("/accedi/attiva"), sicurezza.codice_atteso(altrui))
    assert dove_manda(resp).endswith("errore=1")
    assert auth.segreto_totp() == ""


# ── dove si viene rimandati dopo ────────────────────────────────────────────

@pytest.mark.parametrize("fuori", [
    "https://finto-mymoney.example/", "//finto-mymoney.example/",
    "javascript:alert(1)", "", "/accedi", "/accedi/codice",
])
def test_dopo_il_login_non_si_finisce_fuori_casa(online, fuori):
    """Far atterrare qualcuno su un finto MyMoney subito dopo il login è il
    modo più economico per farsi dare le credenziali."""
    assert rotte._dove_tornare(fuori) == "/"


def test_un_percorso_interno_invece_si_rispetta(online):
    assert rotte._dove_tornare("/finanze/movimenti") == "/finanze/movimenti"


# ── uscire ──────────────────────────────────────────────────────────────────

def test_uscire_esce_davvero(online):
    """Un'uscita che non esce è il peggior tipo di bug: sembra funzionare."""
    req = richiesta(cookies={auth.NOME_COOKIE: auth.crea_biglietto(IO, True)})
    resp = rotte.esci(req)
    assert dove_manda(resp) == "/accedi"
    # Starlette mette le virgolette a un valore vuoto: quel che conta è che
    # dentro non ci sia più niente
    assert cookie_messi(resp)[auth.NOME_COOKIE].strip('"') == ""
    # e quel che resta al browser non riapre niente
    assert auth.utente_da_richiesta(richiesta(cookies={auth.NOME_COOKIE: ""})) is None


# ── il cookie, come viaggia ─────────────────────────────────────────────────

def _attributi(resp, nome: str) -> str:
    for chiave, valore in resp.raw_headers:
        if chiave.lower() == b"set-cookie" and valore.decode().startswith(nome + "="):
            return valore.decode().lower()
    return ""


def test_il_biglietto_e_marchiato_come_si_deve(online):
    segreto = sicurezza.nuovo_segreto()
    auth.imposta_segreto_totp(segreto)
    resp = manda_codice(_con_parziale(), sicurezza.codice_atteso(segreto))
    riga = _attributi(resp, auth.NOME_COOKIE)
    assert "httponly" in riga        # nessuna pagina ha motivo di leggerlo
    assert "samesite=lax" in riga    # un sito terzo non agisce a nome nostro
    assert "secure" in riga          # in HTTPS non deve viaggiare in chiaro


def test_in_locale_il_cookie_non_pretende_https(online):
    """Senza questo, provare l'accesso sul PC sarebbe impossibile: il browser
    scarterebbe un cookie `Secure` arrivato su http."""
    segreto = sicurezza.nuovo_segreto()
    auth.imposta_segreto_totp(segreto)
    req = richiesta("/accedi/codice", https=False, cookies={
        auth.NOME_COOKIE: auth.crea_biglietto(IO, completo=False)})
    resp = manda_codice(req, sicurezza.codice_atteso(segreto))
    assert "secure" not in _attributi(resp, auth.NOME_COOKIE)
