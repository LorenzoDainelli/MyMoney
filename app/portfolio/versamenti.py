"""Versamenti PAC: registra un acquisto distribuito sui titoli e lo rende
modificabile/eliminabile.

Modello (come Trade Republic con il PMC): UNA sola posizione per titolo, le
quantità si SOMMANO e il prezzo medio di carico si ricalcola da solo. Ogni PAC:
- ripartisce l'importo fra i titoli inclusi, in proporzione alla % target
  (normalizzata così il totale torna esatto al centesimo);
- calcola le quote comprate col prezzo di quel giorno (stima onesta: mai numeri
  inventati — se un prezzo manca, aggiunge solo il valore in € e lo segnala);
- conserva il DELTA applicato a ogni posizione (in `VersamentoRiga`), così
  eliminare o modificare un PAC ripristina esattamente le quantità.

Tutto OFFLINE/locale, nessun segnale operativo: registra ciò che l'utente ha già
fatto, non suggerisce acquisti.
"""
from datetime import date, datetime, timezone

from sqlalchemy import select, func

from shared.db import SessionLocal
from shared import tempo
from portfolio.models import Position, Versamento, VersamentoRiga
from portfolio import service, market


def normalizza_ora(s: str) -> str:
    """Come l'hai scritta di fretta -> "HH:MM". Stringa vuota se non è un'ora.

    I due punti, sul telefono, vogliono un cambio di tastiera: trentotto volte
    sono trentotto cambi di tastiera. Qui si accettano le cifre e basta —
    "0935", "935", "9:35" sono tutte le nove e trentacinque — perché quello che
    deve essere vero è l'orario, non il modo in cui l'hai battuto.

    Una o due cifre sole valgono l'ora tonda ("9" -> 09:00). Non è un indovinare:
    l'anteprima mostra sempre l'ora che verrà usata per ogni titolo, quindi una
    lettura sbagliata la vedi prima di confermare, non dopo.
    """
    cifre = "".join(ch for ch in (s or "") if ch.isdigit())
    if not cifre:
        return ""
    if len(cifre) <= 2:                  # "9" / "17" -> ora tonda
        cifre = cifre.rjust(2, "0") + "00"
    elif len(cifre) == 3:                # "935" -> 09:35
        cifre = "0" + cifre
    cifre = cifre[:4]                    # "17:05:33" resta le 17:05, come prima
    h, m = int(cifre[:2]), int(cifre[2:])
    if h > 23 or m > 59:
        return ""
    return f"{h:02d}:{m:02d}"


def parse_ora(s: str):
    """'HH:MM' -> time, oppure None se vuota/non valida. Nessun orario inventato."""
    n = normalizza_ora(s)
    return datetime.strptime(n, "%H:%M").time() if n else None


# Yahoo tiene le candele orarie solo per il periodo recente: oltre, si usa la
# chiusura del giorno (unico dato onesto disponibile).
GIORNI_INTRADAY = 25


def istante(data: date, t, fuso: str = "") -> float:
    """L'orario che hai scritto, nel fuso in cui l'hai letto, come istante
    universale (secondi epoch).

    È qui che «09:35» smette di essere ambiguo. Le nove e trentacinque di dove?
    Gli orari di esecuzione li leggi nell'app della banca, che può mostrarli
    nell'ora di un altro Paese — e in Irlanda le 09:35 sono un'ora dopo che a
    Roma, cioè un'altra candela e un altro prezzo. `fuso` vuoto vuol dire
    «l'ora dell'app», che è il comportamento di prima.
    """
    return datetime.combine(data, t, tzinfo=tempo.fuso_di(fuso)).timestamp()


def _prezzo_intraday(tk: str, quando: float):
    """Prezzo all'ISTANTE indicato, dalle candele orarie. None se non c'è.

    I timestamp di Yahoo sono istanti universali e qui si confrontano con un
    istante universale: nessuna conversione a metà strada, nessun modo di
    sbagliare fuso. Prima il confronto passava per l'orologio della macchina, e
    sul server — che gira in UTC — l'app andava a prendere la candela di due
    ore prima, cioè un altro prezzo.
    """
    serie = market.history_series(market._yahoo_symbol(tk), "1mo", "1h")
    best = None
    for epoch, close in serie:
        if close and float(epoch) <= quando:
            best = close
    return best


def _prezzo_eur_alla_data(p: Position, data: date, qmap: dict, oggi: date,
                          ora=None, fuso: str = ""):
    """Prezzo in € del titolo alla data (e all'ora, se indicata).
    Ritorna (prezzo, fonte). fonte: 'live' (prezzo corrente), 'orario' (candela
    dell'ora indicata), 'storico' (chiusura di quel giorno), 'live~' (ripiego sul
    corrente), 'n/d' (non disponibile)."""
    tk = (p.ticker or "").strip()
    if not tk:
        return None, "n/d"
    q = qmap.get(tk.upper())

    def in_euro(prezzo):
        """Converte in € usando la valuta di quotazione del titolo."""
        cur = (q.currency if q else "") or "EUR"
        try:
            rate = market._fx_to_eur_rate(cur)
        except Exception:
            rate = 0
        return round(prezzo / rate, 4) if rate else None

    # con l'ora: provo la candela oraria (solo per il periodo recente)
    t = parse_ora(ora) if isinstance(ora, str) else ora
    if t is not None and 0 <= (oggi - data).days <= GIORNI_INTRADAY:
        quando = istante(data, t, fuso)
        if quando <= datetime.now(timezone.utc).timestamp():
            val = _prezzo_intraday(tk, quando)
            if val is not None:
                prezzo = in_euro(val)
                if prezzo:
                    return prezzo, "orario"

    # oggi (o data futura, per sicurezza): usa il prezzo corrente in €
    if data >= oggi:
        if q and q.ok and q.price_eur:
            return round(q.price_eur, 4), "live"
        return None, "n/d"
    # data passata: cerca la chiusura di quel giorno (o del giorno buono precedente)
    serie = market.history_series(market._yahoo_symbol(tk), "3mo", "1d")
    best = None
    for epoch, close in serie:
        d = datetime.utcfromtimestamp(epoch).date()
        if d <= data and close:
            best = close
    if best is not None:
        prezzo = in_euro(best)
        if prezzo:
            return prezzo, "storico"
    # ripiego: prezzo corrente, se lo storico non è raggiungibile
    if q and q.ok and q.price_eur:
        return round(q.price_eur, 4), "live~"
    return None, "n/d"


def _riparti(posizioni, importo: float, esclusi: set):
    """Assegna a ogni titolo scelto la sua quota in €.
    Ritorna (lista_posizioni_incluse, {id: euro}).

    **Caso normale:** proporzionale alla % target, normalizzata sui soli
    inclusi — escludere un titolo redistribuisce la sua quota sugli altri.

    **Secondo caso, e non è una scorciatoia:** i titoli scelti possono non
    avere NESSUNA quota target. Succede con l'ETC oro, che ha `pct_target = 0`
    di proposito, perché non fa parte della ripartizione dei 100 € mensili:
    quello lo compra la banca con i saveback e gli arrotondamenti. Prima quei
    titoli venivano scartati dal filtro e il modulo rispondeva «nessun titolo
    incluso», che era vero ma inutile — l'app sapeva dell'oro come 38ª
    posizione, sapeva che il saveback lo compra, e non dava nessun modo di
    registrare l'acquisto.

    Quando nessuno dei titoli scelti ha una quota si divide **in parti uguali**.
    Con un titolo solo vuol dire tutto a lui, che è il caso vero. Questo ramo
    si accende solo dove prima non usciva niente: nulla di ciò che già
    funzionava può cambiare comportamento.
    """
    scelti = [p for p in posizioni if p.id not in esclusi and not p.is_fisso]
    con_quota = [p for p in scelti if (p.pct_target or 0) > 0]
    if con_quota:
        # Basta che UNO dei scelti abbia una quota perché comandino le quote:
        # in un versamento misto un titolo a target zero non deve mangiarsi
        # una fetta che nel piano non gli spetta.
        inclusi = con_quota
        pesi = {p.id: p.pct_target for p in inclusi}
    else:
        inclusi = scelti
        pesi = {p.id: 1.0 for p in inclusi}

    somma = sum(pesi.values())
    if importo <= 0 or somma <= 0 or not inclusi:
        return [], {}
    euros, acc = {}, 0.0
    for p in inclusi:
        e = round(importo * pesi[p.id] / somma, 2)
        euros[p.id] = e
        acc += e
    # l'arrotondamento ai centesimi può lasciare un residuo: lo metto sul titolo
    # con più peso, così la somma torna ESATTA all'importo.
    resid = round(importo - acc, 2)
    if resid and inclusi:
        big = max(inclusi, key=lambda p: (pesi[p.id], p.id))
        euros[big.id] = round(euros[big.id] + resid, 2)
    return inclusi, euros


def ora_del_titolo(pid: int, orari: dict | None) -> str:
    """L'ora di esecuzione di QUESTO titolo, scritta bene. Un posto solo, così
    anteprima e salvataggio non possono leggere due orari diversi per la stessa
    riga."""
    return normalizza_ora((orari or {}).get(pid))


def anteprima(importo: float, data: date, esclusi: set,
              orari: dict | None = None, fuso: str = "") -> dict:
    """Calcola (senza salvare nulla) come verrebbe distribuito il versamento.

    `orari` è {id_posizione: "HH:MM"}: l'ora di esecuzione titolo per titolo.
    Chi non ce l'ha prende il prezzo del giorno. `fuso` dice in che ora sono
    scritti quegli orari (vuoto = quella dell'app)."""
    qmap = market.quotes_map()
    oggi = tempo.oggi()
    posizioni = service.lista_posizioni()
    inclusi, euros = _riparti(posizioni, importo, esclusi)
    righe, avvisi, tot = [], [], 0.0
    for p in inclusi:
        euro = euros[p.id]
        tot += euro
        ora_p = ora_del_titolo(p.id, orari)
        prezzo, fonte = _prezzo_eur_alla_data(p, data, qmap, oggi, ora_p, fuso)
        qta = round(euro / prezzo, 6) if (prezzo and prezzo > 0) else None
        if qta is None:
            avvisi.append(p.ticker or p.nome_vista)
        # La percentuale MOSTRATA è la fetta vera di questo versamento, non la
        # quota target grezza. Con tutti i titoli inclusi le due coincidono
        # (i target fanno 100). Con qualche esclusione la quota grezza direbbe
        # «20%» accanto a un importo che è il 25 — e sull'oro, che di target ne
        # ha zero, direbbe «0%» accanto a tutti i soldi del versamento.
        righe.append({"id": p.id, "ticker": p.ticker, "nome": p.nome_vista,
                      "pct": round(euro / importo * 100, 2) if importo else 0.0,
                      "euro": euro, "prezzo": prezzo, "ora": ora_p,
                      "qta": qta, "fonte": fonte})
    return {"righe": righe, "totale": round(tot, 2), "n_inclusi": len(inclusi),
            "avvisi": avvisi, "data": data, "importo": round(importo, 2),
            "fuso": fuso, "fuso_etichetta": tempo.etichetta(fuso)}


DESCRIZIONE_MOVIMENTO = "Versamento PAC"


def _sync_finanze(vid: int, importo: float, data: date, conto: str,
                  ora: str = "", fuso: str = "") -> None:
    """Riflette il PAC in Finanze: UN trasferimento dal conto di provenienza al
    portafoglio 'PAC investimenti'.

    È un giro interno, quindi il patrimonio non cambia: si sposta e basta. Il
    movimento è uno solo per versamento (aggiornato, mai duplicato). Le
    oscillazioni di mercato NON diventano movimenti: il saldo del conto PAC è
    derivato dal Portafoglio (vedi finance.service.valore_pac_live)."""
    from finance import service as fin
    from finance.models import TIPO_TRASFERIMENTO

    with SessionLocal() as db:
        v = db.get(Versamento, vid)
        tx_id = v.tx_id if v is not None else None
        if v is None:
            return

    dest = fin.wallet_per_nome(fin.NOME_WALLET_PAC)
    src = fin.wallet_per_nome(conto)
    # conto non riconosciuto (o coincide con la destinazione): niente movimento
    # inventato, ma se ce n'era uno vecchio va tolto.
    if dest is None or src is None or dest.id == src.id:
        if tx_id:
            fin.elimina_movimento(tx_id)
            with SessionLocal() as db:
                v = db.get(Versamento, vid)
                if v is not None:
                    v.tx_id = None
                    db.commit()
        return

    # Il movimento porta l'ora del primo ordine eseguito, se c'è, riportata
    # nell'ora dell'app: in Finanze gli orari sono tutti quelli, e un'ora
    # tedesca in mezzo a ore irlandesi non si distinguerebbe da un errore.
    # Il GIORNO resta quello del versamento anche quando la conversione lo
    # sposterebbe: Finanze e Portafoglio non devono mai raccontare due date
    # diverse per lo stesso acquisto.
    t = parse_ora(ora)
    if t is None:
        quando = datetime.combine(data, datetime.min.time())
    else:
        locale = datetime.fromtimestamp(istante(data, t, fuso), tz=timezone.utc)
        quando = datetime.combine(data, locale.astimezone(tempo.fuso()).time())
    if tx_id and fin.aggiorna_movimento(tx_id, TIPO_TRASFERIMENTO, quando, importo,
                                        src.id, dest.id,
                                        descrizione=DESCRIZIONE_MOVIMENTO):
        return
    nuovo = fin.crea_movimento(TIPO_TRASFERIMENTO, quando, importo, src.id, dest.id,
                               descrizione=DESCRIZIONE_MOVIMENTO)
    with SessionLocal() as db:
        v = db.get(Versamento, vid)
        if v is not None:
            v.tx_id = nuovo
            db.commit()


def _reverse(db, vid: int, posmap: dict) -> None:
    """Annulla i delta di un versamento sulle posizioni e cancella le sue righe."""
    righe = db.execute(
        select(VersamentoRiga).where(VersamentoRiga.versamento_id == vid)
    ).scalars().all()
    for r in righe:
        p = posmap.get(r.position_id)
        if p is not None:
            if r.qta is not None:
                p.quantita = round(max(0.0, (p.quantita or 0) - r.qta), 8)
            else:
                p.valore_posseduto = round(max(0.0, (p.valore_posseduto or 0) - r.euro), 2)
            p.versato_totale = round(max(0.0, (p.versato_totale or 0) - r.euro), 2)
        db.delete(r)


def salva(importo: float, data: date, conto: str, esclusi: set, vid=None,
          orari: dict | None = None, fuso: str = "") -> int | None:
    """Registra un nuovo versamento (vid=None) o ne modifica uno esistente.
    Applica le quote alle posizioni (PMC) e memorizza i delta. Ritorna l'id.

    `orari` è {id_posizione: "HH:MM"}: l'ora di esecuzione di ogni singolo
    titolo. L'ora del versamento non si chiede più: è il primo ordine eseguito,
    e la si ricava da qui — un'ora scritta a parte sarebbe una seconda verità
    accanto a queste, e prima o poi le due si contraddicono."""
    qmap = market.quotes_map()
    oggi = tempo.oggi()
    with SessionLocal() as db:
        posizioni = list(db.execute(
            select(Position).order_by(Position.ordine, Position.id)).scalars().all())
        posmap = {p.id: p for p in posizioni}
        inclusi, euros = _riparti(posizioni, importo, esclusi)
        if not inclusi:
            return None
        if vid:                                   # modifica: prima annullo il vecchio
            v = db.get(Versamento, vid)
            if v is None:
                return None
            _reverse(db, vid, posmap)
        else:
            v = Versamento()
            db.add(v)
        v.data, v.importo, v.conto = data, round(importo, 2), (conto or "").strip()
        # Il fuso si salva col versamento e non si rilegge dalle impostazioni:
        # gli orari restano quelli letti allora, anche se poi cambi Paese tu.
        v.fuso = (fuso or "").strip() if tempo.valido(fuso) else ""
        # È il PAC del mese, o un acquisto a parte? Lo dicono i titoli: se
        # nessuno di quelli comprati ha una quota nel piano, questo versamento
        # nel piano non c'è — è l'oro comprato dalla banca coi saveback. Serve
        # a `promemoria()`, che altrimenti conterebbe 7 centesimi d'oro come
        # «la rata di agosto è fatta».
        v.fuori_piano = not any((p.pct_target or 0) > 0 for p in inclusi)
        db.flush()                                # per avere v.id
        ore_usate = []
        for p in inclusi:
            euro = euros[p.id]
            ora_p = ora_del_titolo(p.id, orari)
            if ora_p:
                ore_usate.append(ora_p)
            prezzo, fonte = _prezzo_eur_alla_data(p, data, qmap, oggi, ora_p, fuso)
            qta = round(euro / prezzo, 6) if (prezzo and prezzo > 0) else None
            if qta is not None:
                p.quantita = round((p.quantita or 0) + qta, 8)
            else:                                  # prezzo n/d: tengo almeno il valore in €
                p.valore_posseduto = round((p.valore_posseduto or 0) + euro, 2)
            p.versato_totale = round((p.versato_totale or 0) + euro, 2)
            if p.data_ultimo_acquisto is None or data > p.data_ultimo_acquisto:
                p.data_ultimo_acquisto = data
            db.add(VersamentoRiga(versamento_id=v.id, position_id=p.id, isin=p.isin,
                                  ticker=p.ticker, euro=euro, qta=qta, ora=ora_p,
                                  prezzo_eur=prezzo, fonte=fonte))
        # L'ora del versamento non si chiede: è quella del primo ordine
        # eseguito. Chiederla a parte voleva dire tenere due orari per lo stesso
        # fatto — uno scritto in cima al modulo e uno sulle righe — e prima o
        # poi i due si contraddicono.
        v.ora = min(ore_usate) if ore_usate else ""
        db.commit()
        nuovo_id, ora_giro, fuso_giro = v.id, v.ora, v.fuso
    # Il trasferimento in Finanze è UNO: i soldi hanno lasciato il conto quando
    # è partito il primo ordine, l'unico istante che i dati conoscono davvero.
    _sync_finanze(nuovo_id, round(importo, 2), data, conto, ora_giro, fuso_giro)
    return nuovo_id


def elimina(vid: int) -> bool:
    """Elimina un versamento e ripristina esattamente le quantità delle posizioni."""
    with SessionLocal() as db:
        v = db.get(Versamento, vid)
        if v is None:
            return False
        tx_id = v.tx_id
        posizioni = list(db.execute(select(Position)).scalars().all())
        _reverse(db, vid, {p.id: p for p in posizioni})
        db.delete(v)
        db.commit()
    if tx_id:                       # via anche il trasferimento in Finanze
        from finance import service as fin
        fin.elimina_movimento(tx_id)
    return True


def dettaglio(vid: int) -> dict | None:
    """Dati di un versamento per il pre-riempimento della modifica."""
    with SessionLocal() as db:
        v = db.get(Versamento, vid)
        if v is None:
            return None
        righe = db.execute(
            select(VersamentoRiga).where(VersamentoRiga.versamento_id == vid)
        ).scalars().all()
        return {"id": v.id, "data": v.data, "ora": v.ora or "", "importo": v.importo,
                "conto": v.conto, "fuso": v.fuso or "",
                "inclusi_ids": {r.position_id for r in righe},
                # gli orari per titolo tornano nel modulo così come sono stati
                # salvati: modificare il PAC del mese scorso vuol dire ritrovarli
                # tutti al loro posto e correggerne due, non riscriverne trentotto.
                "orari": {r.position_id: (r.ora or "") for r in righe if r.ora}}


def ultimi_orari(escludi_vid=None) -> dict:
    """Gli orari per titolo dell'ultimo PAC che ne aveva: {id_posizione: "HH:MM"}.

    Trade Republic sgrana gli ordini più o meno negli stessi momenti ogni mese.
    Serve al bottone «Riprendi gli orari»: il mese dopo si parte da quelli e se
    ne correggono due, invece di ribatterne trentotto. Sono un PUNTO DI
    PARTENZA da controllare, non un dato: i prezzi si calcolano su quello che
    resta scritto nel modulo, cioè su ciò che hai confermato tu."""
    with SessionLocal() as db:
        vs = db.execute(select(Versamento).order_by(
            Versamento.data.desc(), Versamento.id.desc())).scalars().all()
        for v in vs:
            if escludi_vid and v.id == escludi_vid:
                continue        # modificando un PAC, «l'ultimo» non è sé stesso
            orari = {r.position_id: r.ora for r in db.execute(
                select(VersamentoRiga).where(VersamentoRiga.versamento_id == v.id)
            ).scalars().all() if r.ora}
            if orari:
                return orari
    return {}


def ultimo_fuso(escludi_vid=None) -> str:
    """Il fuso dell'ultimo versamento che ne aveva uno. Serve solo a proporlo
    già scelto nel modulo: chi legge gli orari nell'app della banca li legge
    ogni mese nella stessa ora, e ricordarselo è compito dell'app."""
    with SessionLocal() as db:
        vs = db.execute(select(Versamento).order_by(
            Versamento.data.desc(), Versamento.id.desc())).scalars().all()
        for v in vs:
            if escludi_vid and v.id == escludi_vid:
                continue
            if v.fuso:
                return v.fuso
    return ""


GIORNI_PREAVVISO = 2      # quanti giorni prima del solito iniziare a ricordarlo


def promemoria(oggi: date = None) -> dict | None:
    """«Il PAC di questo mese non l'hai ancora registrato», ma solo se è vero.

    Il giorno non è scritto nel codice: si ricava dalla MEDIANA dei versamenti
    già fatti. Se il PAC lo sposti, il promemoria si sposta con te — e se non
    hai mai versato niente, non c'è nessuna abitudine da ricordare e questa
    funzione tace (regola: mai inventare un'abitudine che non esiste)."""
    from statistics import median

    # Solo i versamenti DEL PIANO. Gli acquisti a parte — l'oro che la banca
    # compra coi saveback, pochi centesimi quando le pare — non sono la rata
    # mensile: contarli spegnerebbe il promemoria del mese («c'è già un
    # versamento di agosto») e sposterebbe le due mediane qui sotto, il giorno
    # tipico e l'importo tipico, verso date e cifre che non sono mai state tue.
    # Solo i versamenti DEL PIANO. Gli acquisti a parte — l'oro che la banca
    # compra coi saveback, pochi centesimi quando le pare — non sono la rata
    # mensile: contarli spegnerebbe il promemoria del mese («c'è già un
    # versamento di agosto») e sposterebbe le due mediane qui sotto, il giorno
    # tipico e l'importo tipico, verso date e cifre che non sono mai state tue.
    storico = [v for v in lista() if not v.get("fuori_piano")]
    if not storico:
        return None
    oggi = oggi or tempo.oggi()
    if any(v["data"].year == oggi.year and v["data"].month == oggi.month
           for v in storico):
        return None                       # questo mese è già registrato
    giorno_tipico = int(median([v["data"].day for v in storico]))
    if oggi.day < giorno_tipico - GIORNI_PREAVVISO:
        return None                       # è ancora presto: non è un promemoria, è rumore
    ultimo = max(v["data"] for v in storico)
    return {
        "giorno": giorno_tipico,
        "ultimo": ultimo,
        "giorni_da_ultimo": (oggi - ultimo).days,
        "importo_tipico": round(median([v["importo"] for v in storico]), 2),
        "in_ritardo": oggi.day > giorno_tipico,
        "n_versamenti": len(storico),
    }


def storico_quantita() -> dict:
    """Quando è arrivata, quota per quota, la quantità che oggi possiedi.
    Ritorna {position_id: (base, [(timestamp, quantità_da_lì_in_poi), ...])}.

    `base` = la parte di quantità che i versamenti NON spiegano (inserita a mano):
    si considera posseduta da sempre, perché inventarle una data di acquisto
    sarebbe peggio che ammettere di non conoscerla.

    Serve al grafico del patrimonio, che altrimenti userebbe la quantità di OGGI
    anche per i giorni in cui il titolo non l'avevi ancora — disegnando insieme i
    titoli comprati e i soldi che allora erano ancora sul conto."""
    with SessionLocal() as db:
        righe = db.execute(
            select(VersamentoRiga.position_id, VersamentoRiga.qta, Versamento.data)
            .join(Versamento, Versamento.id == VersamentoRiga.versamento_id)
            .order_by(Versamento.data, Versamento.id)).all()
        quantita = {p.id: (p.quantita or 0.0)
                    for p in db.execute(select(Position)).scalars().all()}
    passi = {}
    for pid, qta, data in righe:
        if qta:
            passi.setdefault(pid, []).append(
                (datetime.combine(data, datetime.min.time()).timestamp(), qta))
    out = {}
    for pid, q_oggi in quantita.items():
        mie = passi.get(pid, [])
        base = max(0.0, round(q_oggi - sum(d for _, d in mie), 8))
        cum, gradini = base, []
        for ts, d in mie:
            cum = round(cum + d, 8)
            gradini.append((ts, cum))
        out[pid] = (base, gradini)
    return out


def lista() -> list:
    """Storico dei versamenti (più recenti in cima), con numero di titoli.

    `ora_span` è l'ora da mostrare: una sola se i titoli sono stati eseguiti
    tutti nello stesso momento, altrimenti il primo e l'ultimo ("09:12–17:40").
    Mostrare solo l'ora del versamento, quando le righe ne hanno una ciascuna,
    racconterebbe di un istante in cui quasi niente è stato comprato."""
    with SessionLocal() as db:
        vs = db.execute(select(Versamento).order_by(
            Versamento.data.desc(), Versamento.id.desc())).scalars().all()
        ore_per_v = {}
        for vid_, o in db.execute(select(VersamentoRiga.versamento_id,
                                         VersamentoRiga.ora)).all():
            if o:
                ore_per_v.setdefault(vid_, set()).add(o)
        out = []
        for v in vs:
            n = db.execute(select(func.count()).select_from(VersamentoRiga)
                           .where(VersamentoRiga.versamento_id == v.id)).scalar()
            ore = sorted(ore_per_v.get(v.id, ()))
            span = (v.ora or "") if not ore else (
                ore[0] if len(ore) == 1 else f"{ore[0]}–{ore[-1]}")
            out.append({"id": v.id, "data": v.data, "ora": v.ora or "",
                        "ora_span": span, "n_orari": len(ore),
                        "fuso": v.fuso or "",
                        "fuso_etichetta": tempo.etichetta(v.fuso or ""),
                        "importo": v.importo, "conto": v.conto, "n_titoli": n or 0,
                        "fuori_piano": bool(v.fuori_piano)})
        return out
