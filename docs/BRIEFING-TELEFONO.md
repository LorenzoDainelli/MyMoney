# MyMoney sul telefono — il contratto

> Documento di lavoro, sul modello di `shift-hours/BRIEFING-DESIGN.md`.
> Chi lavora al telefono legge **questo** e guarda la pagina di riferimento
> (`/static/riferimento/index.html`, servita dall'app), e non ha bisogno d'altro.
>
> Data: 15 agosto 2026 · backup della versione precedente: tag `ui-telefono-prima`

---

## 1. Cosa stiamo ridisegnando, e perché

MyMoney **funziona già ed è in uso ogni giorno**, sul PC e sul telefono, con i
dati veri, online. Non c'è niente da costruire e niente da inventare: c'è da
rifare l'aspetto **della sola versione telefono**.

Il verdetto dell'utente, dopo mesi d'uso: *«sembra sempre un sito web e non
un'applicazione, devo scorrere troppo, proporzioni ecc, ed è difficile da
usare»*.

Non è una questione di gusto. È una questione di **impianto**, ed è misurabile.

### La misura, prima di toccare qualcosa

Altezza di ogni schermata a 375 punti di larghezza (iPhone), 15 agosto 2026:

| pagina | altezza | schermate |
|---|---|---|
| Home | 3250px | 4,0 |
| Finanze | 3194px | 3,9 |
| **Portafoglio** | **6209px** | **7,6** |
| **PAC** | **5467px** | **6,7** |
| Impostazioni | 4695px | 5,8 |
| Analisi | 4519px | 5,6 |
| Notizie | 2826px | 3,5 |

### La differenza vera con Shift Hours

Non sono i colori e non sono le spaziature.

| | Shift Hours | MyMoney oggi |
|---|---|---|
| il guscio | `height: 100dvh`, griglia `auto 1fr auto` | nessun guscio |
| che cosa scorre | **una sola area**, in mezzo | **l'intero documento** |
| la barra in basso | una **riga della griglia** | `position: fixed`, galleggia sopra |
| il markup | scritto per il telefono | quello del PC, strizzato da una media query |
| in cima | **un** numero protagonista | tre o quattro riquadri |

L'ultima riga è la radice di tutte le altre. Finché il telefono è «la pagina
del PC con addosso `@media (max-width: 760px)`», ogni blocco pensato per una
finestra da 1280 punti si mette in colonna e allunga la pagina — e nessuna
correzione di spaziature lo può disfare.

**Quello che fa dire «è un'app» è che il contenuto scorre DENTRO una cornice
che non si muove.** Tutto il resto viene dietro.

## 2. Per chi

Una persona sola, il proprietario, che conosce l'app a memoria perché l'ha
fatta costruire riga per riga. Non serve spiegargli cosa sono i dati. Serve
metterglieli davanti in tre secondi, con il pollice di una mano sola, mentre è
in fila alla cassa.

Tre cose da tenere in testa:

- **Deve sembrare un'app, non un sito.** Niente che esca dallo schermo, niente
  da scorrere per sei schermate, niente che assomigli a un modulo da compilare.
- **I numeri sono il contenuto.** Il patrimonio e i saldi sono la cosa che
  guarda per prima e la ragione per cui apre l'app.
- **Se un'interazione richiede una spiegazione, va semplificata, non spiegata.**

## 3. Il perimetro — la regola che non si tocca

### Il PC non cambia di un pixel

È la richiesta esplicita: *«senza toccare in alcun modo quella del PC»*.

Il modo per **garantirlo**, non per sperarlo, è che il telefono viva in file
suoi e in blocchi di markup suoi. Un blocco che esiste solo sul telefono non
può rompere il PC, perché sul PC non c'è.

### File che si possono modificare

```
app/static/telefono/token.css        misure, tocchi, movimento del telefono
app/static/telefono/guscio.css       il contenitore, la barra, i pannelli
app/static/telefono/componenti.css   hero, righe, liste, pillole, stati vuoti
app/static/telefono.css              solo il punto d'ingresso (@import)
app/templates/*.html                 SOLO dentro i blocchi marcati telefono
app/static/riferimento/*.html        la pagina di approvazione
```

### File che NON si toccano mai

```
app/static/styles.css        design freeze v1.0
app/static/mymoney.css       design freeze v1.0
app/static/tokens/*.css      design freeze v1.0
```

Sono copiati **verbatim** dall'handoff e si sostituiscono in blocco quando il
design cambia. Scriverci dentro roba nostra vuol dire perderla al primo
aggiornamento — silenziosamente, che è il modo peggiore.

Se serve un valore che non c'è, si aggiunge un token **in `telefono/token.css`**.

### Il markup a doppio binario

Due etichette, che esistono già:

- `.tel-solo` — si vede solo sul telefono
- `.pc-solo` — si vede solo sullo schermo grande

Quando la versione telefono di una cosa non è «la stessa cosa più stretta» ma
**un'altra cosa** — otto conti in riga invece di otto card, la lista per giorno
invece della tabella — si scrivono due blocchi. **Meglio due blocchi onesti che
un blocco solo torturato dal CSS.**

### Le classi che devono continuare a esistere

Il JavaScript le cerca per nome. Rinominarne una non si scopre guardando la
pagina: si spegne un pezzo di app in silenzio.

```
tel-tabbar tel-tab tel-fab tel-velo tel-foglio tel-presa tel-voce
tel-home tel-home-h tel-conti tel-conto tel-conto-n tel-conto-s
tel-mov tel-mov-d tel-mov-q tel-mov-i tel-giorno tel-giorno-h
tel-giorno-d tel-giorno-t tel-altri tel-modulo tel-effetto
tel-solo pc-solo tel-via mm-drawer mm-nuovo
```

Se ne possono **aggiungere**. Non se ne possono rinominare o eliminare senza
aver prima cercato chi le usa in `app/static/*.js` e in `app/templates/`.

## 4. Regole che sembrano estetiche e non lo sono

Se un giorno questi fogli si riscrivono da zero, **queste vanno riportate**.
Sono correzioni di bug reali, già pagate una volta.

| regola | dove | cosa succede senza |
|---|---|---|
| `font-size: 16px` sui campi | input, select, textarea | iPhone **ingrandisce la pagina** al tocco e te la lascia ingrandita |
| `height: 100dvh` (**non** `min-height`) | `.tel-app` | la barra in basso finisce fuori schermo |
| `min-height: 0` | `.tel-app__corpo` | il contenuto spinge la barra fuori dalla griglia |
| `-webkit-tap-highlight-color: transparent` | ovunque | rettangolo grigio a ogni tocco lento |
| `overscroll-behavior: contain` | corpo e pannelli | il rimbalzo scopre lo sfondo del browser |
| `touch-action: manipulation` | tutto ciò che si preme | 300ms d'attesa prima di ogni tocco |
| `env(safe-area-inset-*)` | testata e barra | titolo sotto il notch, barra sotto l'indicatore |
| `minmax(0, 1fr)` (**non** `1fr`) | ogni griglia | una casella larga sfonda la colonna |
| area di tocco ≥ 44×44 | tutto ciò che si preme | bersagli che il dito manca |
| doppia classe (`.grid.mm-stats4`) | dove serve battere base.html | il blocco `<style>` in `base.html` viene **dopo** i fogli: a parità di specificità vince lui |

L'ultima è la trappola che è già costata due volte. `base.html` ha un
`<style>` in fondo al `<head>`, quindi **dopo** `telefono.css`. Una regola con
la stessa specificità perde in silenzio: la si vede scritta e non fa niente.
Un punto di specificità in più risolve senza spostare l'ordine dei fogli, che
cambierebbe la cascata di tutto il resto.

## 5. Vincoli di contenuto e di stile

- **Due temi, chiaro e scuro.** Qui, al contrario di Shift Hours, il tema
  scuro c'è ed è in uso: nessun colore scritto a mano, o si rompe in silenzio
  in uno dei due.
- **Identità invariata**: pistacchio elettrico `--lime-400` e giallo pastello
  `--yellow-300` su neutri caldi. Si lavora sugli accostamenti, non
  sull'identità.
- **Font**: Geist, già caricato. Geist Mono solo per ticker e ISIN.
- **Larghezza massima 440px**, centrata, come Shift Hours. Sopra i 760 punti
  comanda il PC e questo impianto non esiste.
- **Sette schermate, non una di più e non una di meno.** Home, Finanze,
  Portafoglio, PAC, Analisi, Notizie, Impostazioni.
- **Non si toglie nessuna funzione.** Questo è un rifacimento dell'aspetto. Se
  una cosa oggi si può fare dal telefono, dopo si deve poter fare ancora —
  eventualmente da un pannello invece che dalla prima schermata, mai da
  nessuna parte.
- **Numeri sempre tabellari** (`font-variant-numeric: tabular-nums`):
  incolonnati si confrontano a occhio.
- **Nessun dato inventato.** Vale nell'app e vale nella pagina di riferimento,
  dove invece i dati devono essere **tutti** finti: nel repo non entra mai un
  saldo vero.

### I ruoli del colore, che devono restare distinguibili a colpo d'occhio

| ruolo | token | non deve confondersi con |
|---|---|---|
| **azione** | `--accent` (lime) | — |
| **guadagno** | `--pos` (verde) | l'azione: il lime e il verde sono vicini, e un saldo non è un bottone |
| **perdita** | `--neg` (rosso) | — |
| **agente AI** | `--ai` (giallo) | l'azione |
| **neutro** (partite di giro) | `--muted` | il guadagno: un giro **non è** un incasso |

L'ultima riga è già costata un bug: l'importo di un trasferimento veniva
scritto in verde perché ereditava il colore dei link, e si leggeva come
un'entrata.

## 6. Cosa si consegna

1. I tre fogli in `app/static/telefono/`.
2. La pagina di riferimento in `app/static/riferimento/`: **tutti** i
   componenti e **le schermate intere** alla larghezza di un iPhone, nei due
   temi, con dati inventati. È il documento su cui si approva il lavoro
   **prima** di guardarlo sul telefono.

   Sta sotto `static/` e non in `docs/` per un motivo preciso: **deve essere
   servita dall'app**, allo stesso indirizzo da cui arrivano i fogli veri. Le
   cornici dei telefoni sono iframe — l'unico modo di dare a ogni schermata il
   suo contesto da 375 punti dentro una finestra grande — e Chrome rifiuta di
   caricare un iframe `file://` dentro una pagina `file://`, lasciando il
   riquadro vuoto **senza dire niente**. Aperta con doppio clic mostrerebbe sei
   rettangoli neri e farebbe pensare che il lavoro sia rotto.
3. I template, solo dopo l'approvazione della pagina di riferimento.

## 7. Lista di controllo prima di dire «fatto»

- [ ] I file del design freeze hanno `git diff` pulito.
- [ ] Nessuna classe della sezione 3 è sparita.
- [ ] Le regole della sezione 4 sono presenti.
- [ ] Nessuna funzione è diventata irraggiungibile dal telefono.
- [ ] **Il PC a 1280 punti è identico a prima**: stesse colonne, stesse righe,
      nessuno scorrimento orizzontale, nessuna misura cambiata.
- [ ] A 375 punti non c'è scorrimento orizzontale su nessuna schermata.
- [ ] Ogni schermata sta nel guscio: scorre il corpo, non la pagina.
- [ ] Ogni bersaglio di tocco è ≥ 44×44.
- [ ] Entrambi i temi provati, chiaro e scuro.
- [ ] Nessun errore in console.
- [ ] La suite dei test passa.
- [ ] Commit e push su `main`.

## 8. Cosa NON fare

- Non aggiungere funzioni, schermate o campi.
- Non togliere funzioni: spostarle sì, farle sparire no.
- Non toccare i file del design freeze.
- Non scrivere colori a mano: si rompe il tema scuro, e si scopre tardi.
- Non inventare numeri per far stare meglio un blocco.
- Non dare mai segnali operativi: l'app aiuta a capire, non dice cosa fare.
