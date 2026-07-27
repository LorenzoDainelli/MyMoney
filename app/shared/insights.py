"""Estrattore di FATTI: trova in Python ciò che merita davvero di essere detto.

Il principio dell'agente è questo: **l'AI non calcola, spiega**. Qui dentro non
c'è nessuna chiamata all'AI — solo aritmetica sui dati veri. Ogni fatto nasce
da un confronto con la STORIA DELL'UTENTE (la sua mediana, le sue abitudini),
mai con medie generiche: "molto" non esiste, "il doppio del tuo solito" sì.

Perché serve: se all'AI passiamo dei totali, può solo riformularli — ed è per
questo che oggi suona come un'informativa. Se le passiamo "gli abbonamenti sono
il 114% sopra la tua mediana di 6 mesi", ha qualcosa da spiegare.

Due garanzie che discendono da qui:
- i numeri non possono essere inventati: li calcola il codice, non il modello;
- se NIENTE supera le soglie, la lista torna vuota e l'agente deve dirlo. È la
  difesa contro il rischio peggiore: un'AI che, dovendo per forza dire qualcosa
  di interessante, inventa un significato che non c'è.
"""
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

# ---------------------------------------------------------------------------
# Soglie: sotto queste, un fatto NON è un fatto (è rumore). Sono deliberatamente
# alte: meglio poche osservazioni vere che tante plausibili.
# ---------------------------------------------------------------------------
MIN_EURO_CATEGORIA = 15.0     # sotto questa cifra uno scostamento non conta
MIN_SCOSTAMENTO = 40.0        # % di distanza dalla mediana per essere notevole
MIN_MESI_STORIA = 2           # mesi passati necessari per avere una "abitudine"
SOGLIA_CONCENTRAZIONE = 30.0  # % di un settore sul portafoglio
SOGLIA_DOMINANZA_CAT = 40.0   # % di una categoria sulle uscite del mese
MULT_MOVIMENTO_ANOMALO = 3.0  # quante volte la spesa tipica per essere "grosso"
GIORNI_PAC_RUMORE = 45        # sotto, il rendimento del PAC è rumore, non trend


@dataclass
class Fatto:
    """Una cosa notevole, già verificata e già quantificata.

    `chiave` è stabile nel tempo (es. "cat:abbonamenti:sopra"): serve alla
    memoria per sapere se questa osservazione è già stata fatta e non ripeterla.
    `forza` (0-100) dice quanto merita di aprire il discorso.
    """
    chiave: str
    testo: str
    forza: float
    area: str = "finanze"
    dati: dict = field(default_factory=dict)


def _eur(v) -> str:
    """Numero all'italiana dentro il testo di un fatto (1.234,56 €)."""
    s = f"{abs(float(v)):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return ("-" if v < 0 else "") + s + " €"


def _pct(v, dec: int = 0, segno: bool = False) -> str:
    """Percentuale all'italiana (36,9%): la virgola decimale conta, il testo
    finisce sotto gli occhi dell'utente."""
    s = f"{float(v):+.{dec}f}" if segno else f"{float(v):.{dec}f}"
    return s.replace(".", ",") + "%"


def _mesi_indietro(now: datetime, k: int) -> tuple:
    y, m = now.year, now.month - k
    while m <= 0:
        m += 12
        y -= 1
    return y, m


def _forza(scostamento_pct: float, euro: float) -> float:
    """Un fatto pesa per QUANTO è fuori scala e per QUANTI soldi muove: uno
    scostamento enorme su 3 € non interessa, uno del 50% su 200 € sì."""
    peso_pct = min(abs(scostamento_pct) / 2.0, 50.0)      # 100% fuori -> 50 punti
    peso_eur = min(abs(euro) / 10.0, 50.0)                # 500 € -> 50 punti
    return round(min(peso_pct + peso_eur, 100.0), 1)


# ===========================================================================
#  QUANTA STORIA ESISTE DAVVERO
#  Il vincolo più importante di tutti: l'app raccoglie dati dal 4/7/2026, e non
#  si può parlare di andamenti che durano più della storia che possediamo. Un
#  titolo può avere 12 mesi di storia DI MERCATO, ma se l'utente lo possiede da
#  otto giorni quel numero non racconta la SUA esperienza: sono due cose diverse
#  e vanno tenute distinte, altrimenti l'agente scrive frasi false.
# ===========================================================================
def orizzonte(oggi: datetime = None) -> dict:
    """Da quando esistono i dati, e per quali confronti bastano."""
    from finance import service as fin

    now = oggi or datetime.now()
    inizio = fin.data_inizio()
    giorni = max(0, (now - inizio).days)

    # mesi civili COMPLETI prima di quello corrente: sono gli unici con cui si
    # possa fare un confronto onesto
    mesi_completi = 0
    for k in range(1, 25):
        y, m = _mesi_indietro(now, k)
        primo = datetime(y, m, 1)
        ultimo = datetime(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
        if primo >= inizio and ultimo <= now:
            mesi_completi += 1
        else:
            break

    giorni_pac = None
    try:
        from portfolio import versamenti
        elenco = versamenti.lista()
        if elenco:
            giorni_pac = (now.date() - min(v["data"] for v in elenco)).days
    except Exception:
        pass

    # quante giornate esistono davvero in archivio: è il vero limite dei
    # confronti giorno-per-giorno (diverso dai mesi civili completi)
    giorni_archivio = 0
    try:
        from shared import storico as _st
        giorni_archivio = _st.giorni_disponibili()
    except Exception:
        pass

    return {"inizio": inizio, "giorni": giorni, "mesi_completi": mesi_completi,
            "giorni_pac": giorni_pac, "giorni_archivio": giorni_archivio}


def come_testo_orizzonte(oz: dict = None) -> str:
    """Il paragrafo che impedisce all'agente di parlare di anni quando ha
    settimane. Va in cima al prompt: è un vincolo, non un dettaglio."""
    oz = oz or orizzonte()
    righe = [
        "QUANTA STORIA ESISTE (vincolo, non dettaglio):",
        f"- l'app raccoglie dati dal {oz['inizio'].strftime('%d/%m/%Y')}: "
        f"{oz['giorni']} giorni in tutto.",
    ]
    if oz["mesi_completi"] == 0:
        righe.append("- NON esiste nessun mese passato completo: non hai un "
                     "termine di paragone storico. Non dire mai «rispetto ai "
                     "mesi scorsi», «di solito», «come sempre»: non lo sai.")
    else:
        righe.append(f"- mesi passati completi disponibili: {oz['mesi_completi']}. "
                     "Confronta solo con questi.")
    if oz["giorni_pac"] is not None:
        righe.append(f"- il PAC esiste da {oz['giorni_pac']} giorni.")
    if oz.get("giorni_archivio"):
        righe.append(f"- giornate registrate in archivio: {oz['giorni_archivio']}. "
                     "Su questo archivio i confronti giorno-per-giorno sono "
                     "leciti, entro questo numero di giorni e non oltre.")
    righe += [
        "- ATTENZIONE alla differenza fra la storia DI MERCATO di un titolo (che "
        "può essere lunga anni) e da quanto tempo la possiede l'utente. Un "
        "«+3878% a 12 mesi» è successo al titolo, NON a lui: lui non c'era. "
        "Se citi una performance a 12 mesi, di' esplicitamente che è l'andamento "
        "del titolo sul mercato, non il suo guadagno.",
        "- Non descrivere MAI tendenze più lunghe della storia che hai. Con poche "
        "settimane di dati non esistono «abitudini», «andamenti» o «tendenze»: "
        "esistono solo singoli fatti. Dirlo è più utile che fingere il contrario.",
        "",
    ]
    return "\n".join(righe)


# ===========================================================================
#  FINANZE: confronti col passato dell'utente
# ===========================================================================
def fatti_finanze(mesi_storia: int = 6, oggi: datetime = None) -> list:
    """Cosa è successo ai soldi di questo mese che non somiglia ai mesi scorsi."""
    from finance import service as fin

    now = oggi or datetime.now()
    fatti = []
    try:
        corrente = fin.riepilogo_mese(now.year, now.month)
        passati = []
        for k in range(1, mesi_storia + 1):
            y, m = _mesi_indietro(now, k)
            r = fin.riepilogo_mese(y, m)
            # un mese senza alcun movimento non è "un mese a zero spese":
            # è un mese in cui non c'erano ancora dati. Non fa storia.
            if r["entrate"] or r["uscite"]:
                passati.append(r)
    except Exception:
        return fatti

    spese_corr = {s["nome"]: s["tot"] for s in corrente["spese_categoria"]}

    # --- 1. categorie fuori dalla loro abitudine -------------------------
    if len(passati) >= MIN_MESI_STORIA:
        storiche = {}
        for r in passati:
            visti = {s["nome"]: s["tot"] for s in r["spese_categoria"]}
            for nome in set(visti) | set(storiche):
                storiche.setdefault(nome, []).append(visti.get(nome, 0.0))

        for nome, tot in spese_corr.items():
            serie = storiche.get(nome)
            if tot < MIN_EURO_CATEGORIA:
                continue
            if not serie or all(v == 0 for v in serie):
                fatti.append(Fatto(
                    chiave=f"cat:{nome.lower()}:nuova",
                    testo=f"«{nome}» compare per la prima volta: {_eur(tot)} "
                          f"questo mese, mai negli ultimi {len(passati)} mesi.",
                    forza=_forza(100.0, tot),
                    dati={"categoria": nome, "importo": tot, "tipo": "nuova"}))
                continue
            med = median(serie)
            if med <= 0:
                continue
            scost = (tot / med - 1) * 100
            if abs(scost) >= MIN_SCOSTAMENTO:
                verso = "sopra" if scost > 0 else "sotto"
                fatti.append(Fatto(
                    chiave=f"cat:{nome.lower()}:{verso}",
                    testo=f"«{nome}»: {_eur(tot)} questo mese contro una mediana "
                          f"di {_eur(med)}, cioè il {_pct(abs(scost))} {verso} "
                          f"il tuo solito.",
                    forza=_forza(scost, tot),
                    dati={"categoria": nome, "importo": tot, "mediana": med,
                          "scostamento_pct": round(scost, 1)}))

        # --- 2. abitudini SPARITE (spesso più informative di quelle nuove) ---
        for nome, serie in storiche.items():
            presenze = sum(1 for v in serie if v > 0)
            if nome in spese_corr or presenze < 3:
                continue
            tipica = median([v for v in serie if v > 0])
            if tipica < MIN_EURO_CATEGORIA:
                continue
            fatti.append(Fatto(
                chiave=f"cat:{nome.lower()}:sparita",
                testo=f"«{nome}» non compare questo mese: c'era in {presenze} "
                      f"degli ultimi {len(passati)} mesi, di solito {_eur(tipica)}.",
                forza=_forza(100.0, tipica),
                dati={"categoria": nome, "tipica": tipica, "presenze": presenze}))

    # --- 3. il mese nel suo insieme --------------------------------------
    if len(passati) >= MIN_MESI_STORIA:
        med_usc = median([r["uscite"] for r in passati])
        if med_usc > 0:
            scost = (corrente["uscite"] / med_usc - 1) * 100
            if abs(scost) >= MIN_SCOSTAMENTO:
                fatti.append(Fatto(
                    chiave="mese:uscite",
                    testo=f"Uscite del mese {_eur(corrente['uscite'])}, "
                          f"{_pct(abs(scost))} {'sopra' if scost > 0 else 'sotto'} "
                          f"la mediana degli ultimi mesi ({_eur(med_usc)}).",
                    forza=_forza(scost, corrente["uscite"]),
                    dati={"uscite": corrente["uscite"], "mediana": med_usc}))

        # proiezione a fine mese al ritmo tenuto finora
        giorni_passati = now.day
        if giorni_passati >= 7:
            fine = 31 if now.month in (1, 3, 5, 7, 8, 10, 12) else 30
            if now.month == 2:
                fine = 29 if now.year % 4 == 0 else 28
            proiezione = corrente["uscite"] / giorni_passati * fine
            if med_usc > 0 and abs(proiezione / med_usc - 1) * 100 >= MIN_SCOSTAMENTO:
                fatti.append(Fatto(
                    chiave="mese:proiezione",
                    testo=f"Al ritmo di questi {giorni_passati} giorni il mese "
                          f"chiuderebbe intorno a {_eur(proiezione)} di uscite.",
                    forza=_forza((proiezione / med_usc - 1) * 100, proiezione) * 0.7,
                    dati={"proiezione": round(proiezione, 2)}))

    # --- 4. una sola categoria si mangia il mese -------------------------
    if corrente["uscite"] > 0 and corrente["spese_categoria"]:
        prima = corrente["spese_categoria"][0]
        quota = prima["tot"] / corrente["uscite"] * 100
        if quota >= SOGLIA_DOMINANZA_CAT and prima["tot"] >= MIN_EURO_CATEGORIA:
            fatti.append(Fatto(
                chiave=f"cat:{prima['nome'].lower()}:dominante",
                testo=f"«{prima['nome']}» da sola vale il {_pct(quota)} delle "
                      f"uscite del mese ({_eur(prima['tot'])}).",
                forza=_forza(quota, prima["tot"]),
                dati={"categoria": prima["nome"], "quota_pct": round(quota, 1)}))

    # --- 5. il movimento singolo fuori scala -----------------------------
    try:
        movimenti = fin.lista_movimenti(anno=now.year, mese=now.month)
        uscite = [m for m in movimenti if m["t"].tipo == "uscita" and m["t"].importo]
        if len(uscite) >= 5:
            tipica = median([m["t"].importo for m in uscite])
            grosso = max(uscite, key=lambda m: m["t"].importo)
            if tipica > 0 and grosso["t"].importo >= tipica * MULT_MOVIMENTO_ANOMALO:
                d = grosso["t"].data.strftime("%d/%m")
                desc = (grosso["t"].descrizione or grosso["categoria"] or "senza descrizione").strip()
                fatti.append(Fatto(
                    chiave="mov:anomalo",
                    testo=f"Spesa singola più grossa del mese: {_eur(grosso['t'].importo)} "
                          f"il {d} ({desc}), contro una spesa tipica di {_eur(tipica)}.",
                    forza=_forza(grosso["t"].importo / tipica * 100 - 100, grosso["t"].importo),
                    dati={"importo": grosso["t"].importo, "tipica": tipica}))
    except Exception:
        pass

    return fatti


# ===========================================================================
#  PORTAFOGLIO: composizione, contributi, copertura dei dati
# ===========================================================================
def fatti_portafoglio() -> list:
    """Cosa dice il portafoglio che non si vede guardando il totale."""
    from portfolio import service as pf, analytics, market

    fatti = []
    try:
        vista = pf.vista_portafoglio()
    except Exception:
        return fatti

    # --- copertura dei prezzi: onestà prima di tutto ---------------------
    if vista["n_ticker"] and vista["n_prezzi"] < vista["n_ticker"]:
        mancanti = vista["n_ticker"] - vista["n_prezzi"]
        fatti.append(Fatto(
            chiave="pf:copertura",
            testo=f"Attenzione: mancano i prezzi di {mancanti} titoli su "
                  f"{vista['n_ticker']}, quindi il totale è sottostimato.",
            forza=40 + mancanti * 5, area="portafoglio",
            dati={"mancanti": mancanti}))

    # --- concentrazione settoriale ---------------------------------------
    try:
        lt = analytics.look_through(cached_only=True)
        for s in lt["settori"][:3]:
            if s["pct"] >= SOGLIA_CONCENTRAZIONE:
                fatti.append(Fatto(
                    chiave=f"settore:{s['key']}:concentrazione",
                    testo=f"Il settore {s['key']} vale il {_pct(s['pct'], 1)} del "
                          f"portafoglio: è la concentrazione più alta.",
                    forza=min(30 + (s["pct"] - SOGLIA_CONCENTRAZIONE) * 2, 90),
                    area="portafoglio", dati={"settore": s["key"], "pct": s["pct"]}))
                break
    except Exception:
        pass

    # --- chi muove davvero il portafoglio DELL'UTENTE ---------------------
    # Il contributo si misura su ciò che l'utente ha VISSUTO: valore di oggi
    # meno quanto ci ha messo. Usare qui il rendimento a 12 mesi del titolo
    # sarebbe un errore di aritmetica, non solo di parole: moltiplicherebbe le
    # sue quote per un andamento che la sua posizione non ha mai attraversato,
    # perché quel titolo lui non ce l'aveva.
    contributi = []
    for r in vista["righe"]:
        p, val = r["p"], r["valore"]
        versato = p.versato_totale or 0.0
        if not val or versato <= 0:
            continue
        contributi.append((p, val, versato, val - versato))
    if contributi:
        tot_abs = sum(abs(c[3]) for c in contributi)
        p, val, versato, delta = max(contributi, key=lambda c: abs(c[3]))
        # sotto un euro di scostamento non c'è niente da spiegare: sono i
        # centesimi di un PAC appena partito
        if tot_abs >= 1.0 and abs(delta) >= 0.50:
            quota = abs(delta) / tot_abs * 100
            if quota >= 35:
                pct = (val / versato - 1) * 100
                fatti.append(Fatto(
                    chiave=f"pf:{(p.ticker or '').lower()}:contributo",
                    testo=f"{p.ticker} è il titolo che pesa di più sul TUO "
                          f"risultato: {_eur(versato)} versati valgono ora "
                          f"{_eur(val)} ({_pct(pct, 1, segno=True)}), cioè il "
                          f"{_pct(quota)} dello scostamento complessivo.",
                    forza=min(quota, 70), area="portafoglio",
                    dati={"ticker": p.ticker, "versato": versato, "valore": val,
                          "delta": round(delta, 2)}))

    return fatti


# ===========================================================================
#  PAC: versato contro valore, e la scala del tempo
# ===========================================================================
def fatti_pac(oggi: datetime = None) -> list:
    """Il PAC visto con onestà: soprattutto quando è troppo presto per leggerlo."""
    from portfolio import versamenti
    from finance import service as fin

    now = oggi or datetime.now()
    fatti = []
    try:
        storico = versamenti.lista()
        pac = fin.valore_pac_live()
    except Exception:
        return fatti
    if not storico or not pac:
        return fatti

    prima = min(v["data"] for v in storico)
    giorni = (now.date() - prima).days
    versato, valore = pac["versato"], pac["valore"]
    riv = pac["rivalutazione"]
    pct = (valore / versato - 1) * 100 if versato else 0.0

    if giorni <= GIORNI_PAC_RUMORE:
        # il fatto NOTEVOLE qui è proprio che non c'è ancora niente da leggere
        fatti.append(Fatto(
            chiave="pac:troppo_presto",
            testo=f"Il PAC è partito da {giorni} giorni: {_eur(versato)} versati, "
                  f"valore {_eur(valore)} ({riv:+.2f} €, {_pct(pct, 1, segno=True)}). A questa "
                  f"scala di tempo la variazione è rumore di mercato, non un "
                  f"andamento.",
            forza=55, area="pac",
            dati={"giorni": giorni, "versato": versato, "valore": valore}))
    else:
        fatti.append(Fatto(
            chiave="pac:andamento",
            testo=f"PAC: {_eur(versato)} versati in {len(storico)} versamenti, "
                  f"valore attuale {_eur(valore)} ({_pct(pct, 1, segno=True)}).",
            forza=45, area="pac",
            dati={"versato": versato, "valore": valore, "pct": round(pct, 2)}))

    return fatti


# ===========================================================================
#  STORICO: il confronto col proprio passato, finalmente possibile
#  Tutto questo modulo è costruito sul confrontare l'utente con sé stesso, ma
#  fino a ieri l'unico passato disponibile erano i mesi civili completi — che
#  all'inizio della vita dell'app non esistono. Ora c'è un archivio giornaliero
#  (shared/storico.py) e si può dire «rispetto a otto giorni fa» dopo otto
#  giorni, non dopo due mesi.
# ===========================================================================
MIN_EURO_MERCATO = 1.0        # sotto un euro non c'è niente da spiegare
MIN_GIORNI_CONFRONTO = 3      # con meno di così è la stessa giornata due volte


def fatti_storico(giorni: int = 7) -> list:
    """Com'è cambiato il patrimonio da qualche giorno fa, e per quale motivo:
    i soldi che hai messo tu contano a parte da quelli che ha mosso il mercato."""
    from shared import storico

    fatti = []
    try:
        c = storico.confronto(giorni)
    except Exception:
        return fatti
    if not c or c["giorni"] < MIN_GIORNI_CONFRONTO:
        return fatti

    quando = f"in {c['giorni']} giorni (dal {c['da'].strftime('%d/%m')})"
    if abs(c["mercato"]) >= MIN_EURO_MERCATO:
        verso = "guadagnato" if c["mercato"] > 0 else "perso"
        pezzi = [f"Il portafoglio ha {verso} {_eur(c['mercato'])} {quando} "
                 f"per effetto del mercato"]
        if abs(c["versato"]) >= 0.01:
            pezzi.append(f", oltre a {_eur(c['versato'])} che ci hai messo tu")
        fatti.append(Fatto(
            chiave="storico:mercato",
            testo="".join(pezzi) + ".",
            forza=min(30 + abs(c["mercato"]) * 3, 75), area="portafoglio",
            dati={"mercato": c["mercato"], "versato": c["versato"],
                  "giorni": c["giorni"]}))

    if abs(c["liquido"]) >= MIN_EURO_CATEGORIA:
        verso = "salita" if c["liquido"] > 0 else "scesa"
        fatti.append(Fatto(
            chiave="storico:liquidita",
            testo=f"La liquidità sui conti è {verso} di {_eur(c['liquido'])} {quando}.",
            forza=_forza(0, c["liquido"]) * 0.8, area="finanze",
            dati={"liquido": c["liquido"], "giorni": c["giorni"]}))

    return fatti


# ===========================================================================
#  IL QUADRO DI OGGI
#  I "fatti" qui sopra rispondono a "cosa è cambiato?", e all'inizio della vita
#  dell'app la risposta onesta è spesso "poco": non c'è passato con cui
#  confrontare. Ma esiste una seconda domanda, altrettanto legittima e che NON
#  richiede storia: "com'è messa la situazione adesso?".
#  Questo è il quadro: descrittivo, sempre disponibile, tutto calcolato. Serve a
#  dare sostanza senza inventare significato — è sfondo, non notizia, e infatti
#  il prompt vieta di aprirci il discorso.
# ===========================================================================
def quadro(oggi: datetime = None) -> list:
    """Fotografia del presente in righe brevi e verificabili."""
    from finance import service as fin

    now = oggi or datetime.now()
    righe = []

    # --- il mese in corso ------------------------------------------------
    try:
        r = fin.riepilogo_mese(now.year, now.month)
        righe.append(f"Mese in corso: entrate {_eur(r['entrate'])}, uscite "
                     f"{_eur(r['uscite'])}, saldo {_eur(r['saldo'])}.")
        if r["spese_categoria"]:
            voci = ", ".join(f"{s['nome']} {_eur(s['tot'])}"
                             for s in r["spese_categoria"][:4])
            righe.append(f"Dove sono finite le uscite: {voci}.")
        if r["uscite"] and now.day:
            righe.append(f"Ritmo di spesa: {_eur(r['uscite'] / now.day)} al giorno "
                         f"su {now.day} giorni di mese.")
    except Exception:
        pass

    # --- liquidità e investito -------------------------------------------
    try:
        sal = fin.saldi()
        pac = fin.valore_pac_live()
        righe.append(f"Liquidità sui conti: {_eur(sal['liquido'])}; "
                     f"patrimonio totale (investimenti inclusi) {_eur(sal['totale'])}.")
        if pac:
            quota = (pac["valore"] / sal["totale"] * 100) if sal["totale"] else 0
            righe.append(f"Investito nel PAC: {_eur(pac['valore'])}, cioè il "
                         f"{_pct(quota)} del patrimonio; versati "
                         f"{_eur(pac['versato'])}, differenza "
                         f"{_eur(pac['rivalutazione'])}.")
    except Exception:
        pass

    # --- il portafoglio ---------------------------------------------------
    try:
        from portfolio import service as pf, analytics
        vista = pf.vista_portafoglio()
        posizioni = [r["p"] for r in vista["righe"]]
        n_etf = sum(1 for p in posizioni if p.tipo == "ETF")
        righe.append(f"Portafoglio: {len(posizioni)} titoli ({n_etf} ETF, "
                     f"{len(posizioni) - n_etf} azioni), valore complessivo "
                     f"{_eur(vista['totale'])}.")
        try:
            lt = analytics.look_through(cached_only=True)
            settori = lt.get("settori") or []
            if settori:
                primi = ", ".join(f"{s['key']} {_pct(s['pct'], 1)}" for s in settori[:4])
                righe.append(f"Settori più presenti: {primi}. In tutto "
                             f"{len(settori)} settori.")
        except Exception:
            pass
        # i tre titoli più pesanti: dice com'è distribuito, senza giudicare
        pesi = [(r["p"].ticker, r["valore"]) for r in vista["righe"] if r["valore"]]
        if pesi and vista["totale"]:
            pesi.sort(key=lambda x: -x[1])
            top = ", ".join(f"{t} {_pct(v / vista['totale'] * 100, 1)}" for t, v in pesi[:3])
            righe.append(f"Titoli più pesanti: {top}.")
    except Exception:
        pass

    # --- il PAC: cadenza e prossimo atteso --------------------------------
    try:
        from portfolio import versamenti
        storico = versamenti.lista()
        if storico:
            ultimo = max(v["data"] for v in storico)
            tot = sum(v["importo"] for v in storico)
            righe.append(f"PAC: {len(storico)} versamenti per {_eur(tot)} in tutto, "
                         f"l'ultimo il {ultimo.strftime('%d/%m/%Y')}.")
    except Exception:
        pass

    return righe


def come_testo_quadro(righe: list = None) -> str:
    righe = quadro() if righe is None else righe
    if not righe:
        return ""
    return ("IL QUADRO DI OGGI (sfondo verificato: serve a dare sostanza e "
            "contesto. NON aprire il discorso da qui — questi non sono fatti "
            "nuovi, sono lo stato delle cose):\n"
            + "\n".join(f"- {r}" for r in righe) + "\n")


# ===========================================================================
#  Raccolta
# ===========================================================================
AREE = ("finanze", "portafoglio", "pac", "storico")


def raccogli(aree=AREE, limite: int = 8, oggi: datetime = None) -> list:
    """Tutti i fatti delle aree richieste, dal più forte al più debole.

    Lista VUOTA è un risultato legittimo e va rispettato: vuol dire che non è
    successo niente degno di nota, e l'agente deve poterlo dire in una riga.
    All'inizio della vita dell'app è anzi la norma: senza mesi passati non c'è
    nulla con cui confrontare, e fingere il contrario sarebbe disonesto."""
    fatti = []
    if "finanze" in aree:
        fatti += fatti_finanze(oggi=oggi)
    if "portafoglio" in aree:
        fatti += fatti_portafoglio()
    if "pac" in aree:
        fatti += fatti_pac(oggi=oggi)
    if "storico" in aree:
        fatti += fatti_storico()
    fatti.sort(key=lambda f: f.forza, reverse=True)
    return fatti[:limite]


def come_testo(fatti: list) -> str:
    """I fatti come lista numerata da mettere nel prompt, forza inclusa: così
    il modello sa da quale partire senza doverlo indovinare."""
    if not fatti:
        return "NESSUN FATTO NOTEVOLE: niente ha superato le soglie."
    return "\n".join(
        f"{i}. [{f.area}, forza {f.forza:.0f}] {f.testo}"
        for i, f in enumerate(fatti, 1))
