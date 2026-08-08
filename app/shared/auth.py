"""Chi sei, e se puoi entrare.

Due regole guidano tutto quello che c'è qui.

**Le password non le gestiamo noi.** A riconoscerti è Google: noi riceviamo la
sua parola («questa persona è lorenzo…@gmail.com») e ci fidiamo di quella. Meno
codice nostro su una cosa dove sbagliare costa caro. (La 2FA via SMS era stata
scartata perché a pagamento: l'equivalente gratuito è l'app authenticator.)

**«Accedi con Google» non è una serratura.** Un account Google ce l'ha mezzo
mondo: se ci fermassimo lì entrerebbe chiunque. La serratura vera è la **lista
di chi può entrare**, che in questa app ha una riga sola.

Il codice a sei cifre è il gradino in più: chi si impossessasse dell'account
Google, senza il telefono resta comunque fuori.

Sul PC di casa non cambia niente: senza chiave di sessione configurata l'app
resta com'era, a utente singolo e senza login (vedi `richiede_accesso`).
"""
from dataclasses import dataclass

from shared import settings_store, sicurezza
from shared.config import EMAIL_CONSENTITE, SESSION_KEY

NOME_COOKIE = "mymoney_sessione"

# Le uniche pagine raggiungibili senza essere entrati. È una lista di ciò che si
# APRE, non di ciò che si chiude: così una pagina nuova nasce protetta invece
# che aperta, e dimenticarsi di proteggerla non è più possibile.
#
# `/lavori/giornaliero` è qui ma non è scoperto: ha la sua parola d'ordine
# (shared/lavori.py), perché lo chiama un programma, non una persona col
# browser — e a un programma non si può chiedere di fare il login.
PERCORSI_LIBERI = (
    "/salute",
    "/accedi",
    "/accedi/google",
    "/accedi/google/ritorno",
    "/accedi/attiva",
    "/accedi/codice",
    "/esci",
    "/lavori/giornaliero",
)

# Prefissi liberi: file di grafica e simili, che non contengono dati.
PREFISSI_LIBERI = ("/static/", "/favicon")
DURATA_SESSIONE = 14 * 24 * 3600      # due settimane
DURATA_PARZIALE = 10 * 60             # dal login Google al codice: dieci minuti

CHIAVE_TOTP = "auth_totp_segreto"     # nella tabella impostazioni


@dataclass(frozen=True)
class User:
    id: str
    nome: str
    email: str = ""
    is_local: bool = False


# Utente del PC di casa: un solo utente, nessun login.
LOCAL_USER = User(id="local", nome="Utente locale", is_local=True)


def richiede_accesso() -> bool:
    """Vero solo quando l'app è configurata per stare online.

    Senza chiave di sessione non c'è modo di firmare i biglietti, quindi non c'è
    login possibile: siamo sul PC di casa e si entra come sempre. È anche il
    motivo per cui la chiave NON ha un valore di ripiego: un ripiego vorrebbe
    dire una firma indovinabile, cioè una serratura finta.
    """
    return bool(SESSION_KEY)


def percorso_libero(percorso: str) -> bool:
    """Vero solo per le pagine che devono funzionare anche senza essere entrati.

    Sta qui e non dentro il middleware perché una regola di sicurezza va in un
    posto dove i test possano prenderla.
    """
    percorso = (percorso or "/").rstrip("/") or "/"
    if percorso in {p.rstrip("/") or "/" for p in PERCORSI_LIBERI}:
        return True
    return any((percorso + "/").startswith(p) for p in PREFISSI_LIBERI)


def email_ammessa(email: str) -> bool:
    """La lista di chi può entrare. Lista vuota = non entra nessuno."""
    email = (email or "").strip().lower()
    if not email:
        return False
    return email in {e.strip().lower() for e in EMAIL_CONSENTITE if e.strip()}


# ── il biglietto di sessione ────────────────────────────────────────────────

def crea_biglietto(email: str, completo: bool) -> str:
    """`completo` falso = ha superato Google ma non ancora il codice a sei
    cifre. Quel biglietto vale pochi minuti e apre solo la pagina del codice."""
    durata = DURATA_SESSIONE if completo else DURATA_PARZIALE
    return sicurezza.firma_biglietto(
        {"email": (email or "").strip().lower(), "completo": bool(completo)},
        SESSION_KEY, durata)


def utente_da_richiesta(request) -> User | None:
    """L'utente che ha fatto TUTTO il giro, oppure None.

    Torna None anche a chi ha il biglietto parziale: quello non è ancora
    «essere entrati», e distinguerlo qui evita di doverselo ricordare altrove.
    """
    if not richiede_accesso():
        return LOCAL_USER
    corpo = sicurezza.leggi_biglietto(
        request.cookies.get(NOME_COOKIE, ""), SESSION_KEY)
    if not corpo or not corpo.get("completo"):
        return None
    email = corpo.get("email", "")
    # ricontrollata a ogni pagina: togliere un indirizzo dalla lista deve
    # bastare a chiudere fuori anche chi ha già un biglietto valido in tasca.
    if not email_ammessa(email):
        return None
    return User(id=email, nome=email.split("@")[0], email=email)


def email_parziale(request) -> str:
    """L'indirizzo di chi ha passato Google e deve ancora dare il codice."""
    corpo = sicurezza.leggi_biglietto(
        request.cookies.get(NOME_COOKIE, ""), SESSION_KEY)
    if not corpo or corpo.get("completo"):
        return ""
    return corpo.get("email", "")


# ── secondo fattore ─────────────────────────────────────────────────────────

def segreto_totp() -> str:
    """Il segreto dell'app authenticator; vuoto se non è ancora stato attivato."""
    return settings_store.get_setting(CHIAVE_TOTP, "").strip()


def imposta_segreto_totp(segreto: str) -> None:
    settings_store.set_setting(CHIAVE_TOTP, (segreto or "").strip())


def totp_attivo() -> bool:
    return bool(segreto_totp())


def get_current_user() -> User:
    """Compatibilità con il codice scritto quando l'app era solo locale."""
    return LOCAL_USER
