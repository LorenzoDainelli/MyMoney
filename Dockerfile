# Il pacchetto con cui gira MyMoney su un server (Cloud Run).
#
# Sul PC non serve a niente: là la copia locale parte con Avvia-Finanza-Locale.bat
# (Avvia-Finanza.bat apre il browser proprio su quello che questo file costruisce).
# Qui si descrive come costruire una scatola che contiene Python, le librerie e
# l'app, e nient'altro — in particolare NON i dati (vedi .dockerignore).

FROM python:3.12-slim

# Niente file .pyc, e i messaggi escono subito invece di restare nel buffer:
# senza questo, i log su un server arrivano a scatti o non arrivano affatto.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Le dipendenze prima del codice: cambiano di rado, e così Google non le
# riscarica tutte a ogni modifica di una riga dell'app.
COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Il codice dell'app.
COPY app/ /app/

# state/ sono le notizie prodotte dal robot: l'app le legge, non le scrive.
COPY state/ /state/

# L'app non gira come amministratore: se qualcuno trovasse un modo di entrare,
# si troverebbe con i permessi di un utente qualunque invece che con tutti.
RUN useradd --create-home --uid 1000 mymoney && chown -R mymoney /app
USER mymoney

# Cloud Run decide lui la porta e la comunica nella variabile PORT; shared/config.py
# la legge già. 0.0.0.0 vuol dire «accetta da fuori»: sul PC resta 127.0.0.1.
ENV MYMONEY_HOST=0.0.0.0 \
    PORT=8080
EXPOSE 8080

# Un solo processo con più thread: l'app è per una persona sola, e più processi
# vorrebbero dire più copie dei lavori periodici e più connessioni al database.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
