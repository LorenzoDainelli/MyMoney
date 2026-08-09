"""Il giro con Google: dal bottone «Entra» al nome di chi è entrato.

Diviso apposta da `shared/auth.py`: là ci sono le **regole** (chi può entrare,
quanto vale un biglietto), qui c'è la **meccanica** (parlare con Google). Le
regole si leggono senza sapere niente di OAuth, e la meccanica si cambia il
giorno in cui Google cambia idea senza rimettere mano alle regole.

**Perché non abbiamo riusato il codice OAuth di `drive_sync.py`.** Era lo stesso
ballo, ma quel modulo era già condannato: appoggiarci la porta di casa voleva
dire legarla a qualcosa che avevamo deciso di buttare. Qui servivano venti righe
di HTTP e le abbiamo scritte — e infatti con la Fase 5 `drive_sync.py` non
esiste più, mentre questo file è rimasto in piedi da solo.

**Il dettaglio che su un server cambia tutto.** Il segreto usa-e-getta del giro
(PKCE) e lo `state` NON stanno in memoria: stanno in un biglietto firmato dentro
un cookie che vive dieci minuti. Su Cloud Run l'app gira in più copie e si
spegne quando non la usi: il ritorno da Google può benissimo bussare a
un'istanza diversa da quella che ha aperto il giro, e una variabile in memoria
lì non c'è più. Un cookie firmato invece viaggia con l'utente.

**Sull'id_token non controlliamo la firma, ed è voluto.** Non arriva dal browser:
lo chiediamo noi a Google, in diretta, su una connessione cifrata verso il suo
dominio, mostrando il nostro segreto. Non c'è nessuno in mezzo che possa averlo
scritto. Controlliamo invece le cose che contano davvero: che sia stato emesso
**per noi** (`aud`), da **Google** (`iss`), che non sia **scaduto**, e che
l'indirizzo sia **verificato** — un indirizzo non verificato lo può dichiarare
chiunque, e la nostra serratura è proprio una lista di indirizzi.
"""
import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from shared import settings_store
from shared.config import (BASE_URL, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET)

log = logging.getLogger("mymoney.accesso")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# Chiediamo il minimo: chi sei e la tua email. Niente Drive, niente rubrica,
# niente foto. Meno cose chiediamo, meno danni può fare un nostro errore.
SCOPE = "openid email"
TIMEOUT = 20

RITORNO = "/accedi/google/ritorno"
EMITTENTI = ("https://accounts.google.com", "accounts.google.com")


def configurato() -> bool:
    """Vero quando le credenziali di Google ci sono. Senza, la pagina di accesso
    lo dice invece di mandare l'utente su un indirizzo rotto."""
    return bool(OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET)


# ── dove torniamo dopo Google ───────────────────────────────────────────────

def indirizzo_base(request) -> str:
    """L'indirizzo pubblico dell'app.

    Se è configurato lo usiamo e basta: Google pretende che il «dove torno» sia
    identico a quello registrato, e un valore deciso da noi non lo può spostare
    nessuno. Il ripiego serve solo a provare sul PC, dove Google non c'entra.
    """
    if BASE_URL:
        return BASE_URL
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "http").split(",")[0].strip()
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def uri_di_ritorno(request) -> str:
    return indirizzo_base(request) + RITORNO


def connessione_sicura(request) -> bool:
    """Vero se il browser ci sta parlando in HTTPS (anche attraverso Cloud Run,
    che ce lo dice con un'intestazione perché a lui arriviamo in chiaro).

    Serve a decidere se marcare il cookie come `Secure`. Marcarlo sempre
    romperebbe la prova in locale; non marcarlo mai lascerebbe il biglietto
    viaggiare in chiaro il giorno in cui qualcuno apre l'app su `http://`.
    """
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "http").split(",")[0].strip()
    return proto == "https"


# ── HTTP verso Google ───────────────────────────────────────────────────────

def _posta(url: str, dati: dict) -> dict:
    """Una POST form-encoded. Torna il JSON, o {'error': ...}.

    `dati` contiene il nostro segreto: non finisce nei log **mai**, nemmeno
    quando la richiesta fallisce — è il momento in cui verrebbe più voglia.
    """
    corpo = urllib.parse.urlencode(dati).encode()
    req = urllib.request.Request(
        url, data=corpo, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            grezzo, stato = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        grezzo, stato = e.read(), e.code
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log.warning("accesso: Google irraggiungibile (%s)", type(e).__name__)
        return {"error": "rete"}
    try:
        data = json.loads(grezzo)
    except (ValueError, json.JSONDecodeError):
        data = {}
    if stato != 200 and "error" not in data:
        data["error"] = f"http_{stato}"
    return data


# ── il giro vero e proprio ──────────────────────────────────────────────────

def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode("ascii").rstrip("=")


def nuovo_giro() -> dict:
    """I tre usa-e-getta del giro, da mettere nel cookie firmato.

    - `state` torna indietro da Google tale e quale: se non combacia, quel
      ritorno non l'abbiamo chiesto noi (è così che si respinge un giro
      confezionato da qualcun altro);
    - `verifier` è il segreto di PKCE: a Google mostriamo solo la sua impronta,
      e alla fine gli riveliamo l'originale. Chi rubasse il codice per strada
      non saprebbe che farsene;
    - `nonce` finisce dentro l'id_token: dimostra che quel documento è stato
      scritto per QUESTO giro e non è un vecchio riesumato.
    """
    return {"state": secrets.token_urlsafe(24),
            "verifier": secrets.token_urlsafe(48),
            "nonce": secrets.token_urlsafe(16)}


def url_di_google(giro: dict, redirect_uri: str) -> str:
    impronta = _b64(hashlib.sha256(giro["verifier"].encode()).digest())
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": giro["state"],
        "nonce": giro["nonce"],
        "code_challenge": impronta,
        "code_challenge_method": "S256",
        # niente refresh token: qui a Google chiediamo solo «chi è questo»,
        # una volta. Un token che dura mesi sarebbe una cosa in più da custodire
        # senza che ci serva a niente.
        "prompt": "select_account",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _leggi_id_token(id_token: str, nonce: str) -> tuple[str, str]:
    """L'indirizzo email dentro l'id_token, se il documento è in regola.

    Torna (email, motivo_del_rifiuto): uno dei due è sempre vuoto.
    """
    pezzi = (id_token or "").split(".")
    if len(pezzi) != 3:
        return "", "documento"
    try:
        corpo = json.loads(base64.urlsafe_b64decode(
            pezzi[1] + "=" * (-len(pezzi[1]) % 4)))
    except (ValueError, json.JSONDecodeError):
        return "", "documento"
    if not isinstance(corpo, dict):
        return "", "documento"
    if corpo.get("aud") != OAUTH_CLIENT_ID:
        return "", "destinatario"     # emesso per un'altra app
    if corpo.get("iss") not in EMITTENTI:
        return "", "emittente"
    if float(corpo.get("exp", 0)) < time.time():
        return "", "scaduto"
    if nonce and corpo.get("nonce") != nonce:
        return "", "giro"             # documento di un altro giro, riusato
    # `email_verified` falso vuol dire che Google quell'indirizzo non l'ha mai
    # controllato: la nostra serratura è una lista di indirizzi, accettarne uno
    # non verificato sarebbe accettare un nome dichiarato da chi bussa.
    if not corpo.get("email_verified"):
        return "", "nonverificata"
    email = (corpo.get("email") or "").strip().lower()
    return (email, "") if email else ("", "senzaemail")


def chi_e_tornato(code: str, giro: dict, redirect_uri: str) -> tuple[str, str]:
    """Scambia il codice del ritorno e dice chi è. Torna (email, errore)."""
    if not code:
        return "", "codice"
    data = _posta(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "code_verifier": giro.get("verifier", ""),
    })
    if data.get("error") or not data.get("id_token"):
        log.warning("accesso: scambio rifiutato (%s)", data.get("error", "?"))
        return "", "scambio"
    return _leggi_id_token(data["id_token"], giro.get("nonce", ""))


# ── quante volte si può sbagliare il codice a sei cifre ─────────────────────
# Sei cifre sono un milione di combinazioni: chi provasse a indovinarle a raffica
# ci riuscirebbe, e nel frattempo ci farebbe pagare il server. Dopo qualche
# errore si aspetta. Il conteggio sta nel database e non in memoria per lo
# stesso motivo del cookie: il server è più di uno, e un contatore per istanza
# si azzera semplicemente ritentando.
CHIAVE_TENTATIVI = "auth_totp_tentativi"
MAX_TENTATIVI = 8
PAUSA = 300          # cinque minuti


def _tentativi() -> dict:
    try:
        d = json.loads(settings_store.get_setting(CHIAVE_TENTATIVI, "") or "{}")
        return d if isinstance(d, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def in_pausa() -> int:
    """Secondi che mancano prima di poter riprovare (0 = si può)."""
    fino = float(_tentativi().get("fino", 0))
    return max(0, int(fino - time.time()))


def segna_errore() -> None:
    d = _tentativi()
    # gli errori vecchi non contano: chi sbaglia una cifra oggi e una domani non
    # sta forzando niente
    if time.time() - float(d.get("ultimo", 0)) > PAUSA:
        d = {}
    n = int(d.get("n", 0)) + 1
    nuovo = {"n": n, "ultimo": time.time()}
    if n >= MAX_TENTATIVI:
        nuovo = {"n": 0, "ultimo": time.time(), "fino": time.time() + PAUSA}
    settings_store.set_setting(CHIAVE_TENTATIVI, json.dumps(nuovo))


def azzera_errori() -> None:
    settings_store.set_setting(CHIAVE_TENTATIVI, "")
