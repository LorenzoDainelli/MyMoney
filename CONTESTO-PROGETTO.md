# Contesto progetto — briefing per una nuova chat

> Incolla o allega questo file all'inizio di una chat nuova per dare tutto il
> contesto. È scritto per un assistente (Claude) che riparte da zero.
> Ultimo aggiornamento: **2026-07-10**.

---

## 0. Chi sono e come voglio lavorare

- Utente: **Lorenzo Dainelli**, italiano, piano **Claude Pro**. **Rispondimi in italiano.**
- Metodo (importante): **discutere prima, costruire a fasi**, partire dal pezzo più
  semplice e funzionante, e **dopo ogni fase fermarsi e farmi provare** prima di andare
  avanti. Onestà intellettuale sui limiti: verifica sui fatti, non andare a memoria.
- Repo unico (monorepo) su GitHub **privato**. Cartella di lavoro locale:
  `C:\Users\loren\Desktop\Claude\Report` (Windows 11, PowerShell).

Nel repo convivono **DUE componenti dello stesso progetto** ("MyMoney"):

1. **news-monitor** — robot che gira nel **cloud** (Routine di Claude Code) e mi manda
   email coi titoli da seguire. NON si tocca mai senza motivo: è in produzione.
2. **web app MyMoney** — app **locale** nella cartella `app/`, che giro sul mio PC.

Condividono: anagrafica titoli (`config/portfolio.yaml` ↔ `app/portfolio/seed.py`),
design email (`app/emails/render.py`, palette MyMoney) e stato notizie (`state/`).

---

## 1. Regole NON NEGOZIABILI (valgono per entrambi)

- **Mai segnali operativi**: mai "compra/vendi/entra/esci". Lo strumento aiuta a CAPIRE,
  non dice cosa fare. La decisione è sempre mia.
- **Non è un oracolo**: non prevede i prezzi. Ogni stima d'impatto ha **disclaimer** +
  **livello di confidenza dichiarato (bassa/media/alta)**.
- **Cita sempre le fonti** (testata + link) per ogni notizia.
- **Italiano semplice**, riassunti di 2-3 frasi leggibili da telefono.
- **Mai esporre segreti**: chiavi/token (`RESEND_API_KEY`, `FINNHUB_API_KEY`, chiave
  Gemini) mai stampati, loggati o committati.
- **Privacy app (filtro per sezione)**: a Gemini **mai** ISIN/importi/valore/quantità
  (portafoglio), **mai** carte/IBAN/nome (finanze), **mai** il mio nome. I dati personali
  e le chiavi stanno **solo** in `app/data/` (gitignored, mai online).
- **Mai inventare dati**: valore mancante = trattino/nota onesta, mai numeri fittizi.

Queste regole sono anche in `CLAUDE.md` (caricato automaticamente nel cloud).

---

## 2. Parte A — news-monitor (cloud, in produzione)

Monitora il portafoglio reale (**37 titoli**: 11 ETF + 26 azioni, `config/portfolio.yaml`)
e manda **email in design MyMoney** (lo stesso della web app: header lime, neutri caldi).

**Architettura "anti-costo"**: il modello emette solo JSON di analisi; gli **script**
fanno il lavoro meccanico:
- `scripts/fetch_news.py` — scarica le news (API **Finnhub**) e passa al modello un
  digest compatto. Gestisce la dedup a monte (`--seen-file`).
- `scripts/render_email.py` — CLI che chiama `app/emails/render.py` (design condiviso:
  per cambiare l'aspetto delle email si modifica QUEL file).
- `scripts/update_state.py` — aggiorna lo stato (dedup + pruning 30 giorni).
- `scripts/send_email.py` — invio via **Resend** (serve User-Agent "browser" o
  Cloudflare blocca con errore 1010).
- `scripts/newskey.py` — `news_key(url)`: chiave di dedup deterministica dall'URL.
- Stato in `state/` (es. `seen.json`, `predictions.json`), committato su `main`.

**Routine attive** (Report ×2, Event-check ×3, Settimanale, Mensile). Tetto Pro =
5 run/giorno nei feriali. L'Event-check **invia SEMPRE** un'email: 🚨 se c'è qualcosa
di critico, altrimenti ✅ "tutto tranquillo". Report: skip email se nulla supera la
soglia (rilevanza report **50**, evento critico **70**, `config/settings.yaml`).

### ⚠️ Learning — i CRON personalizzati delle routine sono in UTC
Il cron digitato a mano viene eseguito in **UTC** (la UI non converte). I preset invece
convertono da soli. Estate (CEST = UTC+2): `cron = ora locale − 2`; inverno − 1.
Valori in uso: Report `0 5,17 * * 1-5` (07:00/19:00 IT), Event-check `0 9,13,20 * * 1-5`.

### ⚠️ Learning — dedup per URL **e** per evento
La dedup va fatta per **URL normalizzato** (`newskey.py`) **e** per **evento** (stesso
fatto da fonte diversa = non reinviare). Mai fidarsi di id inventati dal modello.

---

## 3. Parte B — web app MyMoney (sul cloud)

Portafoglio investimenti, finanze personali e agente AI. Legge le notizie del robot
e riusa la lista titoli. Dall'8 agosto 2026 gira **online**: telefono e PC sono la
stessa app sullo stesso database, quindi non esiste più una sincronizzazione.

### Stack
- **FastAPI + Jinja2 + SQLAlchemy**. Python 3.11.
- **Cloud Run** (`europe-west8`) + **Cloud SQL PostgreSQL 17**; segreti in Secret
  Manager. Indirizzo e dettagli in `docs/PIANO-CLOUD.md`.
- Porta d'ingresso: **Google OAuth** (una sola email in lista) + **TOTP** a sei cifre.
- Si apre col **doppio click su `Avvia-Finanza.bat`** (che ora apre solo il browser
  sull'indirizzo online). `Avvia-Finanza-Locale.bat` avvia invece la vecchia copia
  SQLite `app/data/finanza.db`, ferma al travaso: chiede conferma, e quel che ci
  scrivi resta lì.
- ⚠️ In locale `run.py` usa `reload=False` → **dopo ogni modifica va riavviata**.
- Base valuta **EUR**. Tema chiaro/scuro + **6 lingue** (IT/EN/ES/FR/DE/UK).
- Il lavoro giornaliero (notizie via HTTPS da GitHub, prezzi, fondamentali, serie
  del grafico) lo chiama **Cloud Scheduler** una volta al giorno su `/lavori/giorno`
  (`shared/lavori.py`), non più l'avvio dell'app.

### Design: "MyMoney Design System" (design freeze v1.0)
- Fonte autorevole del design: cartella
  `C:\Users\loren\Downloads\MyMoney Design System\design_handoff_mymoney\`
  (`styles/` copiati VERBATIM, `design_reference/*.jsx.txt` di sola lettura).
- Nell'app: `app/static/styles.css` (entry @import) + `app/static/tokens/*.css` +
  `app/static/mymoney.css`. Icone Lucide in `app/templates/_icons.html`.
- Regola di lavoro dell'utente: quando manca qualcosa del design, **copiarla dai
  riferimenti, mai inventarla**.

### Mappa (dove vivono le cose)
- Pagine HTML → `app/templates/` (`base.html` = scheletro comune).
- Traduzioni → `app/shared/i18n.py` (gli HTML usano `t('chiave')`).
- Logica: `app/portfolio/` (posizioni, prezzi/fondamentali Yahoo, analisi, PAC,
  patrimonio `wealth.py`), `app/finance/` (wallet, movimenti, sintesi),
  `app/news/` (lettura robot), `app/shared/` (db, i18n, AI, impostazioni),
  `app/emails/` (design email condiviso).
- Interazioni JS: `app/static/app.js` (conferme inline, drawer dettaglio posizione,
  ricalcolo PAC live, ordinamento tabelle, count-up) + `wealth-chart.js` (grafico).

### Stato funzionale (tutto ✅, luglio 2026)
- **Dashboard**: hero (patrimonio con count-up, spesa media, saldo mese), grafico
  patrimonio per range 1G→MAX (dati reali con cache in background), migliori/peggiori,
  notizie dal monitor, box Dividendi/AI/Settori **sempre presenti** (nota onesta se
  mancano i dati).
- **Portafoglio**: 37 posizioni (somma target 100%), prezzi live, tabella **ordinabile**
  (click sugli header), dettaglio in **drawer** (fondamentali, holdings, analisi AI),
  **nome breve** per gli ETF in tabella (es. IWDA → "Global"; l'ufficiale resta nel
  dettaglio; campo `nome_breve`, modificabile dal form).
- **PAC**: ricalcolo live, solo percentuali (niente importi fissi).
- **Analisi**: look-through, settori, geografia, valute, rischio, spiegazioni ✨AI.
- **Finanze**: card conti/carte REALI (mai generiche): AIB (viola brand `#632874`),
  Hype `#12B3A6`, Revolut `#5B5BD6`, Trade Republic `#334155`, Contanti, PAC; ordinate
  per saldo decrescente col PAC sempre ultimo. Movimento in linguaggio naturale (AI),
  sintesi mese, **tutti** i movimenti in tabella ordinabile.
  **Partita di giro** (4º tipo di movimento): spesa che qualcuno rimborsa (es. la
  pagano i genitori). Due gambe (speso/ricevuto) su UNA riga; i saldi si muovono
  davvero, in entrate/uscite conta SOLO la differenza (guadagno datato al rimborso,
  perdita alla spesa); casella "rimborso dopo" = partita APERTA nel riquadro
  "In attesa di rimborso" (neutra finché non chiusa; chiudibile o convertibile in
  uscita). Eliminare una partita elimina entrambe le gambe.
- **Notizie**: card come le email, aggiornate da GitHub a ogni avvio.
- **Impostazioni**: aspetto/lingua, agente AI Gemini (chiave, modello, modalità, test).
- Il portafoglio target dell'utente è **solo in percentuale** (niente più importi
  fissi). `seed.allinea_al_seed()` riallinea il DB preservando i dati personali.

### Agente AI (Gemini, opzionale)
- Chiave **gratuita** Google AI Studio (inizia con `AIza…`, MAI collegare carta).
- Modello di default `gemini-2.0-flash` con ripiego automatico se un modello dà 404.
- 429 = solo rate limit del free tier, si resetta da solo, nessun costo possibile.
- Funzioni: punto della settimana (dashboard), lettura finanze, parsing spese in
  linguaggio naturale, analisi posizione, spiegazione metriche. Tutto con dati
  aggregati/anonimi e conferma manuale per i salvataggi.

---

## 4. Stato del repo

- Branch di lavoro `redesign-ui` (redesign MyMoney completo) **fuso in `main`** e
  pushato su GitHub il 10/07/2026. Una nuova chat lavora su **`main`**.
- Il robot committa lo stato su `main` a ogni run: prima di lavorare fare
  `git pull` per allinearsi.

---

## 5. Promemoria per l'assistente che riparte
- Rispondi in **italiano**, semplice.
- **Non toccare il news-monitor** senza motivo (è in produzione).
- Le chiavi/segreti **non si stampano mai**; i dati in `app/data/` non vanno su GitHub.
- Lavora **a fasi**, fermati e fammi provare.
- Design: **copiare dal design freeze**, mai inventare. Dati mancanti = "—", mai finti.
- Dopo modifiche al codice dell'app, ricorda di dirmi di **riavviare** (`reload=False`).
