# MyMoney — guscio PWA (v2)

> ⚠️ **In pensione.** Dall'8 agosto 2026 il telefono apre la web app sul cloud,
> che ha tutti i dati e tutte le funzioni. Questa PWA tiene una sua copia dentro
> il telefono e si allinea via Drive: usarla adesso vuol dire creare un terzo
> insieme di dati che diverge dagli altri due. Resta qui finché non viene smontata
> (Fase 5 di `docs/PIANO-CLOUD.md`); non aggiungerci nulla.

Questa cartella è l'app installabile sul telefono (Fase 2 del [PIANO-V2](../PIANO-V2.md)).
È **statica**: HTML + CSS + JS + service worker + icone. **Nessun dato** finanziario
vive qui — i dati staranno nel telefono (IndexedDB, Fase 3) e nel tuo Google Drive
(sync, Fase 4).

## Provarla in locale (dal PC)
Con l'app avviata (`Avvia-Finanza.bat`), apri: `http://127.0.0.1:8000/pwa/`
Se il PC è raggiungibile mostra i tuoi portafogli reali; altrimenti lo stato "vuoto".

## Pubblicarla su Cloudflare Pages (gratis) e installarla su iPhone

1. Vai su **dash.cloudflare.com** → crea un account gratuito (o accedi).
2. Menu **Workers & Pages** → **Create** → **Pages** → **Connect to Git**.
3. Autorizza GitHub e scegli il repo **news-monitor** (privato: va bene).
4. Impostazioni build:
   - **Framework preset**: *None*
   - **Build command**: *(lascia vuoto)*
   - **Build output directory**: `pwa`
5. **Save and Deploy**. Dopo ~1 minuto avrai un indirizzo tipo
   `https://news-monitor-xxx.pages.dev`.
6. Sull'**iPhone**, apri quell'indirizzo con **Safari** → tocca **Condividi**
   → **Aggiungi alla schermata Home**. Comparirà l'icona MyMoney; si apre a
   schermo intero e funziona anche offline.

> Ad ogni push su GitHub, Cloudflare ripubblica il guscio da solo.

## Sincronizzare col PC via Google Drive (Fase 5)

Il telefono e il PC si parlano attraverso una cartellina **nascosta** del tuo
Google Drive (l'app non vede i tuoi file personali). Serve una configurazione
una-tantum sul tuo account Google: segui **[docs/SETUP-DRIVE.md](../docs/SETUP-DRIVE.md)**.
Poi, nell'app sul telefono: tocca **☁️ Drive** → incolla l'ID client (Web) →
consenso Google → da lì in poi ☁️ Drive sincronizza. Il "pass" di Google dura
~1 ora: quando scade, il tocco successivo ripassa un attimo dal consenso.

## Note tecniche
- Percorsi **relativi**: funziona sia sotto `/pwa/` (in locale) sia alla radice
  (su Cloudflare).
- `sw.js` mette in cache il guscio (apertura offline); l'API `/api/...` passa
  sempre dalla rete.
- Per far leggere alla PWA l'API del PC in LAN si può impostare in console:
  `localStorage.setItem('mm_api_base','http://IP-DEL-PC:8000')` (opzionale).
