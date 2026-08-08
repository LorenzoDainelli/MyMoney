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
from datetime import date, datetime

from sqlalchemy import select, func

from shared.db import SessionLocal
from shared import tempo
from portfolio.models import Position, Versamento, VersamentoRiga
from portfolio import service, market


def parse_ora(s: str):
    """'HH:MM' -> time, oppure None se vuota/non valida. Nessun orario inventato."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:5], "%H:%M").time()
    except ValueError:
        return None


# Yahoo tiene le candele orarie solo per il periodo recente: oltre, si usa la
# chiusura del giorno (unico dato onesto disponibile).
GIORNI_INTRADAY = 25


def _prezzo_intraday(tk: str, quando: datetime):
    """Prezzo alla ORA indicata, dalle candele orarie. None se non c'è.

    I timestamp di Yahoo sono istanti universali: vanno letti nello STESSO fuso
    in cui l'utente ha scritto l'orario, cioè quello scelto nelle impostazioni.
    Prima si usava l'orologio della macchina: sul server, che gira in UTC,
    l'app sarebbe andata a cercare la candela di due ore prima — un altro
    prezzo, non un dettaglio di visualizzazione.
    """
    serie = market.history_series(market._yahoo_symbol(tk), "1mo", "1h")
    best = None
    for epoch, close in serie:
        if close and tempo.da_epoch(epoch) <= quando:
            best = close
    return best


def _prezzo_eur_alla_data(p: Position, data: date, qmap: dict, oggi: date, ora=None):
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
        quando = datetime.combine(data, t)
        if quando <= tempo.adesso():
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
    """Assegna a ogni titolo incluso la sua quota in € (proporzionale alla %
    target, normalizzata). Ritorna (lista_posizioni_incluse, {id: euro})."""
    inclusi = [p for p in posizioni
               if p.id not in esclusi and not p.is_fisso and (p.pct_target or 0) > 0]
    somma = sum(p.pct_target for p in inclusi)
    if importo <= 0 or somma <= 0 or not inclusi:
        return [], {}
    euros, acc = {}, 0.0
    for p in inclusi:
        e = round(importo * p.pct_target / somma, 2)
        euros[p.id] = e
        acc += e
    # l'arrotondamento ai centesimi può lasciare un residuo: lo metto sul titolo
    # con più peso, così la somma torna ESATTA all'importo.
    resid = round(importo - acc, 2)
    if resid and inclusi:
        big = max(inclusi, key=lambda p: (p.pct_target, p.id))
        euros[big.id] = round(euros[big.id] + resid, 2)
    return inclusi, euros


def anteprima(importo: float, data: date, esclusi: set, ora: str = "") -> dict:
    """Calcola (senza salvare nulla) come verrebbe distribuito il versamento."""
    qmap = market.quotes_map()
    oggi = tempo.oggi()
    posizioni = service.lista_posizioni()
    inclusi, euros = _riparti(posizioni, importo, esclusi)
    righe, avvisi, tot = [], [], 0.0
    for p in inclusi:
        euro = euros[p.id]
        tot += euro
        prezzo, fonte = _prezzo_eur_alla_data(p, data, qmap, oggi, ora)
        qta = round(euro / prezzo, 6) if (prezzo and prezzo > 0) else None
        if qta is None:
            avvisi.append(p.ticker or p.nome_vista)
        righe.append({"id": p.id, "ticker": p.ticker, "nome": p.nome_vista,
                      "pct": p.pct_target, "euro": euro, "prezzo": prezzo,
                      "qta": qta, "fonte": fonte})
    return {"righe": righe, "totale": round(tot, 2), "n_inclusi": len(inclusi),
            "avvisi": avvisi, "data": data, "importo": round(importo, 2)}


DESCRIZIONE_MOVIMENTO = "Versamento PAC"


def _sync_finanze(vid: int, importo: float, data: date, conto: str,
                  ora: str = "") -> None:
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

    # il movimento porta l'ora del versamento, se l'hai indicata
    quando = datetime.combine(data, parse_ora(ora) or datetime.min.time())
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
          ora: str = "") -> int | None:
    """Registra un nuovo versamento (vid=None) o ne modifica uno esistente.
    Applica le quote alle posizioni (PMC) e memorizza i delta. Ritorna l'id."""
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
        v.ora = (ora or "").strip()[:5]
        db.flush()                                # per avere v.id
        for p in inclusi:
            euro = euros[p.id]
            prezzo, fonte = _prezzo_eur_alla_data(p, data, qmap, oggi, ora)
            qta = round(euro / prezzo, 6) if (prezzo and prezzo > 0) else None
            if qta is not None:
                p.quantita = round((p.quantita or 0) + qta, 8)
            else:                                  # prezzo n/d: tengo almeno il valore in €
                p.valore_posseduto = round((p.valore_posseduto or 0) + euro, 2)
            p.versato_totale = round((p.versato_totale or 0) + euro, 2)
            if p.data_ultimo_acquisto is None or data > p.data_ultimo_acquisto:
                p.data_ultimo_acquisto = data
            db.add(VersamentoRiga(versamento_id=v.id, position_id=p.id, isin=p.isin,
                                  ticker=p.ticker, euro=euro, qta=qta,
                                  prezzo_eur=prezzo, fonte=fonte))
        db.commit()
        nuovo_id = v.id
    _sync_finanze(nuovo_id, round(importo, 2), data, conto, ora)
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
        ids = [r.position_id for r in db.execute(
            select(VersamentoRiga).where(VersamentoRiga.versamento_id == vid)
        ).scalars().all()]
        return {"id": v.id, "data": v.data, "ora": v.ora or "", "importo": v.importo,
                "conto": v.conto, "inclusi_ids": set(ids)}


GIORNI_PREAVVISO = 2      # quanti giorni prima del solito iniziare a ricordarlo


def promemoria(oggi: date = None) -> dict | None:
    """«Il PAC di questo mese non l'hai ancora registrato», ma solo se è vero.

    Il giorno non è scritto nel codice: si ricava dalla MEDIANA dei versamenti
    già fatti. Se il PAC lo sposti, il promemoria si sposta con te — e se non
    hai mai versato niente, non c'è nessuna abitudine da ricordare e questa
    funzione tace (regola: mai inventare un'abitudine che non esiste)."""
    from statistics import median

    storico = lista()
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
    """Storico dei versamenti (più recenti in cima), con numero di titoli."""
    with SessionLocal() as db:
        vs = db.execute(select(Versamento).order_by(
            Versamento.data.desc(), Versamento.id.desc())).scalars().all()
        out = []
        for v in vs:
            n = db.execute(select(func.count()).select_from(VersamentoRiga)
                           .where(VersamentoRiga.versamento_id == v.id)).scalar()
            out.append({"id": v.id, "data": v.data, "ora": v.ora or "",
                        "importo": v.importo, "conto": v.conto, "n_titoli": n or 0})
        return out
