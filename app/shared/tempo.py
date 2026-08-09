"""Che ora è, per questa app.

Finora la risposta era ovvia: l'ora del PC. Un solo computer, un solo orologio,
`datetime.now()` e via. Poi gli orologi sono diventati tre e hanno smesso di
andare d'accordo:

- il **PC** dice l'ora di Windows (che resta quella di casa anche se tu non ci sei);
- il **telefono** si sposta da solo quando cambi Paese;
- il **server** in cloud non sta da nessuna parte: gira in **UTC**.

Tre risposte diverse alla stessa domanda, e la data di un movimento finiva per
dipendere da quale dei tre l'aveva scritta. Un'ora di differenza non sposta
nessun conto — sposta il *giorno*, ma solo vicino a mezzanotte, e quando lo fa
può spostare anche il mese.

Quindi il fuso qui è una **scelta**, non una scoperta: sta nelle impostazioni,
lo decidi tu, e vale per tutta l'app a prescindere da chi esegue il codice.

**Perché le date restano "nude" (naive).** Tutto il database e tutti i confronti
sono scritti senza fuso. Trasformarli a metà in date "con fuso" vorrebbe dire
confrontare mele con pere — e in Python un confronto misto non dà un risultato
sbagliato, solleva un errore. Qui si converte all'ora del fuso scelto e si
toglie l'etichetta: il fuso non è perso, è scritto nelle impostazioni.
"""
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CHIAVE = "fuso_orario"

# Il ripiego è l'Italia, non UTC: in locale coincide con quello che l'app ha
# sempre fatto (l'orologio era italiano), e sul server evita il caso peggiore —
# un fuso che non è quello di nessuno.
PREDEFINITO = "Europe/Rome"

# Le voci del menù. La lista è corta di proposito: sono i posti dove potresti
# davvero essere. Non è una gabbia — `valido()` accetta qualunque nome IANA, per
# cui allungarla è solo questione di aggiungere una riga.
FUSI = (
    ("Europe/Rome",    "Italia"),
    ("Europe/Dublin",  "Irlanda"),
    ("Europe/London",  "Regno Unito"),
    ("Europe/Lisbon",  "Portogallo"),
    ("Europe/Athens",  "Grecia"),
    ("Europe/Helsinki", "Finlandia"),
    ("America/New_York", "New York"),
    ("America/Los_Angeles", "California"),
    ("Asia/Dubai",     "Dubai"),
    ("Asia/Tokyo",     "Giappone"),
    ("Australia/Sydney", "Sydney"),
    ("UTC",            "UTC (ora universale)"),
)

# Cache del nome scelto. Serve per un motivo preciso, non per velocità: `adesso()`
# viene chiamata anche dentro il salvataggio di SQLAlchemy (models.py timbra
# `updated_at` in `before_flush`), e aprire lì una seconda sessione per leggere
# un'impostazione significa chiedere una connessione mentre il database sta
# scrivendo. Con SQLite quello è il modo classico di prendersi un "database is
# locked". La cache scade da sola, così cambiare fuso non richiede un riavvio.
_SCADENZA = 60.0          # secondi
_cache_nome = ""
_cache_letta = 0.0


def _adesso_monotono() -> float:
    import time
    return time.monotonic()


def valido(nome: str) -> bool:
    """Vero se è un nome di fuso che il sistema conosce davvero."""
    nome = (nome or "").strip()
    if not nome:
        return False
    try:
        ZoneInfo(nome)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def nome_fuso() -> str:
    """Il fuso scelto: impostazione → variabile d'ambiente → Italia.

    Non solleva mai: se il database non c'è (le routine delle email girano fuori
    dall'app) si scende al ripiego invece di far fallire tutto per un fuso.
    """
    global _cache_nome, _cache_letta
    ora = _adesso_monotono()
    if _cache_nome and (ora - _cache_letta) < _SCADENZA:
        return _cache_nome

    scelto = ""
    try:
        from shared import settings_store
        scelto = (settings_store.get_setting(CHIAVE, "") or "").strip()
    except Exception:
        scelto = ""
    if not scelto:
        scelto = os.environ.get("MYMONEY_FUSO", "").strip()
    if not valido(scelto):
        scelto = PREDEFINITO

    _cache_nome, _cache_letta = scelto, ora
    return scelto


def fuso() -> ZoneInfo:
    try:
        return ZoneInfo(nome_fuso())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")     # meglio un'ora sbagliata che una pagina rotta


def imposta(nome: str) -> bool:
    """Cambia il fuso. Falso (e non cambia niente) se il nome non esiste."""
    nome = (nome or "").strip()
    if not valido(nome):
        return False
    from shared import settings_store
    settings_store.set_setting(CHIAVE, nome)
    scarta_cache()
    return True


def scarta_cache() -> None:
    global _cache_nome, _cache_letta
    _cache_nome, _cache_letta = "", 0.0


def etichetta(nome: str = "") -> str:
    """«Irlanda» invece di «Europe/Dublin». Se non lo conosciamo, il nome così com'è."""
    nome = (nome or nome_fuso()).strip()
    for id_, testo in FUSI:
        if id_ == nome:
            return testo
    return nome.replace("_", " ")


# ── che ora è ───────────────────────────────────────────────────────────────

def adesso() -> datetime:
    """Adesso, nell'ora del fuso scelto, senza etichetta di fuso."""
    return datetime.now(fuso()).replace(tzinfo=None)


def oggi() -> date:
    """Che giorno è dove hai detto di essere. Non è sempre il giorno del server:
    all'una di notte italiana a Milano è già domani e a Londra ancora ieri."""
    return adesso().date()


def etichetta_giorno(quando) -> str | None:
    """Chiave di traduzione per «oggi»/«ieri», oppure None se va scritta la data.

    Sta qui, e non in chi la usa, per un motivo solo: il confronto deve passare
    da `oggi()`. Scritta con `date.today()` guarderebbe l'orologio della
    macchina, e dall'Irlanda le 23:37 sono già il giorno dopo a Roma — un
    movimento appena registrato si presenterebbe come «ieri». Mettendola qui
    nessuno può sbagliare a chiamarla: non c'è un parametro da passare.

    Restituisce la chiave i18n e non il testo: la home la mostra in sei lingue.
    """
    g = quando.date() if isinstance(quando, datetime) else quando
    giorni = (oggi() - g).days
    if giorni == 0:
        return "dash.today"
    if giorni == 1:
        return "dash.yesterday"
    return None


def da_epoch(epoch) -> datetime:
    """Un istante universale (i timestamp di Yahoo) letto nell'ora del fuso scelto.

    Serve al PAC: se indichi l'ora di un acquisto, l'app cerca la candela di
    quell'ora. Convertirla con l'orologio della macchina vorrebbe dire, sul
    server, cercarla due ore prima — cioè un prezzo diverso.
    """
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(
        fuso()).replace(tzinfo=None)


def a_naive(dt: datetime) -> datetime:
    """Una data con fuso (es. la 'Z' che manda il telefono) riportata nell'ora
    scelta e spogliata dell'etichetta. Le date già nude passano intatte."""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(fuso()).replace(tzinfo=None)
