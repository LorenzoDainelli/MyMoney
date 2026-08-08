"""Percorsi e configurazione di base dell'app.

Tutto ciò che è 'dove stanno i file' passa da qui. Le voci che cambiano tra il
PC e un server (database, indirizzo, porta) si leggono dall'ambiente, con i
valori di sempre come default: **in locale non cambia nulla**, basta non
impostare niente. Sul server si configura senza toccare il codice — e senza
mettere password nel repo.

Variabili riconosciute:
  MYMONEY_DB_URL   indirizzo del database (default: il file SQLite in data/)
  MYMONEY_HOST     su quale indirizzo ascoltare (default: solo il PC stesso)
  MYMONEY_PORT     porta del server (default: 8000)
  MYMONEY_JOB_TOKEN  parola d'ordine per far partire i lavori periodici da fuori
  MYMONEY_SESSION_KEY       chiave con cui si firmano i biglietti di sessione;
                            se manca NON c'è login e l'app resta quella di casa
  MYMONEY_EMAIL_CONSENTITE  chi può entrare, separati da virgola
  MYMONEY_OAUTH_CLIENT_ID / _SECRET   credenziali di «accedi con Google»
  MYMONEY_BASE_URL indirizzo pubblico dell'app (per il ritorno da Google)
"""
import os
import tempfile
from pathlib import Path

# .../app  (la cartella dell'app, due livelli sopra questo file: shared/config.py)
APP_DIR = Path(__file__).resolve().parent.parent

# Cartella dei dati LOCALI: database, segreti, cache. Mai su GitHub (vedi .gitignore).
# Su un server il disco può essere di sola lettura: in quel caso non è un errore,
# vuol dire solo che i dati non stanno in un file locale (c'è il database vero).
DATA_DIR = APP_DIR / "data"
try:
    DATA_DIR.mkdir(exist_ok=True)
except OSError:
    pass

# Database SQLite unico, con tabelle separate per dominio (portfolio_/finance_).
DB_PATH = DATA_DIR / "finanza.db"
DB_URL = os.environ.get("MYMONEY_DB_URL", "").strip() or f"sqlite:///{DB_PATH}"

# Vero quando giriamo sul file locale. Serve nei pochi punti in cui i due motori
# non parlano la stessa lingua (opzioni di connessione, tipi delle colonne).
IS_SQLITE = DB_URL.startswith("sqlite")

# Server: di default solo 127.0.0.1 (il PC stesso), non esposto alla rete. Privacy.
# Un server in cloud ha bisogno di 0.0.0.0 e della porta che gli assegna lui.
HOST = os.environ.get("MYMONEY_HOST", "").strip() or "127.0.0.1"
PORT = int(os.environ.get("PORT") or os.environ.get("MYMONEY_PORT") or 8000)

def _cartella_scrivibile(preferita: Path, ripiego: Path) -> Path:
    """La prima cartella in cui riusciamo davvero a scrivere.

    Sul PC è sempre `data/`. Su un server il disco dell'app è di sola lettura
    tranne una cartella temporanea: i file di comodo (cache) vanno lì. Non è
    un peccato perderli — si riscaricano; i dati veri stanno nel database.
    """
    for cartella in (preferita, ripiego):
        try:
            cartella.mkdir(parents=True, exist_ok=True)
            prova = cartella / ".prova-scrittura"
            prova.write_text("x", encoding="ascii")
            prova.unlink()
            return cartella
        except OSError:
            continue
    return ripiego


# Dove finiscono i file di comodo (cache delle notizie e simili).
CACHE_DIR = _cartella_scrivibile(DATA_DIR, Path(tempfile.gettempdir()) / "mymoney")

# Parola d'ordine per l'indirizzo che fa partire i lavori periodici (prezzi,
# storico, pulizia). Se è impostata vuol dire che c'è qualcuno fuori — un
# programma di Google — incaricato di chiamarci una volta al giorno: in quel caso
# NON li facciamo più partire da soli a ogni avvio. Sul PC resta vuota e tutto
# funziona come è sempre stato.
JOB_TOKEN = os.environ.get("MYMONEY_JOB_TOKEN", "").strip()

# ── accesso (serve solo quando l'app sta online) ────────────────────────────
# Chiave con cui si firmano i biglietti di sessione. **Nessun valore di
# ripiego**: un ripiego sarebbe una firma indovinabile, cioè una serratura
# finta. Vuota = nessun login, l'app è quella di casa e si entra come sempre.
SESSION_KEY = os.environ.get("MYMONEY_SESSION_KEY", "").strip()

# Chi può entrare. Vuota = nessuno: se l'app è online con la lista vuota non
# entra nemmeno il proprietario. È voluto — meglio chiusi fuori che aperti a
# tutti, perché «accedi con Google» da solo lo può fare mezzo mondo.
EMAIL_CONSENTITE = [e for e in os.environ.get(
    "MYMONEY_EMAIL_CONSENTITE", "").split(",") if e.strip()]

# Credenziali di «accedi con Google» (create nella console Google Cloud).
OAUTH_CLIENT_ID = os.environ.get("MYMONEY_OAUTH_CLIENT_ID", "").strip()
OAUTH_CLIENT_SECRET = os.environ.get("MYMONEY_OAUTH_CLIENT_SECRET", "").strip()

# L'indirizzo pubblico dell'app, scritto a mano perché Google pretende che il
# «dove torno dopo il login» sia IDENTICO a quello registrato nella console.
# Ricavarlo dall'intestazione Host funzionerebbe quasi sempre, ma quell'intestazione
# la scrive chi chiama: preferisco un valore che decidiamo noi. Vuoto = lo si
# ricava dalla richiesta, che è quel che serve per provare sul PC.
BASE_URL = os.environ.get("MYMONEY_BASE_URL", "").strip().rstrip("/")

APP_NAME = "Finanza personale"
