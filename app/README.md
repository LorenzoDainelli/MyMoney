# MyMoney — App finanza personale

Applicazione personale che unisce **portafoglio investimenti** e **finanze
personali**, con un agente AI condiviso (Gemini, opzionale). Gira su **Cloud Run**
con i dati su Cloud SQL, e si apre nel browser dal telefono come dal PC. Vive
nello stesso repo del news-monitor, ma è separata: il robot-notizie gira per conto
suo nel cloud e l'app lo legge soltanto.

## Come si apre
Doppio click su **`Avvia-Finanza.bat`** (nella cartella sopra a questa): apre il
browser sull'app online. Dal telefono, lo stesso indirizzo — è la stessa app sugli
stessi dati, quindi **non c'è nessuna sincronizzazione da fare**.
- Alla porta: accesso con Google (una sola email autorizzata) più un **codice a
  sei cifre** dall'app dell'autenticatore.
- Sul telefono conviene aggiungerla alla schermata Home: si apre a tutto schermo,
  senza la barra del browser.

### La copia locale
`Avvia-Finanza-Locale.bat` avvia lo stesso programma sul **file di questo
computer** (`data/finanza.db`), fermo al travaso dell'8 agosto 2026. Serve per
guardare indietro o per lavorare offline; quello che ci scrivi resta lì e non
raggiunge il cloud. Il primo avvio prepara l'ambiente (~1 minuto) e serve
**Python 3** installato (verificato: 3.11).

## Cosa c'è
- **Dashboard**: patrimonio con grafico per periodo (1G→MAX), spesa media, saldo
  del mese, migliori/peggiori, dividendi stimati, notizie dal monitor, punto
  della settimana (AI) ed esposizione per settore.
- **Portafoglio**: posizioni con prezzi live (Yahoo), dettaglio in pannello
  (fondamentali, holdings, analisi AI), tabella ordinabile.
- **PAC**: ripartisce l'importo mensile per % target, ricalcolo live.
- **Analisi**: look-through, settori, geografia, valute, metriche di rischio, spiegazioni ✨AI.
- **Finanze**: conti e carte reali (AIB, Hype, Revolut, Trade Republic, contanti,
  PAC), movimenti con inserimento anche in linguaggio naturale (AI), sintesi del mese.
  Quattro tipi di movimento: entrata, uscita, trasferimento e **partita di giro**
  (spesa che qualcuno rimborsa: i saldi si muovono davvero, nelle statistiche conta
  solo la differenza; le partite aperte restano in evidenza finché non le chiudi).
- **Notizie**: le card del news-monitor, aggiornate da GitHub a ogni avvio.
- **Impostazioni**: aspetto, lingua (6), agente AI Gemini. La chiave resta solo in locale.

A ogni avvio l'app aggiorna da sola, in background: notizie, prezzi,
fondamentali e la serie del grafico del patrimonio.

## Struttura
```
app/
  main.py            avvio FastAPI, collega le pagine
  run.py             fa partire il server e apre il browser
  requirements.txt   librerie (gratuite)
  shared/            database, config, i18n, impostazioni, agente AI
  portfolio/         posizioni, prezzi/fondamentali, analisi, PAC, patrimonio
  finance/           conti, categorie, movimenti, sintesi del mese
  news/              lettura del news-monitor (sola lettura)
  emails/            layout condiviso delle email del monitor
  templates/         pagine HTML (design system MyMoney)
  static/            styles.css + tokens/ + mymoney.css (design freeze v1.0)
  data/              database locale + chiavi  ← MAI su GitHub (.gitignore)
```

## Privacy
- I dati finanziari e le chiavi API stanno **solo** in `app/data/` (escluso dal repo).
- Il server ascolta solo su `127.0.0.1` (il PC stesso), non è esposto alla rete.
- All'agente AI arrivano solo dati aggregati e anonimi (mai ISIN, quantità o IBAN).
