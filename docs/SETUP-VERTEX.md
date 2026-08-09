# Usare Vertex AI per l'agente (consumare i crediti Google Cloud)

L'agente AI dell'app usa Gemini. Di default passa da **Google AI Studio** (chiave
API, piano gratuito). In alternativa puoi farlo passare da **Vertex AI**, il
servizio Gemini dentro Google Cloud: così le richieste vengono fatturate al tuo
progetto Cloud e **consumano i crediti** (i 258 € del trial), invece di restare
sul piano gratuito.

> **Quando conviene?** Solo se vuoi usare l'agente in modo più intensivo e/o hai
> crediti che altrimenti scadrebbero inutilizzati. I crediti scadono (per te: **28
> settembre 2026**); dopo, Vertex è a pagamento (non ha un piano gratuito perpetuo
> come AI Studio). Puoi tornare a "Studio" in qualsiasi momento dal menù Provider,
> senza perdere nulla.

La privacy non cambia: all'AI non vengono mai inviati ISIN, importi, quantità,
valori o nomi — il filtro `privacy.scrub_text` vale identico su entrambi i provider.

---

## Cosa ti serve (una volta sola)

Serve un **service account** del progetto Cloud e la sua **chiave JSON**. Sono
azioni da fare tu nella Console Google Cloud (io non posso creare credenziali).

### 1. Abilita l'API Vertex AI
1. Vai su <https://console.cloud.google.com/> e in alto assicurati di essere sul
   progetto giusto (**MyMoney**, id `mymoney-502422`).
2. Apri **API e servizi → Libreria**, cerca **Vertex AI API** e premi **Abilita**.
   (Link diretto: <https://console.cloud.google.com/apis/library/aiplatform.googleapis.com>)

### 2. Crea il service account
1. **IAM e amministrazione → Account di servizio → + Crea account di servizio**.
2. Nome: es. `mymoney-agente`. Premi **Crea e continua**.
3. Ruolo: **Vertex AI User** (`roles/aiplatform.user`). Aggiungilo e premi **Fine**.

### 3. Scarica la chiave JSON
1. Apri il service account appena creato → scheda **Chiavi**.
2. **Aggiungi chiave → Crea nuova chiave → JSON → Crea**.
3. Il browser scarica un file `.json`. **È un segreto**: tienilo al sicuro, non
   mandarlo a nessuno, non metterlo in cartelle sincronizzate pubblicamente.

---

## Collega l'agente a Vertex (nell'app)

1. Se non l'hai già fatto, installa le librerie necessarie (una volta sola). Dal
   `cmd`, nella cartella del progetto:
   ```
   app\.venv\Scripts\python -m pip install -r app\requirements.txt
   ```
   Poi **riavvia l'app** (`Avvia-Finanza-Locale.bat`). Questa pagina riguarda la
   copia locale: sul cloud la chiave dell'agente arriva dal Secret Manager
   (`MYMONEY_GEMINI_API_KEY`), non dalle Impostazioni.
2. Nell'app apri **Impostazioni → Agente AI** e nel menù **Provider** scegli
   **Vertex AI (Google Cloud · usa i crediti)**. La pagina si ricarica.
3. Compila:
   - **ID progetto Google Cloud**: `mymoney-502422`.
   - **Regione**: lascia `global` (va bene per Gemini). In alternativa una regione,
     es. `europe-west1`.
   - **Service account (chiave JSON)**: apri il file `.json` scaricato con un
     editor di testo, copia **tutto** il contenuto e incollalo nella casella.
4. Premi **Salva agente**, poi **Prova connessione**: se è tutto ok vedrai
   "✓ Connessione riuscita".

Da qui in poi ogni funzione dell'agente (analisi, "il punto della settimana",
inserimento movimenti in linguaggio naturale) passa da Vertex e consuma i crediti.

---

## Tornare al piano gratuito
Impostazioni → Agente AI → **Provider → Google AI Studio**. La chiave Gemini che
avevi resta salvata: riparte tutto come prima, gratis.

## Se qualcosa non va
- **"Manca la libreria google-auth"**: non hai installato le dipendenze — vedi il
  passo 1 qui sopra, poi riavvia l'app.
- **"Chiave non valida" / errore 401-403**: il service account non ha il ruolo
  *Vertex AI User*, oppure l'API Vertex non è abilitata, oppure hai incollato un
  JSON incompleto. Ricontrolla i passi 1-3.
- **Modello non trovato (404)**: su Vertex i modelli **2.0 non esistono** (danno 404
  in ogni regione): lì serve il **2.5**. L'app lo sa e usa `gemini-2.5-flash` di
  default quando il provider è Vertex — ogni provider ricorda il proprio modello,
  quindi passare da Studio a Vertex non trascina un nome che di là non esiste. Se
  hai scritto a mano un modello inesistente, l'app ripiega comunque una volta sul
  default del provider. Verificato sul progetto reale: `gemini-2.5-flash`,
  `gemini-2.5-flash-lite` e `gemini-2.5-pro` rispondono su `global`, `us-central1`,
  `europe-west1`, `europe-west4`.

## Controllare la spesa
In Console Cloud: **Fatturazione → Report** (filtra per servizio *Vertex AI*) e
**Fatturazione → Crediti** per vedere quanti dei 258 € stai usando.
