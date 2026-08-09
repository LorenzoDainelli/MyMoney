# PIANO-FASE-7 — Robustezza e rifiniture del sync

> ⚠️ **Superato dalla Fase 5 del piano cloud (09/08/2026).** Questo documento
> descrive la sincronizzazione fra PC, telefono e Google Drive: quel meccanismo
> **non esiste più**. I dati stanno in un solo database sul cloud e telefono e PC
> sono due finestre sulla stessa app. Resta qui come storia di come ci si è
> arrivati, non come descrizione di com'è adesso. Vedi `docs/PIANO-CLOUD.md`.

> **Piano-contratto** secondo WORKFLOW-AGENTI.md §4. Fase 1 (progetto) scritta da
> Claude Code il 15/07/2026. **Fase 2 (esecuzione): Antigravity.** Fase 3 (review):
> Claude Code.
>
> Antigravity: implementa **esattamente** ciò che è scritto qui. Se un punto è
> ambiguo o manca qualcosa, **fermati e segnala** invece di improvvisare (§2 del
> workflow). Non prendere decisioni di architettura. Rispetta lo stile del codice
> esistente (vanilla JS senza framework lato PWA; Python stdlib lato PC, nessuna
> nuova dipendenza). Ogni task si chiude solo quando i suoi test passano.
>
> Contesto necessario (già nel repo, leggilo prima): `docs/SYNC-PROTOCOL.md`,
> `app/shared/sync.py`, `app/shared/drive_sync.py`, `pwa/sync.js`, `pwa/drive.js`,
> `app/tests/test_sync.py`, `app/tests/test_drive_sync.py`.

## Obiettivo della fase

Rendere il sync (Fasi 4-5) a prova di uso quotidiano: (1) **compatibilità di
versione** che non corrompe mai i dati tra versioni diverse dell'app; (2) un
**test multi-dispositivo simulato** che dimostra la convergenza; (3) rifiniture
di robustezza e chiarezza (indicatore "ultima sync" + avviso se vecchia; messaggi
d'errore Drive comprensibili; pulizia dei tombstone vecchissimi).

**Vincolo trasversale (tutti i task):** nessuna nuova dipendenza Python o JS;
mai loggare/mostrare token o credenziali; nessun dato finanziario (importi,
descrizioni, nomi) nei log; comportamento invariato quando le nuove condizioni
non si verificano (schema uguale, sync recente, nessun tombstone vecchio).

---

## T1 — Guardia di versione dello schema (forward-compat)

**Problema.** Oggi `SCHEMA_VERSION = 1`. Se un domani bumpiamo il formato a 2, un
dispositivo ancora alla 1 non deve **applicare** operazioni di schema 2 (rischio
di scrivere dati che non capisce). Regola: un messaggio con `schema ≤ locale` si
applica; con `schema > locale` si **salta** (non è un errore) e si alza un flag
"aggiornamento necessario". `SCHEMA_VERSION` **resta 1** in questa fase: qui si
costruisce solo la guardia, che diventerà utile al primo vero bump.

### T1.a — PC: `app/shared/sync.py`
- Aggiungi un helper:
  ```python
  def _schema_troppo_nuovo(s) -> bool:
      """True se il messaggio è di uno schema più nuovo di quello che capiamo."""
      try:
          return int(s) > SCHEMA_VERSION
      except (TypeError, ValueError):
          return False   # schema assente/illeggibile = trattalo come 1 (legacy)
  ```
- In `import_ops` (riga ~275): il dict di ritorno deve avere una nuova chiave
  `future` (int, default 0). Nel ciclo su `by_entity[entity]`, PRIMA di chiamare
  `_apply_one`, se `_schema_troppo_nuovo(op.get("schema"))` → `future += 1` e
  `continue` (non chiamare `_apply_one`, non contarlo come `errors`).
  Alla fine, se `future > 0`, chiama
  `settings_store.set_setting("sync_needs_update", "1")`.
  Ritorna `{"applied", "skipped", "errors", "future"}`.
- In `apply_snapshot` (riga ~428): all'inizio, se
  `_schema_troppo_nuovo(data.get("schema"))` → NON costruire ops; chiama
  `settings_store.set_setting("sync_needs_update", "1")` e ritorna
  `{"applied": 0, "skipped": 0, "errors": 0, "future": 1}`.
- `import_bundle` deve continuare a funzionare: somma anche `future` se presente
  (usa `r.get("future", 0)`).

### T1.b — PC: `app/shared/drive_sync.py`
- Alla riga ~326, sostituisci il controllo attuale
  `data.get("schema") != sync.SCHEMA_VERSION` con la regola nuova: **accetta**
  schema ≤ locale, **rifiuta** solo schema più nuovo. Cioè:
  - se `not isinstance(data, dict)` → `errors += 1`, log, `continue` (invariato);
  - se `sync._schema_troppo_nuovo(data.get("schema"))` → NON applicare, `continue`
    (la guardia dentro `apply_snapshot` alzerà comunque il flag; qui NON contarlo
    come `errors`, semmai incrementa un contatore locale se serve alla UI — non
    richiesto). Rimane il caso schema mancante/uguale/minore → `apply_snapshot`.
- Il dict di `_sync_with` e `sync_once` includa `future` (somma dei `future`
  ritornati da `apply_snapshot`).

### T1.c — PWA: `pwa/sync.js`
- In `opsFromSnapshot(snap)`: ogni op ora deve portare lo schema DELLO SNAPSHOT,
  non un `1` fisso: cambia `schema: 1` in `schema: snap.schema || 1` (riga ~98).
- In `applyRemoteOps(ops, localDeviceId)`: nel forEach che riempie `byEntity`,
  scarta le op con schema troppo nuovo e contale. Concretamente, prima di
  `byEntity[e].push(op)`, se `(op.schema || 1) > 1` → incrementa un contatore
  `future` e NON pushare l'op. Al termine, se `future > 0`, chiama
  `DB.setMeta("needs_update", true)`. Il valore di ritorno diventa
  `{ applied, skipped, future }`.
  (Nota: `1` qui è la costante di schema locale della PWA; lasciala hardcoded
  come già è altrove nel file, coerente con lo stile esistente.)

### T1.d — PWA: `pwa/drive.js`
- Riga ~162: cambia `snap.schema !== 1` in `snap.schema > 1` (accetta ≤ 1,
  rifiuta più nuovo). Quando `snap.schema > 1`, prima di `return` chiama
  `DB.setMeta("needs_update", true)`.

### T1.e — Superficie UI del flag "aggiornamento necessario"
- **PC** — `app/shared/settings_routes.py` + `app/templates/settings.html`:
  nella GET `/impostazioni`, leggi `store.get_setting("sync_needs_update", "")`
  e passalo al template come `sync_needs_update` (bool). In `settings.html`,
  dentro la card "Sincronizzazione Google Drive", se `sync_needs_update` mostra
  un avviso `<div class="note warn">{{ t('set.sync_update') }}</div>`. Aggiungi la
  chiave i18n `set.sync_update` in `app/shared/i18n.py` (it + en/es/fr/de/uk),
  testo it: "Un altro dispositivo usa una versione più recente di MyMoney:
  aggiorna quest'app per sincronizzare tutto."
- **PWA** — `pwa/index.html` + `pwa/app.js`: aggiungi in cima a `<main class="wrap">`
  (subito dopo il tag di apertura) un banner
  `<div id="needs-update" class="stale" hidden>Aggiorna MyMoney: un altro
  dispositivo usa una versione più recente.</div>`. In `app.js`, dentro `render()`,
  dopo aver letto i meta, imposta `$("needs-update").hidden` in base a
  `DB.getMeta("needs_update")` (mostralo se true). Riusa/definisci la classe CSS
  `.stale` (vedi T3 per lo stile: una sola classe condivisa).

### Criteri di accettazione T1
- Nuovi test in `app/tests/test_sync.py` (classe `TestSchemaGuard`):
  1. `import_ops` con un'op `schema=2` → `result["future"] == 1`,
     `result["applied"] == 0`, e `settings_store.get_setting("sync_needs_update")`
     == `"1"`. **Nota fixture:** i test patchano `SessionLocal` ovunque; assicurati
     di patchare `settings_store.SessionLocal` come già fa `test_drive_sync.py`,
     così `set_setting` scrive nel DB di test (altrimenti il test tocca il DB reale
     → vietato).
  2. `import_ops` con op `schema=1` e op `schema=2` miste → applica solo la 1,
     `future == 1`.
  3. `apply_snapshot` con `data["schema"]=2` → `applied == 0`, `future == 1`,
     nessun record scritto.
  4. Op senza campo `schema` → trattata come schema 1 (applicata).
- `pytest app/tests/` resta **tutto verde** (i 36 esistenti + i nuovi).
- Verifica manuale JS (descrivi nel report, non serve harness): con uno snapshot
  `{schema:2,...}` la PWA non applica nulla e `needs_update` diventa true.

**Vincoli T1:** non cambiare il valore di `SCHEMA_VERSION` (resta 1). Non toccare
la logica di merge `_wins`. Non introdurre errori dove prima non ce n'erano
(schema uguale/minore/assente = comportamento identico a oggi).

---

## T2 — Test multi-dispositivo simulato

**Obiettivo.** Un test automatico che simula **due dispositivi indipendenti**
(due DB SQLite separati + due `device_id`) che si scambiano lo stato tramite un
**finto-Drive condiviso**, e dimostra i criteri di fine del PIANO-V2 §Fase 4:
X+Y convergono, e un conflitto sullo stesso record si risolve con LWW senza
duplicati.

**File nuovo:** `app/tests/test_multidevice.py`.

**Come simulare due device nello stesso processo.** I moduli `sync`/`drive_sync`
usano stato globale (`SessionLocal`, `get_device_id`, `SYNC_DIR`). Per "essere"
un device alla volta, usa un context manager che ri-patcha quei globali:

```python
from contextlib import contextmanager
from unittest.mock import patch

@contextmanager
def come_device(engine_session, device_id, sync_dir):
    """Esegue il blocco come se fossimo QUEL dispositivo."""
    with patch.object(sync_mod, "SessionLocal", engine_session), \
         patch.object(store_mod, "SessionLocal", engine_session), \
         patch.object(sync_mod, "get_device_id", lambda: device_id), \
         patch.object(sync_mod, "SYNC_DIR", sync_dir):
        yield
```

- Crea **due** engine/Session distinti (due file .db in `tmp_path`), due
  `sync_dir` distinti, `device_id` `"pc_uno"` e `"pwa_due"`.
- **FakeDrive condiviso**: riusa la classe `FakeDrive` di `test_drive_sync.py`
  (copiala nel nuovo file oppure importala — scegli l'import se `FakeDrive` è
  importabile senza effetti collaterali; altrimenti duplicala, è piccola). La
  STESSA istanza di `FakeDrive` viene passata a `sync_once` di entrambi i device
  → è il "corriere" comune.

**Casi da coprire (acceptance):**
1. **X+Y.** Dentro `come_device(uno)`: crea wallet W1 + transazione X. Dentro
   `come_device(due)`: crea wallet W2 + transazione Y. Poi:
   `with come_device(uno): drive_mod.sync_once(client=fake)` →
   `with come_device(due): drive_mod.sync_once(client=fake)` →
   `with come_device(uno): drive_mod.sync_once(client=fake)`.
   Verifica: **entrambi** i DB contengono X e Y (query per uid), e il
   **saldo totale** calcolato è identico al centesimo sui due lati
   (usa `finance.service`/`sync.build_snapshot` per ricavarlo in modo coerente).
2. **Conflitto LWW.** Entrambi i device modificano lo STESSO record (stesso uid)
   con `rev` diversi, poi sincronizzano a giro completo. Verifica: vince il `rev`
   più alto su **entrambi**, e non esistono due righe con lo stesso uid (nessun
   duplicato).
3. **Tombstone.** `uno` cancella (soft-delete) un record e sincronizza; dopo il
   giro completo, su `due` quel record risulta `deleted=True`.

### Criteri di accettazione T2
- `pytest app/tests/test_multidevice.py` verde, ≥ 3 test (i casi sopra).
- I test NON toccano il DB reale né `app/data/` (tutto in `tmp_path`).
- Deterministici (nessuna dipendenza da orologio reale per l'esito del merge:
  imposta `rev`/`updated_at` espliciti dove serve).

**Vincoli T2:** non modificare `sync.py`/`drive_sync.py` per far passare il test
(se un caso non passa per un bug del motore, **fermati e segnalalo** in fase di
consegna: lo valuta la review). Solo codice di test in questo task.

---

## T3 — Indicatore "ultima sync" + avviso se vecchia

**Obiettivo.** Rendere visibile quando è avvenuta l'ultima sincronizzazione e
avvisare se è **troppo vecchia** (rischio di lavorare su dati non aggiornati).

### T3.a — PWA
- **`pwa/styles.css`**: definisci una classe `.stale` (banner d'avviso sobrio,
  coerente con la palette: sfondo tenue `var(--accent-soft)` o simile, testo
  `var(--ink)`, bordo `var(--border)`, `border-radius: var(--r-md)`, padding
  ~10px 12px, `font-size:13px`, `margin: 12px 0`). Una sola classe, riusata anche
  dal banner `#needs-update` di T1.
- **`pwa/index.html`**: subito dopo l'apertura di `<main class="wrap">` aggiungi
  `<div id="stale" class="stale" hidden></div>` (oltre a `#needs-update` di T1).
- **`pwa/app.js`**, dentro `render()` (dove già si legge `last_sync` e si scrive
  `#sync-info`): calcola i giorni dall'ultima sync. Se non c'è mai stata sync, o
  se sono passati **> 7 giorni**, mostra `#stale` con il testo:
  "Ultima sincronizzazione oltre una settimana fa — tocca 🔄 o ☁️ per aggiornare"
  (se mai sincronizzato: "Non hai ancora sincronizzato questo telefono").
  Altrimenti `#stale` resta `hidden`. Non cambiare il comportamento di
  `#sync-info` nel footer (resta com'è).

### T3.b — PC (piccolo)
- L'esito dell'ultima sync è già mostrato in `settings.html` (`drive_last`).
  Aggiungi solo: se l'ultima sync ha più di 7 giorni (dal campo `ts`), mostra
  accanto un `<span class="muted">` con "(più di una settimana fa)". Calcolo in
  `settings_routes.py` (passa un bool `drive_last_stale`). Se è complicato parsare
  `ts`, **fermati e segnala**: è un dettaglio, non bloccare il resto.

### Criteri di accettazione T3
- Verifica in browser (viewport mobile, `/pwa/`): con `last_sync` assente → banner
  "non hai ancora sincronizzato"; con `last_sync` = 10 giorni fa (impostabile da
  console: `DB.setMeta('last_sync', <iso vecchia>)` poi `location.reload()`) →
  banner "oltre una settimana fa"; con `last_sync` = ora → nessun banner.
  Documenta l'esito nel report (niente overflow orizzontale, testo leggibile).
- Nessuna regressione: `#sync-info` continua a funzionare come prima.

**Vincoli T3:** solo CSS/HTML/JS della PWA e il piccolo calcolo lato PC. Non
toccare il motore di sync.

---

## T4 — Messaggi d'errore Drive comprensibili (quota / token)

**Obiettivo.** Oggi un errore Drive diventa un generico "sync_err". Distinguere
almeno **quota piena** e **token scaduto/revocato** con un messaggio chiaro.

### T4.a — PC: `app/shared/drive_sync.py`
- In `DriveClient._req`, quando `status >= 400` (e non 401), leggi il corpo e, se
  contiene un indizio di quota (sottostringa `storageQuota` o `quotaExceeded` nel
  JSON d'errore di Google), solleva `DriveError("quota")`; altrimenti l'attuale
  `DriveError(f"http_{status}")`. Non loggare il corpo intero (può contenere
  dettagli): logga solo `status` e l'etichetta (`quota`/`http_NNN`).
- In `sync_once`, mappa: `DriveError` il cui messaggio è `"quota"` →
  `{"ok": False, "error": "quota", ...}`; resto invariato (`"drive"`).
- `settings_routes.py` (`drive_sync_now`): se `result["error"] == "quota"` →
  redirect `?drive=quota`; se `"auth"` → `?drive=auth` (già c'è). `settings.html`:
  aggiungi il ramo `{% elif drive_msg == 'quota' %}` con `t('set.drive_msg_quota')`.
  i18n `set.drive_msg_quota` (it: "Spazio su Google Drive esaurito: libera spazio
  e riprova.") in tutte le lingue.

### T4.b — PWA: `pwa/drive.js`
- In `api(...)`, quando `!res.ok` e lo status è 403, prova a leggere il messaggio
  e se indica quota lancia `{ code: "quota" }`. In `driveSync().catch`, gestisci
  `err.code === "quota"` con `reason: "quota"`. In `app.js` `doDriveSync`, se
  `r.reason === "quota"` mostra in `#sync-info`: "Drive: spazio esaurito".

### Criteri di accettazione T4
- Test in `test_drive_sync.py`: un `FakeDrive` che nella `list_state_files`
  solleva `DriveError("quota")` → `sync_once` ritorna `{"ok": False,
  "error": "quota"}`. Un `FakeDrive` che solleva `DriveAuthError` → `error=="auth"`
  (già coperto: mantienilo verde).
- `pytest app/tests/` verde.

**Vincoli T4:** non cambiare il flusso OAuth. Mai loggare il corpo delle risposte
Drive (solo status + etichetta).

---

## T5 — Compattazione tombstone vecchissimi (solo transazioni, PC)

**Obiettivo.** Evitare che i tombstone (record `deleted=True`) si accumulino per
sempre. Rimuovere **fisicamente** solo i tombstone di **transazioni** più vecchi
di **365 giorni** (per `updated_at`). Wallet/categorie NON si compattano (sono
pochi e referenziati da FK: rischio non giustificato).

**Perché 365 giorni ed è sicuro per noi.** Con 2 dispositivi che sincronizzano
regolarmente, dopo un anno entrambi hanno già ricevuto la cancellazione: rimuovere
la lapide non la fa "risorgere". Il limite lungo è la garanzia. (Caso teorico di
un dispositivo offline da oltre un anno: fuori dal nostro scenario; documentato.)

### Implementazione — `app/finance/service.py`
- Aggiungi:
  ```python
  def compatta_tombstone(giorni: int = 365) -> int:
      """Elimina fisicamente le TRANSAZIONI tombstone (deleted=True) con
      updated_at più vecchio di `giorni`. Ritorna quante ne ha rimosse.
      Sicuro per 2 dispositivi che sincronizzano entro l'anno (la cancellazione
      è già stata propagata). Non tocca wallet/categorie."""
  ```
  Usa `SessionLocal`, un `datetime.now() - timedelta(days=giorni)` come soglia,
  filtra `Transaction.deleted.is_(True)` e `Transaction.updated_at < soglia`,
  cancella con `db.delete(...)` dentro il context `sync.importing()` (così gli
  hook del diario NON registrano la rimozione fisica), `db.commit()`, ritorna il
  conteggio. **Importante:** avvolgi in `with sync.importing():` per non generare
  nuove op di diario dalla compattazione.
- **Avvio** — `app/main.py`: dopo le altre migrazioni di avvio (dopo
  `fin_service.applica_saldi_iniziali()`), chiama in un `try/except` best-effort
  `fin_service.compatta_tombstone()` (mai far fallire l'avvio; se solleva, log e
  prosegui).

### Criteri di accettazione T5
- Test in `app/tests/test_sync.py` (o `test_edit.py`), classe `TestCompattazione`:
  1. Una transazione `deleted=True` con `updated_at` = 400 giorni fa → dopo
     `compatta_tombstone()` NON esiste più nel DB; il ritorno è ≥ 1.
  2. Una transazione `deleted=True` con `updated_at` = 10 giorni fa → **sopravvive**.
  3. Una transazione viva (`deleted=False`) vecchia di 400 giorni → **sopravvive**
     (si compattano solo i tombstone).
  4. La compattazione NON scrive nel diario (conta le righe del diario prima/dopo:
     invariate) — perché gira dentro `sync.importing()`.
- `pytest app/tests/` verde.

**Vincoli T5:** SOLO transazioni. Mai cancellare wallet o categorie. Mai eseguire
la compattazione fuori da `sync.importing()`. Soglia default 365 giorni.

---

## Fuori scope (non in questa fase)
- **PIN locale della PWA**: rimane traccia opzionale **v2.3** (feature di prodotto,
  non robustezza del sync). Non implementarlo qui.
- Bump effettivo a `schema: 2`: si farà quando ci sarà un vero cambio di formato;
  qui costruiamo solo la guardia (T1).
- Fase 6 (dashboard sola-lettura sul telefono): saltata per scelta dell'utente.

## Ordine consigliato e dipendenze
1. **T1** (guardia schema) — indipendente. Introduce la classe CSS `.stale`
   condivisa con T3 (coordina: definiscila una volta sola in `styles.css`).
2. **T4** (messaggi Drive) — indipendente, piccolo.
3. **T5** (compattazione) — indipendente.
4. **T3** (indicatore ultima sync) — usa la classe `.stale` di T1.
5. **T2** (test multi-dispositivo) — per ultimo: è la verifica d'insieme; se
   scopre un difetto del motore, **non aggiustare il motore di nascosto**: segnalalo.

## Definizione di "fatto" per l'intera fase
- `pytest app/tests/` **completamente verde** (36 esistenti + tutti i nuovi).
- Nessuna nuova dipendenza in `app/requirements.txt`.
- Nessun segreto/dato finanziario nei log.
- Comportamento invariato quando le nuove condizioni non scattano.
- `docs/SYNC-PROTOCOL.md` aggiornato: nota su compatibilità di versione (§8 o nuova
  §10) e su compattazione tombstone. `PIANO-V2.md`: spunta Fase 7 quando chiusa.
- Consegna alla review (Claude Code): elenco dei file toccati + esito `pytest`.
