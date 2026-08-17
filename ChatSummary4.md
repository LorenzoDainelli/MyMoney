# ChatSummary4 — I test non dipendono più dall'ordine di esecuzione

> Riassunto completo di una sessione di lavoro sulla **suite di test dell'app finanza
> personale** (`app/tests/`) dentro il repo del news-monitor. Contiene: il problema
> riportato, la riproduzione, la diagnosi (più ampia del sintomo iniziale), le direzioni
> valutate e perché ne è stata scelta una, il codice scritto, la verifica, e una scoperta
> collaterale che riguarda **dati veri** e che resta aperta.
> Per il contesto generale del progetto vedi **`CONTESTO-PROGETTO.md`**.

---

## 0. In due righe

Alcuni file di test passavano solo dentro la suite intera e fallivano da soli. La radice era
una sola — ogni modulo fa `from shared.db import SessionLocal` e **fotografa** quel valore al
momento dell'import — ma produceva **tre** sintomi diversi, e in una copia pulita del repo
faceva fallire la suite intera. Risolto con un unico file nuovo, `app/tests/conftest.py`,
**senza toccare il codice di produzione**. Commit su `main`, spinto su GitHub.
Resta aperta una questione: nel diario del sync ci sono **dati finti dei test**.

---

## 1. Il punto di partenza (quello che ha riportato l'utente)

Progetto `app/` (FastAPI + SQLAlchemy, test in `app/tests`). Alcuni file di test passano solo
quando si lancia l'intera suite e falliscono se eseguiti da soli.

**Riproduzione fornita** — da `app/`:

```bash
C:/Users/loren/Desktop/Claude/Report/app/.venv/Scripts/python.exe -m pytest tests/test_versamenti.py -q
```

→ fallimenti con `sqlalchemy.exc.OperationalError: no such table: finance_wallets`.
Lanciando invece `pytest tests -q` passavano tutti.

**Causa già individuata dall'utente:** `finance/service.py` fa
`from shared.db import SessionLocal` a livello di modulo. Le fixture fanno
`monkeypatch.setattr(shared.db, "SessionLocal", TestSession)`. Se `finance.service` viene
importato per la **prima volta durante il test** (import pigro dentro
`portfolio/versamenti.py::_sync_finanze`), cattura la sessione di test, il cui DB temporaneo
non contiene le tabelle `finance_*` perché `test_versamenti.py` non importa mai
`finance.models` e quindi `Base.metadata.create_all` non le crea.

**Direzioni proposte (non prescrittive):** importare `finance.models` in `conftest.py`;
oppure monkeypatchare anche `finance.service.SessionLocal`; oppure smettere di legare
`SessionLocal` al momento dell'import. **Preferenza esplicita: la soluzione che non cambia
il codice di produzione, se regge.**

**Criterio di accettazione:** ogni file di `tests/` deve passare sia da solo sia dentro la
suite completa.

---

## 2. Riproduzione e verifica della diagnosi

Il lavoro è stato fatto nel **worktree** `.claude/worktrees/magical-joliot-6e0249`, sul branch
`claude/gracious-ellis-bc2f18` (allineato a `main`, `040c1e0`).

Il repro è stato confermato subito (5 fallimenti + 2 passati nel worktree; l'utente ne
riportava 6 — differenza irrilevante, stessa causa).

Poi, invece di fermarsi lì, ho lanciato **ogni file singolarmente**. Risultato nel worktree
(copia pulita, `app/data/finanza.db` vuoto):

| File | Da solo, PRIMA |
|---|---|
| `test_ai_memory.py` | 3 failed, 9 passed |
| `test_ai_tools.py` | 7 failed, 11 passed |
| `test_calendario.py` | 8 errors |
| `test_destinazioni.py` | 9 errors |
| `test_insights.py` | 10 failed, 20 passed |
| `test_pac_finanze.py` | 6 errors |
| `test_versamenti.py` | 5 failed, 2 passed |
| `test_wealth.py` | 1 failed, 8 passed |
| *(gli altri 11 file)* | verdi |

**8 file su 19**, non uno. E la suite intera nel worktree pulito, senza correzione, faceva
**26 failed + 23 errors** — non i 225 verdi del PC dell'utente.

---

## 3. Il quadro reale: tre sintomi, una radice

La radice è quella individuata dall'utente — `from shared.db import SessionLocal` fotografa un
valore all'import — ma si manifesta in tre modi.

### 3.1 Tabelle mancanti (il sintomo segnalato)

`Base.metadata` conosce **solo i modelli già importati**. Un file che non importa
`finance.models` chiama `Base.metadata.create_all(engine)` su un database temporaneo che
nasce **senza** le tabelle `finance_*`. Basta che il codice sotto test ci arrivi — l'import
pigro dentro `portfolio/versamenti.py::_sync_finanze` — per ottenere
`no such table: finance_wallets`. Nella suite intera un altro file aveva già fatto quell'import,
e il guaio spariva.

### 3.2 I test parlavano col database VERO

Questo **non** era stato notato prima ed era il problema più grave.

Le fixture sostituivano `SessionLocal` **solo nei moduli elencati a mano**, quelli che di volta
in volta ci si ricordava di scrivere. Tutti gli altri — `shared.settings_store`, `shared.sync`,
`finance.service`, `shared.ai_memory`, `shared.storico`, `portfolio.market`… — continuavano a
puntare a `app/data/finanza.db`, **il database reale dell'utente**.

Sei file passavano *solo perché quel database esiste su questo PC*, con dentro le tabelle
giuste. Esempi concreti:

- `test_calendario.py` importa `shared.settings_store` a livello di modulo ma patcha solo
  `shared.db` e `finance.service`: la lettura di `sync_device_id` finiva sul DB vero;
- `test_wealth.py` è un test di logica pura, ma `W._griglia_piatta` chiama
  `fin_service.data_inizio()` → `finance/service.py:741` → `SELECT min(finance_transactions.data)`
  sul DB vero;
- `test_ai_tools.py` e `test_insights.py` chiamano `ai._genera(...)` che legge impostazioni e
  storico dal DB vero.

Nel worktree, dove `data/finanza.db` è un file da 0 byte, tutto questo esplodeva. Sul PC
dell'utente passava in silenzio.

### 3.3 `RuntimeError: deque mutated during iteration`

Due test fallivano con questo errore, apparentemente scollegato. È lo stesso import pigro:
`finance/models.py:147` registra un listener `@event.listens_for(Session, "before_flush")` a
**livello di modulo**. Importare `finance.models` *durante* un flush significa aggiungere un
listener mentre SQLAlchemy sta scorrendo la lista dei listener. Da cui l'errore.

---

## 4. Le direzioni valutate e la decisione

Le tre direzioni proposte dall'utente, valutate rispetto al quadro completo:

| Direzione | Verdetto |
|---|---|
| Importare `finance.models` in `conftest.py` | **Necessaria ma non sufficiente**: sistema il §3.1 e il §3.3, non il §3.2 (i moduli non patchati). |
| Patchare anche `finance.service.SessionLocal` nelle fixture | **Non regge**: è la stessa lista a mano che ha causato il problema. Ogni nuovo test o nuovo import ricrea il buco. |
| Non legare `SessionLocal` all'import (accedervi come `shared.db.SessionLocal`) | **La più solida**, ma nella forma "cambio i moduli di produzione" viola la preferenza dell'utente. |

**Decisione:** prendere la terza — la sostanza giusta — ma applicarla **dal lato dei test**.
Il `conftest.py` sostituisce l'attributo `SessionLocal` dei moduli con un piccolo oggetto che
rimanda a `shared.db.SessionLocal` **risolta a ogni chiamata**. Il codice di produzione resta
esattamente com'è; sono i test a rendere dinamico quel legame per la durata della loro
esecuzione.

In più, la scelta di dare a **ogni** test un database temporaneo — anche a quelli che col
database non c'entrano nulla — perché sono proprio quelli che ci finivano di straforo.

---

## 5. Cosa ho scritto

Un solo file nuovo: **`app/tests/conftest.py`** (117 righe, di cui una buona metà di commenti
che spiegano *perché* esiste). Zero modifiche al codice di produzione. Zero modifiche ai file
di test esistenti.

### 5.1 Import eager

```python
import finance.models          # tabelle finance_*
import finance.service
import portfolio.market
import portfolio.models        # tabelle portfolio_*
import portfolio.routes
import portfolio.seed
import portfolio.service
import portfolio.versamenti
import shared.ai_memory
import shared.settings_store
import shared.storico
import shared.sync
```

Tutti i moduli che definiscono tabelle o che catturano `SessionLocal`. Effetti:
`Base.metadata` è **sempre** completa (11 tabelle) e **nessun import resta pigro** — il che
elimina anche il problema del listener registrato a metà flush (§3.3).

`main.py` è deliberatamente **escluso**: all'import fa `create_all` sul database vero.

### 5.2 Il rimando dinamico

```python
class _SessioneCorrente:
    def __call__(self, *args, **kwargs):
        return db_mod.SessionLocal(*args, **kwargs)
```

Installato una volta sola, all'import del conftest, in tutti i moduli che avevano fotografato
`SessionLocal`. Da quel momento **patchare la sola `shared.db.SessionLocal` sposta tutta l'app**.
Le fixture esistenti dei singoli file continuano a funzionare senza modifiche: quando
sostituiscono `shared.db.SessionLocal` con la propria sessione, il rimando le segue.

(Controllo fatto prima di scrivere: in tutto il codice di produzione `SessionLocal` è usata
**solo** come `SessionLocal()`, mai per altri attributi del `sessionmaker`. Il rimando basta.)

### 5.3 La fixture autouse

```python
@pytest.fixture(autouse=True)
def database_usa_e_getta(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'conftest.db'}", ...)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal", sessionmaker(bind=engine, ...))
    for mod in _MODULI_CON_ENGINE:                       # finance.service, portfolio.seed
        monkeypatch.setattr(mod, "engine", engine)
    monkeypatch.setattr(shared.sync, "SYNC_DIR", tmp_path / "sync")
    yield
    engine.dispose()
```

Tre dettagli non ovvi:

- **`engine`**, non solo `SessionLocal`: `finance/service.py` e `portfolio/seed.py` lo usano per
  le migrazioni in SQL grezzo (`migra_schema`). Nessun test le chiama oggi, ma senza questa
  riga punterebbero al database vero.
- **`SYNC_DIR`** nel temporaneo: il diario del sync scrive **su file a ogni commit**. Alcuni
  file di test lo patchavano già a mano (`test_sync`, `test_edit`, `test_drive_sync`,
  `test_multidevice`), altri no. Vedi §7.
- **Ordine delle fixture**: quella del conftest gira **prima** di quelle definite nei singoli
  file (pytest ordina le autouse dello stesso scope partendo dal conftest), quindi le fixture
  dei file sovrascrivono con la loro sessione e vincono. Verificato sul campo.

### 5.4 Cosa NON ho toccato

I `monkeypatch.setattr(mod, "SessionLocal", TestSession)` sparsi nei file di test sono ora
**ridondanti ma innocui**: li ho lasciati per tenere il diff minimo e non riscrivere 19 file.

---

## 6. Verifica

| Prova | Esito |
|---|---|
| Ogni file da solo (19 file), nel worktree pulito | **tutti verdi** |
| Suite intera, worktree | **225 passed** |
| Suite in **ordine inverso** dei file | **225 passed** |
| Coppie che prima interagivano (`versamenti`+`pac_finanze`, nei due ordini; `multidevice`+`sync`; `calendario`+`ai_memory`+`wealth`) | **tutte verdi** |
| Repro esatto dell'utente, repo principale | **7 passed** |
| Ogni file da solo (19 file), repo principale | **tutti verdi** |
| Suite intera, repo principale | **225 passed** |
| `data/finanza.db` e diario del sync prima/dopo la suite | **identici byte per byte** |

**Nota sul numero:** i test raccolti sono **225**, non 231. Erano 225 anche *prima* della
modifica (verificato con `--collect-only` sul repo principale senza il conftest): non è stato
tolto nulla.

**Costo:** la suite passa da ~26,5 s a ~33 s (+25%), cioè ~27 ms per test per creare un SQLite
temporaneo con 11 tabelle. Accettabile.

---

## 7. La scoperta collaterale: dati finti nel diario del sync ⚠️

Durante la verifica è emerso che **il diario del sync conteneva dati dei test**.

Il meccanismo: le fixture creavano wallet e movimenti in un database temporaneo, ma l'hook
`after_commit` di `shared/sync.py` scriveva nel diario **vero**,
`app/data/sync/changes-pc_8b3374353cd2.jsonl`, perché `SYNC_DIR` non era patchato in quei file.

Analisi del file (confronto degli `uid` del diario con quelli presenti nel database reale):

| | |
|---|---|
| Righe totali | 180 |
| Righe **orfane** (uid inesistente nel DB vero) | **132** |
| Blocchi contigui | righe 47-66 (20, run del 27 luglio) e 68-179 (112, del 30 luglio) |
| Contenuto tipico | wallet "Trade Republic" / "PAC investimenti" / "Contanti" duplicati 17-18 volte, categorie "Spesa"/"Regali"/"Trasporti", trasferimenti da 100 € |

Sono le fixture di `test_pac_finanze.py`, `test_calendario.py`, `test_versamenti.py`,
`test_destinazioni.py`. **Parte delle 112 righe di oggi è stata prodotta dalle mie run
diagnostiche di questa sessione**, lanciate nel repo principale prima di scrivere la
correzione.

**Da adesso non succede più** (`SYNC_DIR` finisce nel temporaneo — verificato: dopo la suite
intera diario e `finanza.db` sono invariati). Ma **le righe già scritte restano**, e se il
telefono le scarica si ritrova quei record fasulli.

**Perché non ho ripulito da solo:** il diario è append-only e gli altri dispositivi tengono un
**cursore per numero di riga** (`export_diary(since_line)`). Cancellare righe fa slittare tutto
quello che viene dopo, quindi non è un'operazione da fare di iniziativa. **Questione aperta,
in attesa di decisione dell'utente**; prima di toccare qualcosa va fatta una copia del file.

---

## 8. Stato del codice

| Commit | Contenuto |
|---|---|
| `749b0d6` | *test: ogni file di test passa anche da solo, non solo dentro la suite* — il nuovo `app/tests/conftest.py` |
| `450d079` | *merge: i test non dipendono più dall'ordine di esecuzione* (`--no-ff` del branch su `main`) |
| `95f68f9` | *merge: stato della routine dal cloud* — `origin/main` era avanzato con `92c8b03` (`state/*.json` della routine notizie), integrato senza conflitti |

- Lavorato nel worktree/branch **`claude/gracious-ellis-bc2f18`**, spinto su `origin`.
- **`main` è stato mergiato e pushato su GitHub** (`92c8b03..95f68f9`), come da preferenza
  durevole dell'utente ("dopo ogni modifica push su main, senza chiedere").
- Nessuna modifica al codice di produzione: il diff è **un file nuovo, +117 righe**.

### Inciampo tecnico da ricordare

Il primo tentativo di commit è fallito: ho usato la sintassi here-string di **PowerShell**
(`@'…'@`) dentro il tool **Bash**, che è una shell POSIX. Le due shell convivono in questa
sessione ma vogliono sintassi diverse. Risolto scrivendo il messaggio in un file e usando
`git commit -F`.

---

## 9. Cosa resta aperto

1. **Il diario del sync** (§7): decidere se e come ripulire le 132 righe orfane, tenendo conto
   del cursore degli altri dispositivi. Con copia di sicurezza preventiva.
2. **Facoltativo, pulizia:** i `monkeypatch.setattr(..., "SessionLocal", ...)` nei 12 file di
   test ora sono ridondanti. Si possono togliere per snellire le fixture, ma non è urgente e
   non cambia nulla di funzionale.
3. **Da tenere d'occhio:** se un domani un test dovesse chiamare `migra_schema()` o
   `portfolio/seed.py`, la fixture lo copre già (l'`engine` è quello temporaneo), ma vale la
   pena ricontrollare che la copertura regga.

---

## 10. Promemoria operativi

- Lanciare i test da `app/`:

  ```bash
  C:/Users/loren/Desktop/Claude/Report/app/.venv/Scripts/python.exe -m pytest tests -q
  ```

- Da oggi **un singolo file vale quanto la suite intera**: se `pytest tests/test_x.py -q` è
  verde, quel file è a posto davvero. Prima non era così.
- **I test non toccano più i dati veri.** Se un giorno tornassero a farlo — `finanza.db` che
  cambia data, o righe nuove nel diario dopo una run — è il segnale che qualcosa ha aggirato
  il conftest.
- Il worktree è una **copia pulita** (`app/data/` vuoto): è l'ambiente più severo, ed è quello
  giusto per accorgersi in anticipo di questa classe di problemi.
