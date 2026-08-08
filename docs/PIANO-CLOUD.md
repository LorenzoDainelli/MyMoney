# MyMoney sul cloud — piano di sviluppo

Scritto il 03/08/2026, dopo la decisione di portare l'app su Google Cloud con i
**dati veri** e un **login serio (Google + 2FA)**.

Questo documento è il piano: cosa c'è oggi, dove vogliamo arrivare, in che
ordine, e — soprattutto — le cose che si romperebbero se le ignorassimo.

---

## 0. Com'è fatto oggi (e perché conta)

Non c'è "l'app": ci sono **tre pezzi** che si parlano attraverso Google Drive.

1. **L'app sul PC** — FastAPI + SQLite (`app/`). È quella completa: finanze,
   portafoglio, agente AI, notizie. Ascolta solo su `127.0.0.1`, quindi la vede
   solo il PC.
2. **La PWA del telefono** (`pwa/`) — è **autonoma**: ha il suo database dentro
   il telefono (`db.js`) e la sua sincronizzazione (`sync.js`, `drive.js`). Non
   chiama nessun server: parla direttamente con Google Drive.
3. **Google Drive** — fa da corriere. Ogni dispositivo carica il suo stato in una
   cartella nascosta, scarica quello degli altri, e i due si fondono con la
   regola "vince la modifica più recente".

**Questo va detto chiaramente: il telefono già funziona.** Il cloud non serve a
sbloccare qualcosa di impossibile, serve a **semplificare un'architettura che
oggi ha tre copie degli stessi dati** e un meccanismo di fusione da mantenere.

E quel meccanismo costa: i due bug inseguiti a fine luglio (il collegamento fra
le righe della carta che non viaggiava, le righe orfane dopo una cancellazione)
erano **entrambi** bug di sincronizzazione. Con un solo database non sarebbero
potuti esistere.

---

## 1. Dove vogliamo arrivare

```
        telefono ─┐
                  ├──► HTTPS ──► Cloud Run (l'app) ──► Cloud SQL (i dati)
        PC ───────┘                    │
                                       └──► Secret Manager (le chiavi)
```

**Una sola app, un solo database, tutti i dispositivi che si collegano lì.**
Niente più copie, niente più fusione, niente più corriere.

Il prezzo: **serve internet**. Senza rete non apri le finanze. Oggi, sul PC,
funzionano anche staccati.

---

## 2. La decisione grossa: che ne facciamo di PWA e sync?

> **DECISO il 03/08/2026 — opzione A: il cloud è l'unica verità.**
> Conseguenze operative: il login protegge **tutto** (non c'è più una parte che
> gira solo in casa); la PWA autonoma e il sync via Drive vanno in pensione alla
> Fase 5; il diario del sync e i suoi backup (§3.2) **non serve** portarli sul
> server, il problema si cancella da solo; e l'app senza rete non si apre — è il
> prezzo accettato.

Va decisa **prima** di scrivere il login, perché cambia cosa proteggere.

**Opzione A — Il cloud è l'unica verità.** ← *scelta.* Il PC e il telefono
diventano due finestre sulla stessa app. Si spengono la PWA autonoma e il sync
via Drive.
- ➕ Un solo posto dove i dati sono veri. Sparisce un'intera classe di bug.
  Il telefono guadagna **tutte** le funzioni, non il sottoinsieme della PWA.
- ➖ Senza rete non c'è app. E il codice del sync (mesi di lavoro) va in soffitta.

**Opzione B — Il cloud è "un altro dispositivo".** Tutto resta com'è, il server
si sincronizza come fanno PC e telefono.
- ➕ Funziona anche offline, niente si butta.
- ➖ Si tiene tutta la complessità **e** se ne aggiunge. Due database che
  scrivono davvero, con la fusione a fare da arbitro: è lo scenario in cui i
  bug di sync fanno più male.

**La mia raccomandazione è la A**, e la ragione è una sola: l'opzione B paga il
prezzo del cloud senza incassarne il beneficio. Ma è una scelta di Lorenzo,
perché è lui che sa quanto gli serve l'app senza rete.

Il sync non si cancella comunque: resta nel repo e nella storia di git, e il tag
`v2.0` è lì apposta.

---

## 3. Le cose che si romperebbero (da sistemare prima dei dati veri)

Trovate leggendo il codice, non immaginate.

### 3.1 Il lavoro in background non gira più
`main.py:80` lancia un thread a ogni avvio che aggiorna prezzi e notizie, fa
girare il sync e soprattutto chiama `storico.registra()` — **la fotografia
giornaliera del patrimonio**, quella che disegna il grafico.

Su Cloud Run l'istanza **si spegne quando non la usi**. Quindi:
- se non apri l'app per due giorni, nello storico ci sono due buchi;
- se la apri dieci volte, chiami Yahoo dieci volte per niente;
- se il server è impegnato ne partono due insieme, e fanno il lavoro doppio.

**Soluzione:** togliere il lavoro dall'avvio e metterlo dietro a un indirizzo
tipo `/lavori/giornaliero`, che **Cloud Scheduler** chiama una volta al giorno.
Protetto da un token, altrimenti chiunque può farlo partire. È gratis nel
piano base.

### 3.2 I file scritti a runtime spariscono
Su Cloud Run il disco viene buttato via a ogni spegnimento. Oggi scriviamo:

| Cosa | Dove | Che fine fa |
|---|---|---|
| Il database | `data/finanza.db` | ✔ già risolto: va su Cloud SQL |
| Diario del sync | `data/sync/` | sparisce → **non è un problema**: con l'opzione A il sync non gira sul server |
| Backup del sync | `data/backups/` | idem |
| Cache notizie | `data/news_remote.json` | sparisce, ma **si può rileggere**: sta su GitHub. Va bene in `/tmp` |

Con la scelta A questo capitolo si riduce a una riga sola: la cache delle
notizie va in `/tmp`, e tutto il resto smette di esistere sul server.

### 3.3 Le chiavi segrete
La chiave Gemini e il file di servizio Vertex oggi stanno nella tabella delle
impostazioni. Con Cloud SQL ci finiscono comunque, ma su un database in rete.
**Meglio spostarle in Secret Manager**, dove Google le cifra e l'app le legge
senza che passino dal database né dal repo.

---

## 4. Il login (la parte delicata)

Regola che mi do: **le password non le gestiamo noi.** Il riconoscimento lo fa
Google, noi ci fidiamo del suo risultato. Meno codice nostro = meno modi di
sbagliare su una cosa dove sbagliare costa caro.

1. **Accesso con Google.** Il flusso OAuth è **già scritto e funzionante** in
   `shared/drive_sync.py` per il sync: stessa meccanica, altro scopo. Si riusa.
2. **Una lista di chi può entrare**, di una riga sola: il suo indirizzo Gmail.
   Chiunque altro faccia il login con Google viene rimbalzato. Questa è la vera
   serratura: senza, "accedi con Google" vuol dire che entra chiunque abbia un
   account Google, cioè il mondo.
3. **Secondo fattore (TOTP)** — il codice a sei cifre dell'app authenticator.
   Serve una libreria standard (`pyotp`), non la scriviamo a mano.
4. **La sessione** — un cookie firmato, valido qualche giorno, `Secure` +
   `HttpOnly` + `SameSite`. Firmato con una chiave che sta in Secret Manager.
5. **Tutto chiuso per default.** Non si protegge pagina per pagina — si chiude
   tutto e si aprono solo login e controllo di salute. Se un domani aggiungiamo
   una pagina e ci dimentichiamo, quella nasce protetta invece che aperta.

Il punto d'aggancio esiste già: `shared/auth.py` è un guscio scritto apposta,
con `get_current_user()` che oggi risponde sempre "utente locale". Si sostituisce
lì dentro, senza toccare il resto.

**Sull'onestà tecnica:** questo è il pezzo in cui un errore non si vede. I test
qui devono provare che **senza cookie valido si viene rimbalzati**, non solo che
con il cookie si entra.

---

## 5. Le fasi, in ordine

L'ordine non è casuale: ogni fase scopre problemi quando ancora non costano
niente, e **i dati veri arrivano per ultimi**.

### Fase 0 — Portabilità del database ✔ FATTA (03/08/2026)
`MYMONEY_DB_URL`, `shared/schema.py`, `scripts/travaso_db.py`,
269 test verdi su tutti e due i motori. Commit `de88461`.

### Fase 1 — Il deploy di prova ✔ FATTA (03/08/2026)
L'app è online e **privata**: `https://mymoney-1057159819758.europe-west8.run.app`
risponde **403 a chi non ha le credenziali del progetto**, 200 con le credenziali
di Lorenzo. Nessun dato vero: il database contiene solo il precarico (8
portafogli, 38 titoli, 0 movimenti).

Provato: tutte e 7 le pagine rispondono; i lavori periodici girano sul server
(sei passi su sei, 2,1 s) e `/lavori/giornaliero` resta 401 senza la parola
d'ordine; le 11 tabelle sono state create su Cloud SQL e lo storico si è
scritto davvero là dentro; nessun errore nei log.

*Da non dimenticare:* `gcloud run deploy --source` non guarda `.dockerignore` ma
**`.gcloudignore`**. Prima di caricare, verificare con
`gcloud meta list-files-for-upload` che `app/data/` non compaia.

#### (com'era pianificata)
- `Dockerfile` (Python slim, dipendenze, avvio con la porta che dà Cloud Run);
- `.dockerignore` — **`app/data/` va escluso**, altrimenti i dati veri finiscono
  nell'immagine;
- indirizzo `/salute` che risponde "sto bene" (serve a Cloud Run per capire se
  l'app è viva);
- primo deploy, con dentro movimenti inventati.

*Serve da Lorenzo:* il login `gcloud`, il progetto, l'accensione dell'istanza
(gli chiedo conferma una volta, riportando il costo stimato).
**Risultato:** l'app raggiungibile via HTTPS, e la prima fattura vera da guardare.

### Fase 2 — I lavori di fondo (dal §3)
Lavoro in background dietro a Cloud Scheduler, file a runtime sistemati, segreti
in Secret Manager. Nessuna dipendenza dal login: si può fare in parallelo.

### Fase 3 — Il login ✔ SCRITTA (08/08/2026), da accendere
Il codice c'è tutto ed è provato: `shared/sicurezza.py` (biglietti firmati e
codici a sei cifre), `shared/auth.py` (le regole), `shared/accesso.py` (il giro
con Google), `shared/accesso_routes.py` (le pagine), il middleware in `main.py`.
**400 test verdi**, di cui 91 sull'accesso e quasi tutti rifiuti.

Il giro: `/accedi` → Google → **biglietto parziale** (non apre niente, vale dieci
minuti) → codice a sei cifre → biglietto completo. La prima volta si passa da
`/accedi/attiva`, che si attacca l'app authenticator; il segreto si salva **solo
dopo** che un codice giusto ha dimostrato che il telefono lo sa fare.

Provato in locale col giro completo via HTTP: porta chiusa, parziale che non
apre, codice sbagliato respinto, codice giusto che entra, uscita che esce
davvero, e il secondo fattore che **non si può riattivare** una volta attivo.

**Manca solo l'accensione**, e dipende da una cosa che deve fare Lorenzo: creare
il client OAuth (§9). Poi il deploy con le variabili, e il primo accesso vero.

*Cosa NON è stato riusato, di proposito:* l'OAuth di `drive_sync.py`. È lo stesso
ballo, ma quel modulo va in pensione alla Fase 5 e la porta di casa non si lega a
un modulo già condannato. In più `drive_sync` tiene lo stato del giro **in
memoria**: su Cloud Run, dove l'app gira in più copie e si spegne da sola, il
ritorno da Google può bussare a un'istanza diversa e lì quella variabile non
esiste. Qui lo stato viaggia in un cookie firmato.

### Fase 4 — I dati veri
Travaso (lo script c'è ed è provato), verifica riga per riga, e **il vecchio
database resta dov'è** finché non passa qualche settimana serena.

### Fase 5 — Le tre copie diventano una
Dipende dalla scelta del §2. Se A: il PC e il telefono puntano al cloud, PWA
autonoma e sync Drive vanno in pensione.

---

## 6. Costi

| Voce | Al mese |
|---|---|
| Cloud Run | **0 €** — l'uso di una persona sta nel gratuito permanente |
| Cloud SQL (il database) | **~10-15 €** ← è qui tutto il costo |
| Secret Manager, Scheduler | ~0 € |
| HTTPS e indirizzo | 0 €, inclusi |

I ~258 € di crediti coprono tutto fino a metà settembre 2026. Decisione di
Lorenzo del 03/08/2026: **usarli**, e alla scadenza decide lui cosa tenere
acceso. Il vincolo che resta è uno solo: niente spese di tasca sua.

**Attenzione, verificato:** la fatturazione è **attiva** sul progetto. Quando i
crediti finiscono, ciò che è rimasto acceso inizia ad addebitare da solo. Cloud
Run no (resta nel gratuito), Cloud SQL sì. **Si può mettere in pausa** senza
perdere i dati: è la leva da usare a settembre.

### Cosa è stato acceso davvero (03/08/2026)

| Cosa | Scelta | Perché |
|---|---|---|
| Progetto | `mymoney-502422` | già creato da Lorenzo |
| Regione | `europe-west8` (Milano) | la più vicina, meno ritardo |
| Database | Cloud SQL PostgreSQL **17** | la stessa versione provata in locale |
| Macchina | `db-f1-micro`, edizione **ENTERPRISE** | la più piccola |
| Disco | 10 GB HDD | il minimo; i dati sono 196 KB |
| Segreti | `mymoney-db-password`, `mymoney-job-token` | in Secret Manager, mai nel repo |
| Permessi | solo `cloudsql.client` e `secretmanager.secretAccessor` | il minimo per funzionare |

**Trappola incontrata:** senza specificare l'edizione, Google sceglie
**ENTERPRISE_PLUS**, che costa parecchio di più e non accetta nemmeno le
macchine piccole. Va sempre passato `--edition=ENTERPRISE`.

---

## 7. Come si torna indietro

In ogni momento, perché:
- il tag **`v2.0`** è lo stato prima di tutto questo, su GitHub;
- una copia dei dati è in `Desktop\Claude\backup-v2.0-2026-08-03\`;
- l'app senza `MYMONEY_DB_URL` riapre il file SQLite e si comporta **esattamente**
  come prima — è così per costruzione, non per fortuna;
- il travaso sa fare anche il viaggio di ritorno (i motori sono due parametri).

Il punto di non ritorno morbido è la **Fase 5**: da lì in poi tornare indietro
significa rimettere in piedi la sincronizzazione. Prima di quella, è un attimo.

---

## 8. Rischi, detti chiaramente

| Rischio | Quanto | Cosa facciamo |
|---|---|---|
| Errore nel login: l'app resta aperta | **alto** | Chiuso per default; test che provano il rimbalzo; lista di un indirizzo |
| Dimenticare acceso il database | medio | Avviso di budget a 5 €; promemoria di metterlo in pausa |
| Buchi nello storico del patrimonio | medio | Cloud Scheduler (§3.1) prima dei dati veri |
| Perdere dati nel travaso | basso | Verifica riga per riga, già provata; il vecchio database resta |
| Costo insostenibile a crediti finiti | basso | Il numero si conosce prima; si torna indietro col §7 |
| I dati veri esposti prima del login | **alto** | I dati veri entrano in Fase 4, il login è in Fase 3 |

---

## 9. Accendere il login: cosa serve, e chi lo fa

Il codice è pronto. Restano tre cose, e la prima **non posso farla io**: entrare
nella console Google vuol dire entrare nell'account di Lorenzo.

### 9.1 Il client OAuth (lo fa Lorenzo, una volta)

Nella console Google Cloud, progetto `mymoney-502422`:

1. **API e servizi → Schermata consenso OAuth**. Tipo **Esterno**, stato
   **In test**. In «Utenti di test» aggiungere il proprio indirizzo Gmail — con
   l'app in test entra solo chi è in quella lista, ed è un lucchetto in più
   *prima* del nostro.
2. **Credenziali → Crea credenziali → ID client OAuth**, tipo **Applicazione
   web**. In «URI di reindirizzamento autorizzati», **esattamente** questo:
   ```
   https://mymoney-1057159819758.europe-west8.run.app/accedi/google/ritorno
   ```
   Google confronta carattere per carattere: uno slash in più e rifiuta.
3. Le due stringhe che escono (ID e segreto) vanno **in un file**, non in chat:
   `C:\Users\loren\Desktop\Claude\tools\oauth.txt`, ID sulla prima riga, segreto
   sulla seconda. Da lì le leggo io.

### 9.2 Le variabili del server (le metto io, dopo)

| Variabile | Cos'è | Dove sta |
|---|---|---|
| `MYMONEY_SESSION_KEY` | firma i biglietti di sessione | Secret Manager |
| `MYMONEY_OAUTH_CLIENT_SECRET` | il segreto del client | Secret Manager |
| `MYMONEY_OAUTH_CLIENT_ID` | l'ID del client (non è un segreto) | variabile |
| `MYMONEY_EMAIL_CONSENTITE` | chi può entrare — una riga sola | variabile |
| `MYMONEY_BASE_URL` | `https://mymoney-…run.app` | variabile |

**Nessuna ha un valore di ripiego, ed è voluto.** Senza `MYMONEY_SESSION_KEY`
l'app non finge un login: resta quella di casa. Con la lista vuota non entra
nemmeno il proprietario. Meglio chiusi fuori che aperti a tutti.

### 9.3 L'ordine dei passi

1. Deploy con le variabili.
2. **Aprire il servizio** (`--allow-unauthenticated`). Non è un passo
   rimandabile: finché Cloud Run è privato risponde 403 a un browser normale, e
   il ritorno da Google atterra proprio su un browser normale. Il login non si
   può provare a servizio chiuso.
3. Primo accesso di Lorenzo e attivazione dell'app authenticator, **subito**.
4. I dati veri (Fase 4) solo **dopo** che la porta ha retto qualche giorno.

Sul punto 3, la domanda giusta è: fra l'apertura e l'attivazione, chi arriva
prima al mio telefono? **Nessuno, e non per fortuna.** La pagina di attivazione
chiede il biglietto parziale, e il biglietto parziale si ottiene solo passando
la lista di chi può entrare — che ha una riga sola. Uno sconosciuto che facesse
il login con Google verrebbe respinto *prima*. In quella finestra il rischio
esiste per una persona sola: chi già controlla l'account Google di Lorenzo.

Ci sono comunque due lucchetti in più, e sono gratis: la schermata di consenso
**in test** (entra solo chi è fra gli utenti di test), e il fatto che
l'attivazione si fa in un minuto.
