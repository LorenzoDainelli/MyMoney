# MyMoney v2 — Piano a fasi: l'app sul telefono (iPhone), offline, con sync stile Cashew

> ⚠️ **Superato dalla Fase 5 del piano cloud (09/08/2026).** Questo documento
> descrive la sincronizzazione fra PC, telefono e Google Drive: quel meccanismo
> **non esiste più**. I dati stanno in un solo database sul cloud e telefono e PC
> sono due finestre sulla stessa app. Resta qui come storia di come ci si è
> arrivati, non come descrizione di com'è adesso. Vedi `docs/PIANO-CLOUD.md`.

> Scritto il 12/07/2026, alla chiusura della **v1.0** (tag `v1.0` su GitHub +
> backup locale). Questo documento è il progetto ESECUTIVO della versione 2:
> obiettivi, architettura, fasi, problemi previsti, alternative valutate e cosa
> serve da Lorenzo. È scritto per essere ripreso in mano tra mesi senza contesto.

---

## 1. Obiettivo della v2

**Usare MyMoney anche sull'iPhone, ovunque (anche offline), a costo zero,
senza rinunciare alla privacy** (i dati restano di Lorenzo: telefono + PC +
il SUO Google Drive, nessun server di terzi).

In concreto:
1. Sul telefono si apre "l'app" (icona in home, schermo intero) e si possono
   **vedere i saldi e registrare movimenti anche senza connessione**.
2. PC e telefono lavorano **ognuno per conto suo** e poi si **fondono senza
   perdere nulla** (modello Cashew: sync a livello di singolo movimento, non
   sovrascrittura del file).
3. **Porta aperta**: se un domani vorremo un'app nativa 1:1 negli store, i
   dati e il motore di sync saranno già pronti — si rifarebbe solo la UI.

### Cosa NON cambia (vincoli ereditati dalla v1)
- **Tutto gratuito**: niente host a pagamento, niente API a pagamento.
- **Privacy**: nessun dato finanziario su server di terzi; a Gemini mai
  ISIN/importi/nomi; mai esporre chiavi/token nel codice o nel repo.
- **Design MyMoney** (freeze v1.0): la PWA riusa gli stessi token/CSS.
- **Mai segnali operativi** nella parte investimenti.

### Decisioni già prese (con Lorenzo, luglio 2026)
- Telefono = **iPhone**, PC = **Windows** → l'app nativa oggi non è
  praticabile (servirebbe Mac + 99$/anno). Strada scelta: **PWA**.
- Sync = **modello Cashew**: Google Drive personale come "corriere",
  fusione a record, senza server centrale.
- Le parti **investimenti/AI/notizie** continuano a girare sul PC (server
  FastAPI): sul telefono arrivano in **sola lettura** (ultimi dati noti).

---

## 2. Architettura bersaglio

```
   ┌─────────────────────┐         ┌──────────────────────────┐
   │  PC (com'è oggi)    │         │  iPhone (nuovo)          │
   │  FastAPI + SQLite   │         │  PWA statica             │
   │  investimenti, AI,  │         │  UI Finanze offline      │
   │  notizie, finanze   │         │  dati in IndexedDB       │
   └─────────┬───────────┘         └──────────┬───────────────┘
             │  client sync Python            │  client sync JS
             │  (il PC è "un dispositivo")    │
             ▼                                ▼
   ┌──────────────────────────────────────────────────────────┐
   │     Google Drive di Lorenzo — cartella nascosta (appData)│
   │  changes-<device>.jsonl  (log di modifiche per device)   │
   │  snapshot.json           (fotografia periodica)          │
   └──────────────────────────────────────────────────────────┘
```

Principi:
- **Ogni dispositivo ha una copia completa e funzionante** dei dati Finanze.
- Su Drive non c'è "il database", ma **il diario delle modifiche** di ogni
  dispositivo (+ uno snapshot per far partire in fretta un dispositivo nuovo).
- Sincronizzare = **caricare il proprio diario, scaricare quello degli altri,
  applicare le novità** (upload + download, come dichiara Cashew).
- Il PC non è speciale: è un dispositivo come il telefono, con un piccolo
  client di sync in Python dentro l'app attuale.

---

## 3. Le fasi

> Ogni fase è autonoma, si chiude con qualcosa che Lorenzo può PROVARE, e si
> committa/pusha subito (regola: mai lavoro non salvato). Ordine pensato per
> ridurre il rischio: prima le fondamenta invisibili, poi le cose visibili.

### Fase 0 — Fondamenta dati sul PC (prerequisito di tutto)  [taglia: S]  ✅ FATTA
Cosa: preparare i dati delle Finanze alla vita multi-dispositivo, SENZA
cambiare nulla per l'utente.
> Fatto: uid/updated_at/rev/deleted su Wallet/Category/Transaction; timbratura
> automatica via evento SQLAlchemy `before_flush`; migrazione idempotente +
> backfill. 19/19 test unit + migrazione reale sul DB + E2E browser. Il
> soft-delete (uso del flag `deleted`) resta cablato dalla Fase 4.
- Colonna **`uid`** (UUID) su transazioni, wallet e categorie (la chiave
  numerica attuale resta per uso interno; il `uid` è l'identità "universale"
  con cui i dispositivi si parlano). Backfill dei dati esistenti.
- Colonna **`updated_at`** (aggiornata a ogni modifica) e **`deleted`**
  (cancellazione "morbida": la riga resta come lapide/tombstone, così la
  cancellazione può viaggiare nel sync).
- Colonna **`rev`** (contatore di revisione per record, stile Lamport: ad ogni
  modifica `rev = max(rev)+1`) → i conflitti si decidono con (rev, updated_at,
  device_id), NON col solo orologio (gli orologi mentono).
- Migrazione idempotente in `migra_schema()` come già fatto per il giro.
- Le query esistenti imparano a ignorare `deleted=1`.
- **Criterio di fine**: l'app si comporta ESATTAMENTE come prima; test che
  creano/modificano/cancellano e verificano uid/rev/tombstone.

### Fase 1 — API JSON delle Finanze sul PC  [taglia: S]  ✅ FATTA
Cosa: esporre in JSON ciò che oggi è solo HTML, per PWA e sync. SOLA LETTURA:
il canale di SCRITTURA/fusione (`POST /ops`) è stato spostato alla Fase 4, dove
vive col protocollo di sync (scelta di coerenza).
- `GET /api/finanze/stato` → wallet (con saldo attuale), categorie, totale, mese
- `GET /api/finanze/movimenti?since=<ISO>&limit=<n>` → movimenti in formato sync
- Riferimenti tra record per **uid**, mai per id interno. File `finance/api_routes.py`.
- Solo su 127.0.0.1 come oggi; niente autenticazione nuova (finché l'app non
  esce dal PC non serve).
- **Fatto/verificato**: `curl /stato` e `/movimenti` OK; delta `since` OK
  (torna solo i movimenti più recenti); nessun errore server.

### Fase 2 — Guscio PWA installabile sull'iPhone  [taglia: M]  ✅ FATTA (manca il deploy dell'utente)
> Fatto: cartella `pwa/` (index.html, styles.css con i token MyMoney, app.js,
> sw.js service worker, manifest.webmanifest, icone PNG €-su-lime generate).
> Percorsi relativi (funziona sotto /pwa/ in locale e alla radice su Cloudflare).
> Montata anche in FastAPI (`/pwa`) per prova/LAN. Verificato: manifest valido,
> SW registrato e attivo (9 file del guscio in cache, pagina controllata dal SW →
> apertura offline), icone servite, e quando l'API del PC è raggiungibile mostra
> i portafogli/saldi reali. **Resta il passo manuale dell'utente**: pubblicare
> `pwa/` su Cloudflare Pages e installarla su iPhone (guida in `pwa/README.md`).
Cosa: la "app" che si installa dalla home, ancora con pochi contenuti.
- Progetto statico `pwa/` nel repo: HTML + CSS (token MyMoney riusati) + JS
  vanilla. **Nessun framework**: coerente con l'app (vanilla) e leggero.
- `manifest.json` (nome, icona, colori, display standalone) + **service
  worker** (cache dell'app shell → apre anche offline).
- Hosting statico del guscio: **Cloudflare Pages** (gratis, funziona con repo
  GitHub privato). NOTA: GitHub Pages gratis richiederebbe repo pubblico → no.
  In alternativa: un repo pubblico separato SOLO per il guscio (zero dati
  dentro). Decidere con Lorenzo (→ §6 "Cosa serve da te").
- Sul guscio NON c'è alcun dato: i dati vivono nel telefono e su Drive.
- **Criterio di fine**: Lorenzo apre l'URL da Safari, "Aggiungi a Home",
  l'icona MyMoney si apre a schermo intero anche in modalità aereo.

### Fase 3 — Finanze offline sul telefono  [taglia: L]  ✅ FATTA (giro-create in PWA = follow-up)
> Fatto: `pwa/db.js` (IndexedDB: wallets/categorie/movimenti/meta, chiave = uid),
> `pwa/finance.js` (saldi + riepilogo mese IN JS, mirror di service.py, giro
> incluso), `app.js` riscritto (pull dall'API quando c'è rete → IndexedDB; render
> SEMPRE dai dati locali; form "aggiungi movimento" offline per entrata/uscita/
> trasferimento con auto-creazione categoria). Ogni record locale nasce con
> uid/rev/updated_at/`_local` (pronto per il sync). Verificato: seed dal PC,
> aggiunta uscita 10€ (Hype 91,98→81,98, uscite mese 10), persistenza dopo reload,
> il movimento locale sopravvive al ripull, e con API irraggiungibile rende tutto
> da IndexedDB. **Follow-up**: creare PARTITE DI GIRO dal telefono (ora nella PWA
> si aggiungono entrata/uscita/trasferimento; i giri si vedono/calcolano ma si
> creano dal PC).
Cosa: la PWA diventa una vera app Finanze.
- Dati locali in **IndexedDB** (movimenti, wallet, categorie) con lo stesso
  modello della Fase 0 (uid, rev, updated_at, deleted).
- UI: saldi per portafoglio, aggiungi movimento (entrata/uscita/trasferimento/
  **partita di giro multi-operazione**, stessa logica della v1), registro,
  sintesi mese. Riuso dei layout mobile del design freeze.
- `navigator.storage.persist()` per chiedere a iOS di non cancellare i dati;
  banner di avviso se l'ultima sincronizzazione è vecchia (>7 giorni).
- **Criterio di fine**: in modalità aereo si registrano spese sul telefono e i
  saldi tornano; chiudendo e riaprendo l'app i dati sono ancora lì.

### Fase 4 — Motore di sync stile Cashew  [taglia: L, il cuore della v2]
Cosa: la fusione senza perdite tra PC e telefono, via Drive.
- Ogni dispositivo ha un **device_id** (generato al primo avvio).
- Ogni modifica locale finisce anche in un **diario append-only** locale:
  `{uid, tipo_entita, op: upsert|delete, campi, rev, updated_at, device_id}`.
- Sincronizzazione (manuale con bottone + automatica all'apertura):
  1. **upload**: accoda il proprio diario su Drive (`changes-<device>.jsonl`
     nella cartella nascosta appDataFolder);
  2. **download**: legge i diari degli ALTRI dispositivi dal punto in cui era
     rimasto (cursore per file);
  3. **merge**: applica le operazioni; conflitto sullo stesso record →
     vince (rev, updated_at, device_id) più alto (**last-write-wins per
     record**); le cancellazioni sono tombstone e vincono a parità.
- **Snapshot periodico** (`snapshot.json`): ogni N sync il dispositivo che
  sincronizza scrive la fotografia completa → un dispositivo nuovo riparte
  dallo snapshot + code recenti (non rilegge mesi di diari).
- **Compattazione**: diari più vecchi dello snapshot si potano.
- Perché funziona bene per noi: i movimenti sono quasi solo **aggiunte** →
  la fusione è un'unione; i conflitti veri (stesso movimento modificato su due
  dispositivi senza sync in mezzo) sono rari e il LWW è accettabile.
- Il PC partecipa con un client Python (`app/shared/sync.py`), il telefono
  con lo stesso protocollo in JS. **Il protocollo va scritto UNA volta e
  documentato in `docs/SYNC-PROTOCOL.md`**: è il contratto tra i due mondi
  (e domani anche dell'eventuale app nativa).
- **Criterio di fine** (da provare in due scenari):
  - PC aggiunge X, telefono aggiunge Y (offline) → dopo sync entrambi hanno
    X+Y, saldi identici al centesimo;
  - modifica/cancella su un lato → l'altro la riceve; doppia modifica dello
    stesso record → vince l'ultima, nessun duplicato, nessuna perdita.

### Fase 5 — Collegamento a Google Drive (OAuth)  [taglia: M]
Cosa: l'autorizzazione a usare il Drive di Lorenzo come corriere.
- Scope **minimo**: `drive.appdata` (la app vede SOLO la sua cartellina
  nascosta, non i file di Drive). Stessa scelta di Cashew.
- Serve un progetto su Google Cloud (gratuito) con OAuth consent screen in
  modalità "testing" (basta per uso personale, nessuna verifica Google):
  **questa parte la fa Lorenzo con la mia guida passo-passo** — creare account
  /credenziali è un'azione sua, io preparo istruzioni e incollo i client id
  nei posti giusti.
- PC: flusso "installed app" (apre il browser una volta, token salvato in
  `app/data/`, MAI nel repo). Telefono: flusso web con redirect alla PWA,
  token in IndexedDB.
- Fallback sempre disponibile: **export/import file manuale** (scarichi un
  file dal PC, lo apri nella PWA o viceversa) — utile anche come backup e
  come "sync senza Drive" in emergenza.
- **Criterio di fine**: il bottone "Sincronizza" funziona su entrambi i
  dispositivi contro il Drive vero; revocare l'accesso da Google interrompe
  il sync senza rompere i dati locali.

### Fase 6 — Investimenti e notizie sul telefono (sola lettura)  [taglia: M]
Cosa: il resto dell'app, nel modo onesto.
- I dati di portafoglio/prezzi/notizie richiedono il server (Yahoo/chiavi):
  il PC pubblica nel canale di sync uno **"snapshot di sola lettura"**
  (valore portafoglio, posizioni, ultimo grafico, ultime notizie) e la PWA
  lo mostra con l'etichetta "aggiornato al …".
- Niente prezzi live sul telefono in v2 (fuori scope; eventualmente v3 con
  tunnel Tailscale verso il PC acceso).
- **Criterio di fine**: sul telefono si vede la dashboard con patrimonio e
  ultime notizie coerenti con l'ultima apertura del PC.

### Fase 7 — Rifiniture e robustezza  [taglia: M]
- **Versione del protocollo** nel diario (`schema: 2`): un dispositivo vecchio
  che incontra operazioni più nuove si ferma e chiede di aggiornare (mai
  corrompere).
- Test "multi-dispositivo simulato" automatici (due DB + un finto Drive su
  cartella locale) nel CI mentale di ogni modifica al sync.
- Gestione bordi: orologio sballato, sync interrotto a metà (i diari sono
  append-only → ripartire è sicuro), quota Drive piena, token scaduto.
- Piccole cose emerse per strada: PIN locale opzionale per aprire la PWA,
  pulizia tombstone > 1 anno, indicatore "ultima sync" in header.

### Tracce OPZIONALI parallele (non bloccano la v2)
- **v2.1 — Import CSV** degli estratti conto (Hype/Revolut/AIB/PayPal/TR):
  la vecchia "Idea B", con dedup per (data, importo, descrizione normalizzata).
- **v2.2 — Auto-aggiornamento prezzi** ogni X minuti mentre l'app PC è aperta.
- **v2.3 — PIN/password locale** anche sull'app PC.

---

## 4. Problemi previsti (e come li disinneschiamo)

| # | Problema | Impatto | Mitigazione |
|---|----------|---------|-------------|
| 1 | iOS può cancellare i dati della PWA (spazio/inutilizzo) | perdita copia locale | Drive è la fonte di verità; `storage.persist()`; banner "sincronizza"; snapshot |
| 2 | Orologi diversi tra PC e telefono | ordine modifiche sbagliato | contatore `rev` per record (Lamport), orologio solo come spareggio |
| 3 | Stesso record modificato su 2 dispositivi | conflitto | LWW per record, documentato; caso raro per come si usa l'app |
| 4 | Cancellazioni che "risorgono" dopo un merge | dati zombie | tombstone sincronizzate, mai DELETE fisico prima della compattazione |
| 5 | Sync interrotto a metà (rete che cade) | stato incoerente | diari append-only + cursori: si riprende da dove si era, idempotente |
| 6 | Token OAuth scade/revocato | sync fermo | refresh automatico; se fallisce, UI chiara + export/import manuale |
| 7 | Schema che evolve (v2→v3) | dispositivi disallineati | campo `schema` nel diario; il vecchio si rifiuta e chiede aggiornamento |
| 8 | GitHub Pages richiede repo pubblico | esporre il codice | Cloudflare Pages (repo privato) o mini-repo pubblico col solo guscio |
| 9 | Safari-only per installare la PWA | UX installazione | guida di 3 passi con screenshot dentro la pagina stessa |
| 10 | Due sync simultanei (PC e telefono insieme) | scritture in gara | ogni device scrive SOLO il proprio file su Drive → niente collisioni |
| 11 | La partita di giro è un GRUPPO di righe | fusione parziale del gruppo | le gambe viaggiano con `giro_id`; il gruppo si ricostruisce dai record, mai spezzato dal sync |
| 12 | Categorie/wallet creati uguali su 2 device ("Cena" e "Cena") | doppioni | matching per nome normalizzato al merge + unificazione uid |
| 13 | Numeri con virgola/punto, fusi orari | importi/date sballate | nel protocollo SOLO ISO-8601 UTC e numeri con punto; la UI localizza |
| 14 | Drive API ha quote (20k richieste/giorno) | blocco sync | siamo 2 dispositivi con sync all'apertura: irrilevante, ma batch comunque |

## 5. Ragionamenti fuori dagli schemi (alternative valutate)

- **CRDT (fusione matematicamente perfetta)**: affascinante, ma per un
  registro di spese quasi-append-only è un cannone per una mosca. Il modello
  diari+LWW è quello di Cashew e basta. → Scartato (ma il protocollo a diari
  non lo preclude: si può evolvere).
- **SQLite WASM nel browser** invece di IndexedDB: riuseremmo il pensiero SQL,
  ma su iOS Safari la persistenza è meno affidabile e aggiunge 1-2 MB di
  runtime. IndexedDB con uno strato "repository" sottile vince in robustezza.
  → Scartato per ora; lo strato repository rende il cambio possibile.
- **Tailscale-only (niente PWA)**: zero lavoro dati, ma richiede PC acceso e
  non dà offline. → Tenuto come complemento (per prezzi live in v3), non
  come soluzione.
- **File unico su Drive con lock/ETag**: più semplice del modello a diari, ma
  è il modo in cui i dati "si rovinano" (ultimo che scrive vince su TUTTO).
  → Scartato: il requisito X+Y esige la fusione a record.
- **Firebase/Supabase come backend sync**: comodo, ma i dati finanziari
  finirebbero su un cloud di terzi e il free tier è un vincolo esterno.
  → Scartato per filosofia del progetto.
- **App nativa subito**: bloccata da Windows+iPhone (serve Mac, 99$/anno).
  → Porta aperta: il protocollo di sync e le API sono il 70% del lavoro
  riusabile; la UI nativa sarebbe l'unico pezzo nuovo.

## 6. Cosa serve da te (Lorenzo)

1. **Decisione hosting guscio PWA** (Fase 2): Cloudflare Pages collegato al
   repo privato (consigliato) oppure mini-repo pubblico col solo guscio.
2. **Progetto Google Cloud + OAuth** (Fase 5): lo crei tu (è il tuo account),
   io ti do la guida passo-passo e integro le credenziali. ~15 minuti.
3. **Prove su iPhone** alla fine delle Fasi 2, 3, 4, 5 (io non ho un iPhone:
   i tuoi test sono il collaudo vero).
4. **Conferma del piano**: quale fase apro per prima? (consiglio: Fase 0+1
   insieme, sono piccole e sbloccano tutto).

## 7. Regole di lavoro per la v2 (le solite, nero su bianco)

- Una fase alla volta; a fine fase: test + commit + merge + push + Lorenzo
  prova. Mai due fasi aperte insieme.
- Il DB reale non si tocca mai senza migrazione idempotente + backup.
- Ogni pezzo di sync ha test automatici PRIMA di toccare Drive vero
  (finto-Drive su cartella locale).
- Segreti (token, client id sensibili) mai nel repo: stanno in `app/data/`
  (gitignorata) o nel telefono.
- Questo file si AGGIORNA a ogni fase chiusa (stato: ☐ → ☑).

## 8. Stato avanzamento

- ☑ v1.0 chiusa, taggata e backuppata (12/07/2026)
- ☑ Fase 0 — Fondamenta dati (uid, rev, updated_at, tombstone) — 13/07/2026
- ☑ Fase 1 — API JSON Finanze (sola lettura; POST /ops spostato a Fase 4) — 13/07/2026
- ☑ Fase 2 — Guscio PWA installabile (manca solo il deploy Cloudflare dell'utente) — 13/07/2026
- ☑ Fase 3 — Finanze offline sul telefono (IndexedDB; giro-create in PWA = follow-up) — 13/07/2026
- ☑ Fase 4 — Motore di sync (diari + merge LWW, POST /ops, soft-delete) — 14/07/2026
      (sviluppata da Antigravity, revisionata e rifinita; sync HTTP solo in LAN
      finché resta HOST=127.0.0.1 → il sync telefono↔PC diventa reale con la Fase 5)
- ☑ Fase 5 — OAuth Google Drive — 15/07/2026, COLLAUDATA end-to-end
      (piano-contratto in PIANO-FASE-5.md; su Drive viaggia lo STATO completo
      per device `state-<id>.json` in appDataFolder, merge LWW della Fase 4;
      PC: OAuth installed-app+PKCE via urllib, card in Impostazioni, sync
      all'avvio; PWA: redirect implicit senza popup/script esterni, bottone ☁️;
      test con finto-Drive 12/12). Credenziali OAuth create dall'utente
      (guida docs/SETUP-DRIVE.md); verificato: i movimenti del PC compaiono
      sull'iPhone via Drive. Lungo il collaudo risolti anche 3 problemi PWA/iOS:
      avvio robusto se drive.js manca, e soprattutto il blocco WebKit "Response
      served by service worker has redirections" (Cloudflare fa 308 /index.html
      → /: il SW ora ripulisce le risposte dai redirect prima di cacharle, v8).
- ☐ Fase 6 — Dashboard sola-lettura sul telefono (SALTATA per scelta utente)
- ☑ Fase 7 — Robustezza e rifiniture — 15/07/2026
      (piano-contratto PIANO-FASE-7.md; SVILUPPATA da Antigravity, review Fase 3
      di Claude Code = PROMOSSO + rifiniture. Contenuti: guardia di versione
      schema forward-compat — un dispositivo salta le op di schema più nuovo e
      alza un avviso "aggiorna", che si auto-spegne a sync pulita; test
      multi-dispositivo simulato — 2 DB + finto-Drive, convergenza X+Y e LWW;
      indicatore "ultima sync" + avviso >7 giorni sulla PWA; messaggi Drive
      quota/token; compattazione tombstone transazioni >365gg all'avvio.
      44/44 test verdi.)
- ☐ (opz.) v2.1 Import CSV · v2.2 auto-prezzi · v2.3 PIN locale
