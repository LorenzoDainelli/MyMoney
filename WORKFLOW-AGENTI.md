# WORKFLOW AGENTI — Protocollo di collaborazione

> Documento vincolante per **tutti** gli agenti che lavorano su questa repo
> (Claude Code e Google Antigravity / Gemini). Leggilo prima di iniziare
> qualsiasi task. Se un'istruzione dell'utente contraddice questo file,
> chiedi conferma prima di procedere.

---

## 0. Perché esiste questo file

Questo progetto è costruito interamente con agenti AI. Per tenere il codice
coerente e i costi sotto controllo, i ruoli sono **separati e non
intercambiabili**. Ogni agente sa cosa può fare, cosa non deve fare, e chi ha
l'ultima parola.

Regola d'oro, una frase: **Claude Code pensa e decide, Antigravity esegue,
Claude Code verifica.**

---

## 1. Ruoli

| Agente | Ruolo | Cosa fa |
|---|---|---|
| **Claude Code** (Opus 4.8 / Fable 5) | 🧠 **Cervello + Reviewer** | Architettura, decisioni di design, scrittura del piano, review finale, correzioni |
| **Antigravity** (Gemini 3.5 Flash / 3.1 Pro) | 🔧 **Esecutore meccanico** | Implementa esattamente ciò che il piano descrive, niente decisioni autonome di architettura |
| **Claude Code** (in seconda battuta) | ✅ **Autorità di review** | Verifica il lavoro dell'esecutore e ha potere di riscrivere |

L'esecuzione meccanica può essere fatta **anche da Claude Code** quando è
comodo. Ma la **review** è sempre e solo di Claude Code.

---

## 2. Il flusso a fasi

### Fase 1 — Pianificazione → **Claude Code (cervello)**
- Decide architettura e approccio.
- Scrive un **piano-contratto** dettagliato (vedi §4) in un file dedicato
  dentro `docs/`, es. `docs/PIANO-CLOUD.md`. (I vecchi `PIANO-FASE-N.md` nella
  radice sono stati rimossi il 18/08/2026: i piani stanno in `docs/`.)
- Il piano deve essere **autosufficiente**: l'esecutore non vede il
  ragionamento che c'è dietro, vede solo il documento. Tutto ciò che serve
  deve stare scritto lì.

### Fase 2 — Esecuzione meccanica → **Antigravity (Gemini)** *oppure* Claude Code
- Riceve il piano-contratto **incollato nel prompt** (non basta linkare la
  repo).
- Implementa **solo** ciò che il piano dice, rispettando le convenzioni di §5.
- **Non** prende decisioni di architettura. Se il piano è ambiguo o manca
  qualcosa, **si ferma e segnala** invece di improvvisare.
- Default: **Gemini 3.5 Flash**. Sale a **3.1 Pro** solo se un task fallisce
  due volte o attraversa più di 3 file.

### Fase 3 — Review → **Claude Code (autorità)**
- Riceve il **diff** o l'elenco dei file toccati (non l'intera repo).
- Verifica contro il piano-contratto originale.
- **Ha piena autorità di riscrivere.** Se il codice prodotto dall'esecutore
  non rispetta il piano, non segue lo stile del progetto, o semplicemente
  "non è come l'avrebbe fatto Claude Code" → **lo cambia**. Non è un
  compromesso, è la regola.

---

## 3. Autorità di review (la regola che conta)

> **Se l'esecuzione è stata fatta da Antigravity/Gemini, Claude Code in fase
> di review può cambiare qualsiasi cosa** — struttura, naming, stile,
> pattern — **se non è conforme al piano o al proprio standard.**

Questo non è un giudizio sulla qualità di Gemini: è il modo in cui teniamo il
progetto coerente con un solo "gusto" architetturale. L'esecutore produce, il
reviewer garantisce l'uniformità. In caso di dubbio, vince lo stile di Claude
Code.

L'esecutore **non deve** difendere le proprie scelte contro la review: se
Claude Code riscrive, la versione di Claude Code è quella buona.

---

## 4. Formato del piano-contratto (Fase 1)

Un buon piano-contratto minimizza le ambiguità per l'esecutore. Deve
contenere, per ogni task:

- **Task atomico**: una cosa sola, chiaramente delimitata.
- **File esatti** da creare/modificare (percorso completo).
- **Criteri di accettazione espliciti**: come si capisce che è fatto bene
  (es. "il test `test_sync.py` passa 13/13", "l'endpoint risponde 200 con
  JSON `{...}`").
- **Vincoli**: cosa NON toccare, cosa NON cambiare.
- **Dipendenze**: quale task deve venire prima.

Se un criterio di accettazione non è verificabile, il task non è pronto per
essere passato all'esecutore.

---

## 5. Convenzioni di stile del progetto

> Da rispettare da **tutti** gli agenti, così la review di Fase 3 ha poco da
> correggere. Mantieni questa sezione aggiornata quando emergono nuove
> convenzioni.

**Architettura "anti-costo"** — principio guida del progetto:
- Il modello AI emette **solo JSON di analisi**. Gli **script** fanno tutto il
  lavoro meccanico (fetch, dedup, render, invio, merge dello stato).
- Non spostare logica dagli script al modello. Se una cosa può farla uno
  script deterministico, la fa lo script.

**Convenzioni di codice** *(da completare/adattare al progetto reale):*
- Linguaggio: Python per script e backend.
- Dedup: chiave deterministica dall'URL, mai fidarsi degli id del modello.
- Naming file/funzioni: coerente con quelli esistenti nella repo.
- Struttura cartelle: rispettare quella esistente (`app/`, `scripts/`,
  `config/`, `pwa/`, ...). Non introdurre nuove cartelle senza che sia nel
  piano.
- Config in YAML (`portfolio.yaml`, `settings.yaml`), non hardcodare valori
  che appartengono alla config.
- Test: ogni componente nuovo ha o aggiorna i suoi test; devono passare prima
  di considerare un task chiuso.

---

## 6. Note operative

- **Antigravity** ha limiti di rate ogni ~5 ore. Per fasi 2 lunghe, spezza il
  lavoro in blocchi.
- **Gemini non ha memoria** del ragionamento di Fase 1: tutto il contesto
  necessario va nel piano-contratto o nel prompt.
- **La review conviene farla nella stessa sessione** di Claude Code che ha
  scritto il piano, così ha già "in testa" il contratto.
- Le label dei modelli nel dropdown di Antigravity non sono sempre affidabili:
  se un output è sotto le aspettative, non dare per scontato quale modello
  l'ha prodotto.

---

*Ultimo aggiornamento: mantenere questo file come fonte di verità del
workflow. Ogni agente che lo legge accetta implicitamente i ruoli sopra.*
