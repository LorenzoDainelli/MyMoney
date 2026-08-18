# MyMoney — finanza personale + monitor titoli

Un unico progetto personale, in un unico repo **privato**, con due componenti che
condividono dati e design:

1. **Web app MyMoney** (`app/`) — app locale e gratuita per il PC: portafoglio
   investimenti con prezzi live, finanze personali (conti e carte reali, movimenti),
   PAC, analisi, notizie e un agente AI opzionale (Gemini). Design system "MyMoney"
   (lime pistacchio + neutri caldi, tema chiaro/scuro, 6 lingue).
2. **News-monitor** (radice del repo) — robot che gira nel **cloud** (Routine di
   Claude Code, PC spento OK): monitora il portafoglio, filtra le notizie rilevanti
   e invia report via **email** con lo stesso design della web app.

Cosa condividono:
- **Anagrafica titoli**: `config/portfolio.yaml` (robot) e `app/portfolio/seed.py`
  (app) descrivono lo stesso portafoglio (37 posizioni, somma target 100%).
- **Design email**: l'HTML delle email è generato da `app/emails/render.py`
  (palette MyMoney); `scripts/render_email.py` è solo l'ingresso CLI per il robot.
- **Stato**: il robot committa le notizie analizzate in `state/predictions.json`;
  l'app le scarica da GitHub a ogni avvio e le mostra nella sezione Notizie.

Filosofia (regole in `CLAUDE.md`): **ridurre il rumore, non aumentarlo** e
**onestà intellettuale**. Nessun segnale operativo ("compra/vendi"), nessuna
previsione di prezzo; analisi qualitative con fonti citate, disclaimer e livello
di confidenza dichiarato (bassa/media/alta).

---

## La web app (sul cloud)

Doppio click su **`Avvia-Finanza.bat`** → apre il browser sull'app online
(Cloud Run, accesso con Google + codice a sei cifre).
Dettagli, struttura e privacy in **`app/README.md`**.

- **Telefono e PC sono la stessa app sullo stesso database**: quello che scrivi
  da una parte c'è già dall'altra. Non c'è niente da sincronizzare.
- I dati stanno su Cloud SQL; le chiavi in Secret Manager, mai nel repo.
- Il lavoro giornaliero (notizie, prezzi, fondamentali, grafico del patrimonio)
  lo chiama Cloud Scheduler una volta al giorno, non l'avvio dell'app.
- `Avvia-Finanza-Locale.bat` apre la **vecchia copia sul computer**
  (`app/data/finanza.db`, ferma al travaso dell'8 agosto 2026): serve per
  guardare indietro o per lavorare senza internet, e quello che ci scrivi
  **resta lì**. Chiede conferma prima di partire.

## Il news-monitor (cloud)

1. Una Routine schedulata parte nel cloud e clona questo repo (config + prompt + stato).
2. Segue le istruzioni in `prompts/` (report, event-check, settimanale, mensile):
   scarica le news (`scripts/fetch_news.py`, con dedup), le analizza, filtra per soglia.
3. Se c'è qualcosa di rilevante invia l'email via Resend (`scripts/send_email.py`);
   l'event-check invia SEMPRE un esito (🚨 critico oppure ✅ tutto tranquillo).
4. Aggiorna `state/` e committa su `main` per ricordarsene alla run successiva.

```
CLAUDE.md              regole sempre attive (mai segnali operativi, disclaimer, ...)
config/
  portfolio.yaml       i 37 titoli monitorati (pesi allineati all'app)
  settings.yaml        destinatario, mittente, soglie, test_mode
prompts/               istruzioni passo-passo delle routine (report, event-check, ...)
scripts/               fetch news, render email (design in app/emails/), invio, stato
state/                 notizie viste, analisi, log delle run (committati dal robot)
app/                   la web app MyMoney (vedi app/README.md)
```

### Setup una tantum del robot (già fatto, come promemoria)
- Repo GitHub **privato** collegato a Claude Code.
- Ambiente della routine: rete consentita verso `api.resend.com` (+ package manager),
  variabili `RESEND_API_KEY` e `FINNHUB_API_KEY`.
- Permessi: push su `main` abilitato (per i commit di stato).
- ⚠️ I **cron personalizzati sono in UTC**: d'estate (CEST) `cron = ora locale − 2`,
  d'inverno − 1.

## Privacy e sicurezza
- Portafoglio e ISIN restano nel repo **privato**; i dati personali (saldi, movimenti,
  quantità) stanno solo in `app/data/`, mai committati.
- Le chiavi (`RESEND_API_KEY`, Finnhub, Gemini) non vengono mai stampate, loggate
  o committate.
- All'agente AI dell'app arrivano solo dati aggregati e anonimi.
