"""I due mattoni dell'accesso: il biglietto di sessione e il codice a sei cifre.

Nessuna libreria esterna: sono due algoritmi standard che stanno in poche righe,
e il progetto ha sempre preferito la libreria di Python a una dipendenza in più.

**Il biglietto di sessione** è un foglietto che diciamo noi al browser di
conservare, e che il browser ci rimostra a ogni pagina. Dentro c'è scritto chi
sei e fino a quando vale, in chiaro — non è un segreto — ma è **firmato**: se
qualcuno cambia anche una virgola, la firma non torna e il biglietto viene
buttato. La firma si fa con una chiave che sta solo sul server.

**Il codice a sei cifre** (TOTP, lo standard delle app authenticator) nasce da
un segreto condiviso una volta sola e dall'ora corrente: il telefono e il server
fanno lo stesso conto e devono ottenere lo stesso numero. Cambia ogni 30
secondi, e accettiamo anche la finestra precedente e successiva perché gli
orologi non sono mai perfettamente uguali.
"""
import base64
import hashlib
import hmac
import json
import struct
import time

# ── biglietto di sessione ────────────────────────────────────────────────────

def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode("ascii").rstrip("=")


def _da_b64(testo: str) -> bytes:
    return base64.urlsafe_b64decode(testo + "=" * (-len(testo) % 4))


def firma_biglietto(dati: dict, chiave: str, durata_secondi: int) -> str:
    """Crea il biglietto: contenuto + scadenza + firma."""
    corpo = dict(dati)
    corpo["scade"] = int(time.time()) + int(durata_secondi)
    grezzo = _b64(json.dumps(corpo, separators=(",", ":"), sort_keys=True).encode())
    firma = hmac.new(chiave.encode(), grezzo.encode(), hashlib.sha256).digest()
    return f"{grezzo}.{_b64(firma)}"


def leggi_biglietto(biglietto: str, chiave: str) -> dict | None:
    """Il contenuto se il biglietto è valido e non scaduto, altrimenti None.

    Nessuna eccezione verso l'esterno: un biglietto storto è semplicemente un
    biglietto che non vale, e chi chiama deve trattarlo come «non sei entrato».
    """
    if not biglietto or not chiave or "." not in biglietto:
        return None
    grezzo, _, firma_ricevuta = biglietto.rpartition(".")
    attesa = _b64(hmac.new(chiave.encode(), grezzo.encode(), hashlib.sha256).digest())
    # compare_digest: stesso tempo qualunque sia la differenza, così non si può
    # ricostruire la firma un carattere alla volta misurando le risposte.
    if not hmac.compare_digest(firma_ricevuta, attesa):
        return None
    try:
        corpo = json.loads(_da_b64(grezzo))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(corpo, dict) or corpo.get("scade", 0) < time.time():
        return None
    return corpo


# ── codice a sei cifre (TOTP, RFC 6238) ──────────────────────────────────────

PASSO = 30          # secondi di validità di ogni codice
CIFRE = 6


def nuovo_segreto() -> str:
    """Il segreto da mostrare una volta sola all'app authenticator."""
    import secrets
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def codice_atteso(segreto: str, quando: float | None = None) -> str:
    """Il codice valido in questo momento per quel segreto."""
    contatore = int((quando if quando is not None else time.time()) // PASSO)
    chiave = base64.b32decode(segreto.upper() + "=" * (-len(segreto) % 8))
    digest = hmac.new(chiave, struct.pack(">Q", contatore), hashlib.sha1).digest()
    inizio = digest[-1] & 0x0F
    numero = struct.unpack(">I", digest[inizio:inizio + 4])[0] & 0x7FFFFFFF
    return str(numero % (10 ** CIFRE)).zfill(CIFRE)


def codice_valido(segreto: str, codice: str, quando: float | None = None,
                  tolleranza: int = 1) -> bool:
    """Vero se il codice è quello giusto, ora o nella finestra accanto.

    La tolleranza serve perché l'orologio del telefono e quello del server non
    sono mai identici. Una finestra sola per parte: allargarla vuol dire
    allungare la vita di un codice rubato.
    """
    if not segreto or not codice:
        return False
    codice = codice.strip().replace(" ", "")
    if not codice.isdigit() or len(codice) != CIFRE:
        return False
    adesso = quando if quando is not None else time.time()
    for salto in range(-tolleranza, tolleranza + 1):
        if hmac.compare_digest(codice, codice_atteso(segreto, adesso + salto * PASSO)):
            return True
    return False


def uri_authenticator(segreto: str, conto: str, emittente: str = "MyMoney") -> str:
    """L'indirizzo da dare all'app authenticator (di solito come QR)."""
    import urllib.parse
    etichetta = urllib.parse.quote(f"{emittente}:{conto}")
    query = urllib.parse.urlencode({
        "secret": segreto, "issuer": emittente,
        "algorithm": "SHA1", "digits": CIFRE, "period": PASSO})
    return f"otpauth://totp/{etichetta}?{query}"
