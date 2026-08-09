"""Avvio comodo della COPIA LOCALE: fa partire il server sul computer e apre il
browser su 127.0.0.1.

Lo richiama Avvia-Finanza-Locale.bat. L'app di tutti i giorni sta sul cloud e non
passa da qui: Avvia-Finanza.bat apre direttamente il browser sull'indirizzo online.
Puoi anche eseguirlo a mano:
    python run.py
Per fermare l'app: chiudi la finestra nera, oppure premi CTRL+C.
"""
import threading
import webbrowser

import uvicorn

from shared.config import HOST, PORT


def _apri_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    # apre il browser ~1,5s dopo, il tempo che il server sia pronto
    threading.Timer(1.5, _apri_browser).start()
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
