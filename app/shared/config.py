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
"""
import os
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

APP_NAME = "Finanza personale"
