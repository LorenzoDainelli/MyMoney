"""Logica delle finanze personali: saldi, movimenti, trasferimenti, giri, sintesi.

Saldo di un wallet = saldo di apertura + entrate - uscite + trasferimenti in arrivo
- trasferimenti in uscita ± le gambe delle partite di giro. Il trasferimento non
cambia il patrimonio totale; la partita di giro lo cambia SOLO della differenza
netta (Σ rientri − Σ spese), che è anche l'unica cosa contata in entrate/uscite del
mese — e solo quando la partita è chiusa. Una partita può avere più spese e più
rientri: sono più righe con lo stesso `giro_id` (vedi finance/models.py).

Tutto DESCRITTIVO: qui l'inserimento e' manuale e strutturato (l'inserimento in
linguaggio naturale passa dall'agente AI, che però compila solo il modulo).
"""
import math
import uuid
from bisect import bisect_left
from datetime import datetime, timedelta

from sqlalchemy import func, select, text

from shared.db import SessionLocal, engine
from finance.models import (Wallet, Category, Transaction,
                            TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO, TIPO_GIRO)

# Viola ufficiale AIB (Pantone 520 C) — la "strisciolina" brand della card.
AIB_COLORE = "#632874"
# Blu ufficiale PayPal — "Pay Blue" (Pantone 295 C), fonte brandcolorcode.com
# (stessa di AIB). PayPal è un wallet digitale: categoria "carta".
PAYPAL_COLORE = "#00457C"

# Conti e carte REALI, mai generici (richiesta utente): nome -> (tipo, accento
# brand). I colori di Hype/Revolut/Trade Republic sono copiati 1:1 dal design
# (design_reference/data.js: accent '#12B3A6' / '#5B5BD6' / '#334155').
WALLET_BRAND = {
    "AIB": ("conto", AIB_COLORE),
    "Hype": ("carta", "#12B3A6"),
    "Revolut": ("carta", "#5B5BD6"),
    "Trade Republic": ("carta", "#334155"),
    "PayPal": ("carta", PAYPAL_COLORE),
}

# Wallet generici delle prime versioni, da togliere: via se mai usati,
# archiviati (dati preservati) se hanno movimenti o un saldo di apertura.
WALLET_GENERICI = ("Carta di credito", "Conto corrente")

# Saldi di APERTURA dei portafogli al 4 luglio 2026 (NON movimenti: sono il
# punto di partenza da cui il tracking accumula, al posto dello zero). Applicati
# ai wallet ancora a zero (vedi applica_saldi_iniziali); chi non è elencato = 0.
DATA_INIZIO = datetime(2026, 7, 4, 0, 0)
SALDI_INIZIALI = {
    "Hype": 91.98,
    "Contanti": 6.39,
    "AIB": 0.41,
    "Trade Republic": 0.0,
    "Revolut": 0.0,
    "PayPal": 0.0,
    "PAC investimenti": 0.0,
}

# Il portafoglio degli investimenti: unico conto a SALDO DERIVATO (il suo valore
# arriva dal Portafoglio, non dalla somma dei movimenti). Vedi valore_pac_live().
NOME_WALLET_PAC = "PAC investimenti"

# Il salvadanaio della carta: ci si accumulano gli arrotondamenti e il saveback
# fino a quando la banca non li investe (inizio del mese dopo). Sono soldi TUOI e
# contano nel patrimonio, ma non li puoi spendere: restano fuori dai 'liquidi'.
# Grigio perché si veda a occhio che è una tasca chiusa.
NOME_WALLET_NASCOSTI = "Nascosti"
NASCOSTI_COLORE = "#6E7681"

# Portafogli precaricati la prima volta (li puoi rinominare/eliminare).
SEED_WALLETS = [
    ("Contanti", "contanti", ""),
    ("Hype", "carta", WALLET_BRAND["Hype"][1]),
    ("Revolut", "carta", WALLET_BRAND["Revolut"][1]),
    ("Trade Republic", "carta", WALLET_BRAND["Trade Republic"][1]),
    ("PayPal", "carta", PAYPAL_COLORE),
    ("AIB", "conto", AIB_COLORE),
    (NOME_WALLET_NASCOSTI, "altro", NASCOSTI_COLORE),
    ("PAC investimenti", "investimento", ""),
]

# Regole della carta Trade Republic, VERIFICATE dall'utente il 30/07/2026 (non
# dedotte): arrotondamento sempre al prossimo euro, saveback 1% troncato ai
# centesimi con un tetto di 15 € al mese. Sono valori di partenza: restano
# modificabili sul portafoglio e correggibili su ogni singolo movimento.
CARTA_ARROTONDA = {"Trade Republic": {"saveback_pct": 1.0, "saveback_tetto": 15.0}}


def migra_schema():
    """Colonne aggiunte dopo la prima release: create_all non altera le tabelle
    esistenti, quindi le aggiungiamo qui (idempotente, SQLite)."""
    with engine.connect() as c:
        cols = [r[1] for r in c.execute(text("PRAGMA table_info(finance_wallets)"))]
        if cols and "colore" not in cols:
            c.execute(text("ALTER TABLE finance_wallets ADD COLUMN colore VARCHAR(20) DEFAULT ''"))
            c.commit()
        # carta con arrotondamento e saveback (Trade Republic)
        for nome, ddl in (("arrotonda", "BOOLEAN DEFAULT 0"),
                          ("saveback_pct", "FLOAT DEFAULT 0.0"),
                          ("saveback_tetto", "FLOAT DEFAULT 0.0")):
            if cols and nome not in cols:
                c.execute(text(f"ALTER TABLE finance_wallets ADD COLUMN {nome} {ddl}"))
                c.commit()
        # partite di giro: gambe (spesa/rientro) e raggruppamento in una partita
        # + righe generate (arrotondamento/saveback) legate al movimento padre
        cols = [r[1] for r in c.execute(text("PRAGMA table_info(finance_transactions)"))]
        for nome, ddl in (("importo_ricevuto", "FLOAT"),
                          ("data_ricevuto", "DATETIME"),
                          ("controparte", "VARCHAR(80) DEFAULT ''"),
                          ("giro_id", "VARCHAR(32) DEFAULT ''"),
                          ("giro_aperta", "BOOLEAN DEFAULT 0"),
                          ("parent_tx_id", "INTEGER"),
                          ("origine", "VARCHAR(20) DEFAULT ''")):
            if cols and nome not in cols:
                c.execute(text(f"ALTER TABLE finance_transactions ADD COLUMN {nome} {ddl}"))
                c.commit()
        # sync v2 (multi-dispositivo): identità e versione di ogni record + tombstone
        for tabella in ("finance_wallets", "finance_categories", "finance_transactions"):
            cols = [r[1] for r in c.execute(text(f"PRAGMA table_info({tabella})"))]
            for nome, ddl in (("uid", "VARCHAR(32) DEFAULT ''"),
                              ("updated_at", "DATETIME"),
                              ("rev", "INTEGER DEFAULT 1"),
                              ("deleted", "BOOLEAN DEFAULT 0")):
                if cols and nome not in cols:
                    c.execute(text(f"ALTER TABLE {tabella} ADD COLUMN {nome} {ddl}"))
                    c.commit()
            c.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{tabella}_uid ON {tabella}(uid)"))
        c.commit()
    # Backfill idempotenti: metadati di sync ai record pre-v2, e giro_id alle
    # vecchie partite a riga singola (l'apertura dalla vecchia regola: rimborso
    # assente = aperta). Toccano solo le righe non ancora sistemate.
    _backfill_meta_sync()
    _backfill_giro_id()


def _backfill_meta_sync() -> None:
    """Assegna uid e updated_at ai record creati prima della v2 (uid = un id unico
    per riga; updated_at = created_at dove c'è, altrimenti ora). Raw SQL per non
    passare dall'ORM (niente rev++ inutile). Idempotente."""
    now = datetime.now()
    with engine.begin() as c:
        c.execute(text("UPDATE finance_transactions SET updated_at=created_at "
                       "WHERE updated_at IS NULL AND created_at IS NOT NULL"))
        for tabella in ("finance_wallets", "finance_categories", "finance_transactions"):
            for (rid,) in c.execute(text(
                    f"SELECT id FROM {tabella} WHERE uid IS NULL OR uid=''")).fetchall():
                c.execute(text(f"UPDATE {tabella} SET uid=:u WHERE id=:i"),
                          {"u": uuid.uuid4().hex, "i": rid})
            c.execute(text(f"UPDATE {tabella} SET updated_at=:n WHERE updated_at IS NULL"), {"n": now})
            c.execute(text(f"UPDATE {tabella} SET rev=1 WHERE rev IS NULL"))
            c.execute(text(f"UPDATE {tabella} SET deleted=0 WHERE deleted IS NULL"))


def _backfill_giro_id() -> None:
    with SessionLocal() as db:
        legacy = db.query(Transaction).filter(
            Transaction.tipo == TIPO_GIRO,
            (Transaction.giro_id.is_(None)) | (Transaction.giro_id == "")).all()
        for t in legacy:
            t.giro_id = uuid.uuid4().hex[:16]
            t.giro_aperta = t.importo_ricevuto is None
        if legacy:
            db.commit()


def compatta_tombstone(giorni: int = 365) -> int:
    """Elimina fisicamente le TRANSAZIONI tombstone (deleted=True) con
    updated_at più vecchio di `giorni`. Ritorna quante ne ha rimosse.
    Sicuro per 2 dispositivi che sincronizzano entro l'anno (la cancellazione
    è già stata propagata). Non tocca wallet/categorie."""
    from shared import sync
    soglia = datetime.now() - timedelta(days=giorni)
    with SessionLocal() as db:
        with sync.importing():
            res = db.query(Transaction).filter(
                Transaction.deleted.is_(True),
                Transaction.updated_at < soglia
            ).delete()
            db.commit()
            return res


def seed_wallets_if_empty() -> int:
    with SessionLocal() as db:
        if db.query(Wallet).first() is not None:
            return 0
        for i, (nome, tipo, colore) in enumerate(SEED_WALLETS):
            db.add(Wallet(nome=nome, tipo=tipo,
                          saldo_iniziale=SALDI_INIZIALI.get(nome, 0.0),
                          ordine=i, colore=colore))
        db.commit()
        return len(SEED_WALLETS)


def applica_saldi_iniziali() -> None:
    """Imposta i saldi di APERTURA (al 4/7/2026, vedi SALDI_INIZIALI) come valori
    di partenza dei portafogli, SOLO dove sono ancora a zero: sono i saldi
    predefiniti, non movimenti. Non tocca mai un saldo di apertura già impostato,
    così non sovrascrive eventuali correzioni."""
    with SessionLocal() as db:
        for w in db.query(Wallet).all():
            atteso = SALDI_INIZIALI.get((w.nome or "").strip())
            if atteso and not (w.saldo_iniziale or 0.0):
                w.saldo_iniziale = atteso
        db.commit()


def assicura_wallet_brand() -> None:
    """Allinea i portafogli ai conti/carte REALI anche su un DB già popolato:
    crea quelli brand che mancano (AIB, Hype, Revolut, Trade Republic, con il
    loro accento), completa il colore se assente e toglie i generici 'Carta di
    credito' / 'Conto corrente' (eliminati se mai usati, altrimenti archiviati
    così nessun movimento va perso)."""
    with SessionLocal() as db:
        per_nome = {(w.nome or "").strip().lower(): w for w in db.query(Wallet).all()}
        ultimo = db.query(Wallet).order_by(Wallet.ordine.desc()).first()
        ordine = (ultimo.ordine + 1) if ultimo else 0
        for nome, (tipo, colore) in WALLET_BRAND.items():
            w = per_nome.get(nome.lower())
            if w is None:
                db.add(Wallet(nome=nome, tipo=tipo,
                              saldo_iniziale=SALDI_INIZIALI.get(nome, 0.0),
                              ordine=ordine, colore=colore))
                ordine += 1
            elif not (w.colore or "").strip():
                w.colore = colore
        for nome in WALLET_GENERICI:
            w = per_nome.get(nome.lower())
            if w is None or w.archiviato:
                continue
            usato = db.query(Transaction).filter(
                (Transaction.wallet_id == w.id) | (Transaction.wallet_to_id == w.id)).first()
            if usato or (w.saldo_iniziale or 0.0):
                w.archiviato = True
            else:
                db.delete(w)
        db.commit()


def assicura_salvadanaio() -> None:
    """Crea il portafoglio «Nascosti» se manca e accende arrotondamento e saveback
    sulle carte che li hanno (oggi solo Trade Republic), anche su un DB già
    popolato.

    Le regole si scrivono una volta sola, alla creazione: se poi le spegni o le
    correggi dalla scheda del portafoglio, questa funzione non te le rimette —
    altrimenti a ogni avvio l'app disfarebbe la tua scelta. Il segno di 'già
    configurato' è avere una delle due impostate."""
    with SessionLocal() as db:
        per_nome = {(w.nome or "").strip().lower(): w for w in db.query(Wallet).all()}
        if NOME_WALLET_NASCOSTI.lower() not in per_nome:
            ultimo = db.query(Wallet).order_by(Wallet.ordine.desc()).first()
            db.add(Wallet(nome=NOME_WALLET_NASCOSTI, tipo="altro", saldo_iniziale=0.0,
                          ordine=(ultimo.ordine + 1) if ultimo else 0,
                          colore=NASCOSTI_COLORE,
                          note="Arrotondamenti e saveback della carta, in attesa "
                               "che la banca li investa."))
        for nome, regole in CARTA_ARROTONDA.items():
            w = per_nome.get(nome.lower())
            if w is None or w.arrotonda or (w.saveback_pct or 0.0):
                continue                       # assente o già configurato: non tocco
            w.arrotonda = True
            w.saveback_pct = regole["saveback_pct"]
            w.saveback_tetto = regole["saveback_tetto"]
        db.commit()


# ------------------------------ wallet ------------------------------
def wallets(include_archived: bool = False):
    with SessionLocal() as db:
        q = select(Wallet).where(Wallet.deleted.is_(False)).order_by(Wallet.ordine, Wallet.id)
        if not include_archived:
            q = q.where(Wallet.archiviato.is_(False))
        return list(db.execute(q).scalars().all())


def _saldi_map(db) -> dict:
    """Saldo di ogni wallet, calcolato con poche query aggregate.
    Le partite di giro muovono i saldi con le loro DUE gambe reali: la spesa
    esce da wallet_id, il rimborso (quando c'è) entra in wallet_to_id.
    I record con deleted=True (tombstone sync) NON contribuiscono ai saldi."""
    saldi = {w.id: w.saldo_iniziale for w in db.query(Wallet).all()}

    def add(query, sign, key="wallet_id"):
        for wid, tot in query:
            if wid in saldi and tot:
                saldi[wid] += sign * tot

    T = Transaction
    _alive = T.deleted.is_(False)
    add(db.query(T.wallet_id, func.sum(T.importo)).filter(T.tipo == TIPO_ENTRATA, _alive).group_by(T.wallet_id), +1)
    add(db.query(T.wallet_id, func.sum(T.importo)).filter(T.tipo == TIPO_USCITA, _alive).group_by(T.wallet_id), -1)
    add(db.query(T.wallet_id, func.sum(T.importo)).filter(T.tipo == TIPO_TRASFERIMENTO, _alive).group_by(T.wallet_id), -1)
    add(db.query(T.wallet_to_id, func.sum(T.importo)).filter(T.tipo == TIPO_TRASFERIMENTO, _alive).group_by(T.wallet_to_id), +1)
    add(db.query(T.wallet_id, func.sum(T.importo)).filter(T.tipo == TIPO_GIRO, _alive).group_by(T.wallet_id), -1)
    add(db.query(T.wallet_to_id, func.sum(T.importo_ricevuto)).filter(
        T.tipo == TIPO_GIRO, T.importo_ricevuto.isnot(None), _alive).group_by(T.wallet_to_id), +1)
    return saldi


def wallet_per_nome(nome: str, include_archived: bool = True):
    """Wallet con questo nome (confronto senza maiuscole/spazi). None se non c'è."""
    key = (nome or "").strip().lower()
    if not key:
        return None
    for w in wallets(include_archived=include_archived):
        if (w.nome or "").strip().lower() == key:
            return w
    return None


def valore_pac_live() -> dict | None:
    """Il conto PAC non è un conto normale: il suo valore NON è la somma dei
    movimenti, è quello VIVO del Portafoglio (oscilla col mercato).

    Regola: il versamento è un movimento reale, l'oscillazione NON lo è mai —
    la rivalutazione è calcolata qui al volo, così lo storico movimenti resta
    pulito (una riga per PAC) e i totali entrate/uscite non vengono falsati.

    Ritorna {'valore', 'versato', 'rivalutazione'} oppure None se il Portafoglio
    non ha ancora prezzi: in quel caso il saldo resta quello dei movimenti,
    niente numeri inventati."""
    try:
        from portfolio import service as pf_service
        vista = pf_service.vista_portafoglio()
    except Exception:
        return None
    if not vista.get("ha_totale"):
        return None
    versato = round(sum((r["p"].versato_totale or 0.0) for r in vista["righe"]), 2)
    valore = round(vista["totale"], 2)
    return {"valore": valore, "versato": versato,
            "rivalutazione": round(valore - versato, 2)}


def saldi():
    """Lista (wallet, saldo) per i wallet attivi, piu' il patrimonio totale.
    Ordine delle card: saldo decrescente, ma il PAC (tipo 'investimento')
    resta SEMPRE per ultimo, come richiesto.
    Il conto PAC porta il valore vivo del Portafoglio (vedi valore_pac_live)."""
    with SessionLocal() as db:
        smap = _saldi_map(db)
        ws = list(db.execute(
            select(Wallet).where(Wallet.archiviato.is_(False), Wallet.deleted.is_(False)).order_by(Wallet.ordine, Wallet.id)
        ).scalars().all())
    righe = [{"w": w, "saldo": round(smap.get(w.id, 0.0), 2)} for w in ws]
    pac = valore_pac_live()
    for r in righe:
        nome = (r["w"].nome or "").strip().lower()
        if pac and nome == NOME_WALLET_PAC.lower():
            r["saldo"] = pac["valore"]
            r["versato"] = pac["versato"]
            r["rivalutazione"] = pac["rivalutazione"]
            r["derivato"] = True
        elif nome == NOME_WALLET_NASCOSTI.lower():
            r["bloccato"] = True
    righe.sort(key=lambda r: (r["w"].tipo == "investimento", -r["saldo"]))
    totale = round(sum(r["saldo"] for r in righe), 2)
    # Due esclusioni diverse, e confonderle è già costato un bug:
    # - 'derivato' (il PAC) è FUORI dal patrimonio calcolato qui, perché quei soldi
    #   tornano dal Portafoglio come valore dei titoli: contarli sarebbe doppio;
    # - 'bloccato' (i Nascosti) è DENTRO il patrimonio — sono soldi tuoi — ma fuori
    #   dai liquidi, perché finché la banca non compra non li puoi spendere.
    liquido = round(sum(r["saldo"] for r in righe
                        if not r.get("derivato") and not r.get("bloccato")), 2)
    bloccato = round(sum(r["saldo"] for r in righe if r.get("bloccato")), 2)
    return {"righe": righe, "totale": totale, "liquido": liquido,
            "bloccato": bloccato}


# ------------------------------ categorie ------------------------------
def categorie(include_archived: bool = False):
    with SessionLocal() as db:
        q = select(Category).where(Category.deleted.is_(False)).order_by(Category.nome)
        if not include_archived:
            q = q.where(Category.archiviato.is_(False))
        return list(db.execute(q).scalars().all())


def _get_or_create_categoria(db, nome, kind=""):
    nome = (nome or "").strip()
    if not nome:
        return None
    # riusa una categoria esistente con lo stesso nome (niente doppioni)
    ex = db.query(Category).filter(func.lower(Category.nome) == nome.lower(),
                                   Category.archiviato.is_(False),
                                   Category.deleted.is_(False)).first()
    if ex:
        return ex.id
    c = Category(nome=nome, kind=kind)
    db.add(c)
    db.flush()
    return c.id


# ------------------------------ movimenti ------------------------------
def crea_movimento(tipo, data, importo, wallet_id, wallet_to_id=None,
                   categoria_nome="", descrizione="", parent_tx_id=None, origine=""):
    """Crea un movimento. Ritorna l'id del record creato."""
    with SessionLocal() as db:
        cat_id = None
        if tipo in (TIPO_ENTRATA, TIPO_USCITA):
            cat_id = _get_or_create_categoria(
                db, categoria_nome, "entrata" if tipo == TIPO_ENTRATA else "uscita")
        t = Transaction(
            tipo=tipo, data=data or datetime.now(), importo=abs(importo or 0.0),
            wallet_id=wallet_id,
            wallet_to_id=wallet_to_id if tipo == TIPO_TRASFERIMENTO else None,
            category_id=cat_id if tipo != TIPO_TRASFERIMENTO else None,
            descrizione=descrizione.strip(),
            parent_tx_id=parent_tx_id, origine=origine)
        db.add(t)
        db.commit()
        return t.id


def elimina_movimento(tid):
    """Soft-delete di un movimento (Fase 4: il tombstone viaggia nel sync).
    Se è la gamba di una partita di giro, marca tutta la partita come deleted.
    Se ha righe generate (arrotondamento, saveback) se ne vanno con lui: da sole
    non vorrebbero dire niente, e resterebbero invisibili nel registro."""
    with SessionLocal() as db:
        t = db.get(Transaction, tid)
        if not t or t.deleted:
            return
        if t.tipo == TIPO_GIRO and (t.giro_id or ""):
            gambe = db.query(Transaction).filter(Transaction.giro_id == t.giro_id).all()
            padri = [r.id for r in gambe]
            for r in gambe:
                r.deleted = True
        else:
            t.deleted = True
            padri = [t.id]
        for f in db.query(Transaction).filter(Transaction.parent_tx_id.in_(padri)).all():
            f.deleted = True
        db.commit()


def aggiorna_movimento(tid, tipo, data, importo, wallet_id, wallet_to_id=None,
                       categoria_nome="", descrizione=""):
    """Modifica IN-PLACE un movimento normale (entrata/uscita/trasferimento):
    stesso record (uid invariato → il sync lo vede come aggiornamento, rev++).
    Non tocca le partite di giro (quelle passano da `aggiorna_giro`)."""
    with SessionLocal() as db:
        t = db.get(Transaction, tid)
        if not t or t.deleted or t.tipo == TIPO_GIRO:
            return False
        cat_id = None
        if tipo in (TIPO_ENTRATA, TIPO_USCITA):
            cat_id = _get_or_create_categoria(
                db, categoria_nome, "entrata" if tipo == TIPO_ENTRATA else "uscita")
        t.tipo = tipo
        t.data = data or t.data or datetime.now()
        t.importo = abs(importo or 0.0)
        t.wallet_id = wallet_id
        t.wallet_to_id = wallet_to_id if tipo == TIPO_TRASFERIMENTO else None
        t.category_id = cat_id if tipo != TIPO_TRASFERIMENTO else None
        t.descrizione = (descrizione or "").strip()
        db.commit()
        return True


# ------------------- carta con arrotondamento e saveback -------------------
# Una spesa con la carta Trade Republic è UN gesto ma TRE fatti diversi, e
# tenerli distinti è tutto il punto:
#   7,60 € alla Coop      -> USCITA          (consumo: non sono più tuoi)
#   0,40 € di arrotondamento -> TRASFERIMENTO (tuoi, cambiano tasca)
#   0,07 € di saveback    -> ENTRATA         (della banca, prima non c'erano)
# Segnare 8,00 € di spesa falserebbe i consumi; segnarne 7,60 lascerebbe il conto
# scoperto di 40 centesimi. Le tre righe fanno tornare tutte e due le cose.
NOME_CATEGORIA_SAVEBACK = "Saveback"
ORIGINE_ARROTONDAMENTO = "arrotondamento"
ORIGINE_SAVEBACK = "saveback"


def arrotondamento(importo: float) -> float:
    """Quanto manca al PROSSIMO euro. Verificato sulla carta il 30/07/2026: anche
    una cifra tonda sale (8,00 € -> 9,00 €), quindi NON è math.ceil, che su 8,00
    non farebbe niente."""
    if not importo or importo <= 0:
        return 0.0
    return round(math.floor(importo) + 1 - importo, 2)


def saveback_dovuto(importo: float, pct: float, tetto: float = 0.0,
                    gia_maturato: float = 0.0) -> float:
    """L'1% della spesa TRONCATO ai centesimi, entro quel che resta del tetto.

    Troncato e non arrotondato: su 7,60 € l'1% fa 0,076 e la banca ne accredita
    0,07, non 0,08 (unica prova che abbiamo, ed esclude l'arrotondamento normale).
    Se il tetto del mese è pieno torna 0: meglio zero che un centesimo che la
    banca non darà. Resta un valore PROPOSTO, sempre correggibile."""
    if not importo or importo <= 0 or not pct or pct <= 0:
        return 0.0
    # importo(€) × pct(%) = centesimi: 7,60 × 1 = 7,6 -> 7 centesimi
    cent = math.floor(round(importo * pct, 6))
    val = round(cent / 100.0, 2)
    if tetto and tetto > 0:
        val = min(val, max(0.0, round(tetto - (gia_maturato or 0.0), 2)))
    return max(0.0, val)


def saveback_maturato(anno: int, mese: int) -> float:
    """Quanto saveback è già maturato nel mese (per fermarsi al tetto)."""
    start, end = _range_mese(anno, mese)
    with SessionLocal() as db:
        return round(db.query(func.coalesce(func.sum(Transaction.importo), 0.0)).filter(
            Transaction.origine == ORIGINE_SAVEBACK,
            Transaction.deleted.is_(False),
            Transaction.data >= start, Transaction.data < end).scalar() or 0.0, 2)


def regole_carta(wallet_id) -> dict | None:
    """Le regole della carta, se quel portafoglio ne ha. None altrimenti."""
    if not wallet_id:
        return None
    with SessionLocal() as db:
        w = db.get(Wallet, int(wallet_id))
        if w is None or not (w.arrotonda or (w.saveback_pct or 0.0)):
            return None
        return {"arrotonda": bool(w.arrotonda),
                "saveback_pct": w.saveback_pct or 0.0,
                "saveback_tetto": w.saveback_tetto or 0.0}


def extra_carta(wallet_id, importo: float, data=None, escludi_tx=None,
                gia_extra: float = 0.0) -> dict:
    """Arrotondamento e saveback PROPOSTI per una spesa. Sempre un dict, così chi
    chiama non deve distinguere i casi: a zero quando non c'è niente da fare.

    `gia_extra` = saveback già assegnato in questa stessa operazione ma non
    ancora scritto: serve alle partite di giro, dove più spese nascono insieme e
    consumano lo stesso tetto mensile."""
    vuoto = {"arrotondamento": 0.0, "saveback": 0.0, "tetto_pieno": False}
    r = regole_carta(wallet_id)
    if not r or not importo or importo <= 0:
        return vuoto
    quando = data or datetime.now()
    gia = round(saveback_maturato(quando.year, quando.month) + (gia_extra or 0.0), 2)
    if escludi_tx:                      # in modifica: il vecchio saveback non conta
        gia = round(gia - _importo_figlia(escludi_tx, ORIGINE_SAVEBACK), 2)
    sb = saveback_dovuto(importo, r["saveback_pct"], r["saveback_tetto"], gia)
    return {
        "arrotondamento": arrotondamento(importo) if r["arrotonda"] else 0.0,
        "saveback": sb,
        "tetto_pieno": bool(r["saveback_tetto"] and sb <= 0 and r["saveback_pct"]),
    }


def importo_movimento(tid: int) -> float:
    """L'importo attuale di un movimento (per confrontarlo con quello nuovo)."""
    with SessionLocal() as db:
        t = db.get(Transaction, tid)
        return round(t.importo or 0.0, 2) if t else 0.0


def _importo_figlia(parent_id: int, origine: str) -> float:
    with SessionLocal() as db:
        t = db.query(Transaction).filter(
            Transaction.parent_tx_id == parent_id, Transaction.origine == origine,
            Transaction.deleted.is_(False)).first()
        return round(t.importo or 0.0, 2) if t else 0.0


def movimento(tid: int) -> dict | None:
    """Un movimento con i nomi già risolti e le sue righe generate, nella stessa
    forma delle righe di `lista_movimenti` (così il template è uno solo)."""
    with SessionLocal() as db:
        t = db.get(Transaction, tid)
        if t is None or t.deleted:
            return None
        wn = {w.id: w.nome for w in db.query(Wallet).all()}
        cn = {c.id: c.nome for c in db.query(Category).all()}
        gen = [{"t": f, "wallet": wn.get(f.wallet_id, "—"),
                "wallet_to": wn.get(f.wallet_to_id) if f.wallet_to_id else None}
               for f in db.query(Transaction).filter(
                   Transaction.parent_tx_id == tid,
                   Transaction.deleted.is_(False)).order_by(Transaction.id).all()]
        return {
            "t": t,
            "wallet": wn.get(t.wallet_id, "—"),
            "wallet_to": wn.get(t.wallet_to_id) if t.wallet_to_id else None,
            "categoria": cn.get(t.category_id) if t.category_id else None,
            "figlie": gen,
            "addebito": round((t.importo or 0.0) + sum(
                f["t"].importo for f in gen
                if f["t"].origine == ORIGINE_ARROTONDAMENTO), 2),
        }


def figlie(parent_id: int) -> list:
    """Le righe generate da un movimento (arrotondamento, saveback)."""
    with SessionLocal() as db:
        return list(db.query(Transaction).filter(
            Transaction.parent_tx_id == parent_id,
            Transaction.deleted.is_(False)).order_by(Transaction.id).all())


def _crea_figlie(db, parent: Transaction, arr: float, sav: float) -> None:
    """Le due righe che accompagnano la spesa. Il salvadanaio deve esistere: se
    l'hai cancellato non si inventa un portafoglio, semplicemente non si scrive
    niente — meglio nessuna riga che una riga in un posto sbagliato."""
    dest = db.query(Wallet).filter(Wallet.deleted.is_(False)).filter(
        func.lower(func.trim(Wallet.nome)) == NOME_WALLET_NASCOSTI.lower()).first()
    if dest is None:
        return
    if arr and arr > 0:
        db.add(Transaction(
            tipo=TIPO_TRASFERIMENTO, data=parent.data, importo=round(arr, 2),
            wallet_id=parent.wallet_id, wallet_to_id=dest.id,
            descrizione=NOME_WALLET_NASCOSTI, parent_tx_id=parent.id,
            origine=ORIGINE_ARROTONDAMENTO))
    if sav and sav > 0:
        db.add(Transaction(
            tipo=TIPO_ENTRATA, data=parent.data, importo=round(sav, 2),
            wallet_id=dest.id,
            category_id=_get_or_create_categoria(db, NOME_CATEGORIA_SAVEBACK, "entrata"),
            descrizione=NOME_CATEGORIA_SAVEBACK, parent_tx_id=parent.id,
            origine=ORIGINE_SAVEBACK))


def crea_uscita_carta(data, importo, wallet_id, categoria_nome="", descrizione="",
                      arr=None, sav=None) -> int:
    """Una spesa con la carta e le sue due righe. `arr`/`sav` a None = li calcola
    l'app; un numero (anche 0) = l'hai deciso tu e vince sul calcolo."""
    quando = data or datetime.now()
    prop = extra_carta(wallet_id, importo, quando)
    arr = prop["arrotondamento"] if arr is None else max(0.0, round(arr, 2))
    sav = prop["saveback"] if sav is None else max(0.0, round(sav, 2))
    with SessionLocal() as db:
        t = Transaction(
            tipo=TIPO_USCITA, data=quando, importo=abs(importo or 0.0),
            wallet_id=wallet_id,
            category_id=_get_or_create_categoria(db, categoria_nome, "uscita"),
            descrizione=(descrizione or "").strip())
        db.add(t)
        db.flush()                                  # serve l'id per le figlie
        _crea_figlie(db, t, arr, sav)
        db.commit()
        return t.id


def imposta_figlie(tid: int, arr: float, sav: float) -> None:
    """Porta le righe generate ESATTAMENTE a questi importi: le crea se mancano,
    le aggiorna se ci sono, le toglie se metti zero.

    È la strada della modifica: lì i due numeri li hai davanti nel modulo, quindi
    quello che vedi è quello che vale — nessuna euristica, nessuna sorpresa."""
    with SessionLocal() as db:
        t = db.get(Transaction, tid)
        if t is None or t.deleted or t.tipo != TIPO_USCITA:
            return
        vive = {f.origine: f for f in db.query(Transaction).filter(
            Transaction.parent_tx_id == tid, Transaction.deleted.is_(False)).all()}
        mancanti_arr = mancanti_sav = 0.0
        for origine, valore in ((ORIGINE_ARROTONDAMENTO, round(max(0.0, arr or 0.0), 2)),
                                (ORIGINE_SAVEBACK, round(max(0.0, sav or 0.0), 2))):
            f = vive.get(origine)
            if f is None:
                if origine == ORIGINE_ARROTONDAMENTO:
                    mancanti_arr = valore
                else:
                    mancanti_sav = valore
                continue
            if valore <= 0:
                f.deleted = True
            else:
                f.importo, f.data = valore, t.data
        if mancanti_arr or mancanti_sav:
            _crea_figlie(db, t, mancanti_arr, mancanti_sav)
        db.commit()


def risincronizza_figlie(tid: int, importo_prima: float) -> None:
    """Dopo la modifica di una spesa, rifà i conti delle sue due righe — ma solo
    se erano quelle calcolate dall'app. Un importo che hai corretto a mano resta
    tuo: riscriverlo sarebbe disfare una decisione che hai già preso."""
    with SessionLocal() as db:
        t = db.get(Transaction, tid)
        if t is None or t.deleted or t.tipo != TIPO_USCITA:
            return
        vecchie = {f.origine: f for f in db.query(Transaction).filter(
            Transaction.parent_tx_id == tid, Transaction.deleted.is_(False)).all()}
        if not vecchie and not regole_carta(t.wallet_id):
            return
        prima = extra_carta(t.wallet_id, importo_prima, t.data, escludi_tx=tid)
        dopo = extra_carta(t.wallet_id, t.importo, t.data, escludi_tx=tid)
        for origine, chiave in ((ORIGINE_ARROTONDAMENTO, "arrotondamento"),
                                (ORIGINE_SAVEBACK, "saveback")):
            f = vecchie.get(origine)
            if f is not None and round(f.importo or 0.0, 2) != prima[chiave]:
                continue                    # corretta a mano: non la tocco
            if f is None:
                continue                    # non c'era: non la creo dal nulla
            f.data = t.data
            if dopo[chiave] > 0:
                f.importo = dopo[chiave]
            else:
                f.deleted = True            # l'importo nuovo non la giustifica più
        db.commit()


# ------------------------------ partite di giro ------------------------------
def _nuovo_giro_id() -> str:
    import uuid
    return uuid.uuid4().hex[:16]


def _riga_spesa(db, gid, aperta, importo, wallet_id, categoria="", descrizione="", data=None):
    cat_id = _get_or_create_categoria(db, categoria, "")   # categoria come etichetta (kind neutro)
    return Transaction(
        tipo=TIPO_GIRO, giro_id=gid, giro_aperta=aperta,
        data=data or datetime.now(), importo=abs(importo or 0.0),
        wallet_id=wallet_id, category_id=cat_id,
        descrizione=(descrizione or "").strip())


def _riga_rientro(db, gid, aperta, importo, wallet_id, controparte="", data=None):
    # la gamba rientro: importo=0 (non è una spesa), il denaro ENTRA in wallet_to_id
    return Transaction(
        tipo=TIPO_GIRO, giro_id=gid, giro_aperta=aperta,
        data=data or datetime.now(), importo=0.0,
        wallet_id=wallet_id, wallet_to_id=wallet_id,
        importo_ricevuto=abs(importo or 0.0),
        data_ricevuto=data or datetime.now(),
        controparte=(controparte or "").strip())


def _figlie_delle_gambe(db, gambe) -> None:
    """Arrotondamento e saveback su ogni gamba SPESA pagata con una carta che li
    ha. Una spesa da rimborsare resta una spesa fatta con la carta: la banca
    arrotonda lo stesso, e quei soldi finiscono nel salvadanaio anche se poi
    qualcuno ti restituisce l'importo. Il rimborso riguarda la spesa, non
    l'arrotondamento.

    `gambe` = lista di (riga già flushata, arr, sav) con arr/sav None = calcolali."""
    consumato = {}          # (anno, mese) -> saveback assegnato qui dentro
    for riga, arr, sav in gambe:
        k = (riga.data.year, riga.data.month)
        prop = extra_carta(riga.wallet_id, riga.importo, riga.data,
                           gia_extra=consumato.get(k, 0.0))
        a = prop["arrotondamento"] if arr is None else max(0.0, round(arr, 2))
        s = prop["saveback"] if sav is None else max(0.0, round(sav, 2))
        consumato[k] = round(consumato.get(k, 0.0) + s, 2)
        if a or s:
            _crea_figlie(db, riga, a, s)


def crea_giro(spese, rientri=None, aperta=False):
    """Registra una partita di giro con PIÙ spese e PIÙ rientri (una sola partita).
    - spese:   lista di dict {importo, wallet_id, categoria, descrizione, data,
               arr, sav}  (arr/sav assenti o None = li calcola l'app)
    - rientri: lista di dict {importo, wallet_id, controparte, data}
    Con `aperta=True` (casella 'il rimborso arriverà dopo') gli eventuali rientri
    passati vengono IGNORATI: la partita resta in attesa. I saldi dei portafogli
    si muovono comunque, gamba per gamba; il netto conta solo quando è chiusa."""
    spese = [s for s in (spese or []) if (s.get("importo") or 0) > 0 and s.get("wallet_id")]
    rientri = [] if aperta else [r for r in (rientri or [])
                                 if (r.get("importo") or 0) > 0 and r.get("wallet_id")]
    if not spese:
        return None
    gid = _nuovo_giro_id()
    with SessionLocal() as db:
        gambe = []
        for s in spese:
            riga = _riga_spesa(db, gid, aperta, s.get("importo"), s["wallet_id"],
                               s.get("categoria", ""), s.get("descrizione", ""), s.get("data"))
            db.add(riga)
            gambe.append((riga, s.get("arr"), s.get("sav")))
        for r in rientri:
            db.add(_riga_rientro(db, gid, aperta, r.get("importo"), r["wallet_id"],
                                 r.get("controparte", ""), r.get("data")))
        db.flush()                       # servono gli id delle gambe
        _figlie_delle_gambe(db, gambe)
        db.commit()
    return gid


def aggiungi_rientro(giro_id, importo, wallet_id, controparte="", data=None):
    """Aggiunge un rientro (rimborso) a una partita esistente, lasciandola com'è
    (aperta o chiusa). Serve per i rimborsi arrivati in più volte."""
    if not giro_id or (importo or 0) <= 0 or not wallet_id:
        return False
    with SessionLocal() as db:
        altra = db.query(Transaction).filter(Transaction.giro_id == giro_id).first()
        if not altra or altra.tipo != TIPO_GIRO:
            return False
        db.add(_riga_rientro(db, giro_id, altra.giro_aperta, importo, wallet_id, controparte, data))
        db.commit()
        return True


def chiudi_giro(giro_id, importo=None, wallet_id=None, controparte="", data=None):
    """Chiude una partita: da qui il netto (Σ rientri − Σ spese) entra nelle
    statistiche. Se vengono passati importo+wallet_id, registra prima un ultimo
    rientro (comodo dal riquadro 'In attesa'). Accetta anche l'id di UNA riga."""
    with SessionLocal() as db:
        rows = db.query(Transaction).filter(Transaction.giro_id == giro_id).all()
        if not rows:                       # ripiego: giro_id passato come id di riga
            t = db.get(Transaction, giro_id) if str(giro_id).isdigit() else None
            if t and t.giro_id:
                rows = db.query(Transaction).filter(Transaction.giro_id == t.giro_id).all()
        if not rows:
            return False
        gid = rows[0].giro_id
        if importo and wallet_id:
            db.add(_riga_rientro(db, gid, False, importo, wallet_id, controparte, data))
        for r in rows:
            r.giro_aperta = False
        db.commit()
        return True


def converti_giro_in_uscita(giro_id):
    """'Non me li ridaranno': le spese della partita diventano normali uscite,
    gli eventuali rientri già registrati vengono rimossi. Accetta anche l'id di
    una riga della partita."""
    with SessionLocal() as db:
        rows = db.query(Transaction).filter(Transaction.giro_id == giro_id).all()
        if not rows:
            t = db.get(Transaction, giro_id) if str(giro_id).isdigit() else None
            if t and t.giro_id:
                rows = db.query(Transaction).filter(Transaction.giro_id == t.giro_id).all()
        if not rows:
            return False
        for r in rows:
            if r.giro_kind == "rientro":
                r.deleted = True
            else:                          # spesa o combo -> uscita normale
                r.tipo = TIPO_USCITA
                r.giro_id = ""
                r.giro_aperta = False
                r.wallet_to_id = None
                r.importo_ricevuto = None
                r.data_ricevuto = None
                r.controparte = ""
        db.commit()
        return True


def aggiorna_giro(giro_id, spese, rientri=None, aperta=False):
    """Modifica una partita di giro TUTTA INSIEME. Le gambe vecchie diventano
    tombstone (deleted=True: così la sostituzione viaggia nel sync) e vengono
    ricreate dalle liste passate, con lo STESSO `giro_id` (la partita resta la
    stessa). Serve almeno una spesa. Con `aperta=True` i rientri sono ignorati."""
    spese = [s for s in (spese or []) if (s.get("importo") or 0) > 0 and s.get("wallet_id")]
    rientri = [] if aperta else [r for r in (rientri or [])
                                 if (r.get("importo") or 0) > 0 and r.get("wallet_id")]
    if not spese:
        return False
    with SessionLocal() as db:
        rows = db.query(Transaction).filter(
            Transaction.giro_id == giro_id, Transaction.deleted.is_(False)).all()
        if not rows:
            return False
        for r in rows:                     # tombstone delle vecchie gambe
            r.deleted = True
        # ...e delle loro righe generate: le gambe vengono ricreate da zero, quindi
        # le vecchie figlie resterebbero appese a un genitore cancellato — invisibili
        # nel registro e con i soldi ancora nel salvadanaio.
        for f in db.query(Transaction).filter(
                Transaction.parent_tx_id.in_([r.id for r in rows]),
                Transaction.deleted.is_(False)).all():
            f.deleted = True
        gambe = []
        for s in spese:
            riga = _riga_spesa(db, giro_id, aperta, s.get("importo"), s["wallet_id"],
                               s.get("categoria", ""), s.get("descrizione", ""), s.get("data"))
            db.add(riga)
            gambe.append((riga, s.get("arr"), s.get("sav")))
        for r in rientri:
            db.add(_riga_rientro(db, giro_id, aperta, r.get("importo"), r["wallet_id"],
                                 r.get("controparte", ""), r.get("data")))
        db.flush()
        _figlie_delle_gambe(db, gambe)
        db.commit()
        return True


def _fmt_importo_form(v) -> str:
    """Importo per un campo del form: '12,34' (virgola decimale, sempre 2 cifre)."""
    return ("%.2f" % (v or 0.0)).replace(".", ",")


def _fmt_dt_form(dt) -> str:
    """Data per un <input type=datetime-local>: 'YYYY-MM-DDTHH:MM' ('' se assente)."""
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""


def dati_modifica(tid):
    """Ricostruisce i dati di un movimento per il form di modifica.
    - movimento normale -> dict kind='generic' con i suoi campi;
    - partita di giro    -> dict kind='giro' con TUTTE le gambe (spese + rientri),
      così si modifica l'intera partita in un colpo solo (le righe combo legacy
      vengono scomposte in una spesa + un rientro).
    Ritorna None se il movimento non esiste (o è già eliminato)."""
    with SessionLocal() as db:
        t = db.get(Transaction, tid)
        if not t or t.deleted:
            return None
        cn = {c.id: c.nome for c in db.query(Category).all()}
        if t.tipo == TIPO_GIRO and (t.giro_id or ""):
            rows = db.query(Transaction).filter(
                Transaction.giro_id == t.giro_id, Transaction.deleted.is_(False)
            ).order_by(Transaction.data, Transaction.id).all()
            figlie_per_gamba = {}
            for f in db.query(Transaction).filter(
                    Transaction.parent_tx_id.in_([r.id for r in rows]),
                    Transaction.deleted.is_(False)).all():
                figlie_per_gamba.setdefault(f.parent_tx_id, {})[f.origine] = \
                    round(f.importo or 0.0, 2)
            spese, rientri = [], []
            for r in rows:
                if (r.importo or 0) > 0:                 # gamba spesa (anche del combo)
                    # come per le uscite: gli importi generati tornano nel modulo
                    # com'erano, e il flag dice al JS quali avevi corretto tu
                    gen = figlie_per_gamba.get(r.id, {})
                    auto = extra_carta(r.wallet_id, r.importo, r.data, escludi_tx=r.id)
                    spese.append({
                        "importo": _fmt_importo_form(r.importo),
                        "wallet_id": r.wallet_id,
                        "categoria": cn.get(r.category_id, "") or "",
                        "descrizione": r.descrizione or "",
                        "data_local": _fmt_dt_form(r.data),
                        "arr": _fmt_importo_form(gen.get(ORIGINE_ARROTONDAMENTO, 0.0)) if gen else "",
                        "sav": _fmt_importo_form(gen.get(ORIGINE_SAVEBACK, 0.0)) if gen else "",
                        "arr_mio": bool(gen and gen.get(ORIGINE_ARROTONDAMENTO, 0.0)
                                        != auto["arrotondamento"]),
                        "sav_mio": bool(gen and gen.get(ORIGINE_SAVEBACK, 0.0)
                                        != auto["saveback"]),
                    })
                if r.importo_ricevuto is not None:       # gamba rientro (anche del combo)
                    rientri.append({
                        "controparte": r.controparte or "",
                        "importo": _fmt_importo_form(r.importo_ricevuto),
                        "data_local": _fmt_dt_form(r.data_ricevuto or r.data),
                        "wallet_id": r.wallet_to_id or r.wallet_id,
                    })
            return {
                "kind": "giro",
                "giro_id": t.giro_id,
                "action": f"/finanze/giro/{t.giro_id}/aggiorna",
                "aperta": any(r.giro_aperta for r in rows),
                "spese": spese,
                "rientri": rientri,
            }
        gen = {f.origine: round(f.importo or 0.0, 2) for f in db.query(Transaction).filter(
            Transaction.parent_tx_id == tid, Transaction.deleted.is_(False)).all()}
    # gli importi delle righe generate tornano nel modulo COSÌ COME SONO: se ne
    # avevi corretto uno, riaprire la modifica non deve fartelo perdere. Il flag
    # dice al JS quali erano tuoi, così non li ricalcola addosso mentre scrivi.
    auto = extra_carta(t.wallet_id, t.importo, t.data, escludi_tx=tid) \
        if t.tipo == TIPO_USCITA else None
    return {
        "kind": "generic",
        "id": t.id,
        "action": f"/finanze/movimenti/{t.id}/aggiorna",
        "tipo": t.tipo,
        "importo": _fmt_importo_form(t.importo),
        "wallet_id": t.wallet_id,
        "wallet_to_id": t.wallet_to_id,
        "categoria": cn.get(t.category_id, "") or "",
        "descrizione": t.descrizione or "",
        "data_local": _fmt_dt_form(t.data),
        "extra_arr": _fmt_importo_form(gen.get(ORIGINE_ARROTONDAMENTO, 0.0)) if gen else "",
        "extra_sav": _fmt_importo_form(gen.get(ORIGINE_SAVEBACK, 0.0)) if gen else "",
        "extra_arr_mio": bool(auto and gen and
                              gen.get(ORIGINE_ARROTONDAMENTO, 0.0) != auto["arrotondamento"]),
        "extra_sav_mio": bool(auto and gen and
                              gen.get(ORIGINE_SAVEBACK, 0.0) != auto["saveback"]),
    }


def _riassumi_giro(rows) -> dict:
    """Aggrega le gambe di UNA partita (righe con lo stesso giro_id) in un
    riepilogo: totali speso/ricevuto, netto, date chiave, apertura."""
    speso = sum(r.importo or 0.0 for r in rows)
    ricevuto = sum(r.importo_ricevuto or 0.0 for r in rows if r.importo_ricevuto is not None)
    date_spesa = [r.data for r in rows if (r.importo or 0.0) > 0 and r.data]
    date_rientro = [r.data_ricevuto for r in rows if r.importo_ricevuto is not None and r.data_ricevuto]
    return {
        "giro_id": rows[0].giro_id,
        "aperta": any(r.giro_aperta for r in rows),
        "speso": round(speso, 2),
        "ricevuto": round(ricevuto, 2),
        "netto": round(ricevuto - speso, 2),
        "n_rientri": sum(1 for r in rows if r.importo_ricevuto is not None),
        "ultima_spesa": max(date_spesa) if date_spesa else None,
        "ultimo_rientro": max(date_rientro) if date_rientro else None,
        "prima_data": min(date_spesa) if date_spesa else (rows[0].data),
    }


def _gruppi_giro(db):
    """{giro_id: [righe]} di tutte le partite di giro, ordinate per data."""
    rows = list(db.execute(
        select(Transaction).where(Transaction.tipo == TIPO_GIRO,
                                  Transaction.deleted.is_(False))
        .order_by(Transaction.data, Transaction.id)).scalars().all())
    gruppi = {}
    for t in rows:
        gruppi.setdefault(t.giro_id or f"_{t.id}", []).append(t)
    return gruppi


def giri_aperti():
    """Partite di giro APERTE (in attesa di rimborso), le più vecchie prima, con
    le loro spese e i rientri già registrati: alimentano il riquadro
    'In attesa di rimborso' della pagina Finanze."""
    with SessionLocal() as db:
        wn = {w.id: w.nome for w in db.query(Wallet).all()}
        gruppi = _gruppi_giro(db)
        out = []
        for gid, rows in gruppi.items():
            rec = _riassumi_giro(rows)
            if not rec["aperta"]:
                continue
            spese = [{"importo": r.importo, "wallet": wn.get(r.wallet_id, "—"),
                      "descrizione": r.descrizione, "controparte": r.controparte,
                      "data": r.data} for r in rows if r.giro_kind in ("spesa", "combo")]
            rientri = [{"importo": r.importo_ricevuto, "wallet": wn.get(r.wallet_id, "—"),
                        "controparte": r.controparte, "data": r.data_ricevuto}
                       for r in rows if r.giro_kind in ("rientro", "combo")]
            controparti_g = sorted({s["controparte"] for s in spese if s["controparte"]} |
                                   {r["controparte"] for r in rientri if r["controparte"]}, key=str.lower)
            out.append({**rec, "spese": spese, "rientri": rientri, "controparti": controparti_g})
        out.sort(key=lambda g: (g["prima_data"] or datetime.now()))
    return out


def controparti() -> list[str]:
    """I 'da chi' già usati (distinti), per i suggerimenti del modulo."""
    with SessionLocal() as db:
        rows = db.query(Transaction.controparte).filter(
            Transaction.controparte != "",
            Transaction.deleted.is_(False)).distinct().all()
    return sorted({(r[0] or "").strip() for r in rows if (r[0] or "").strip()},
                  key=str.lower)


def lista_movimenti(limit=None, mese=None, anno=None):
    """Movimenti ordinati per data e ora decrescenti; senza `limit` li
    restituisce TUTTI (la tabella in pagina mostra l'intero registro).

    Le righe generate (arrotondamento, saveback) NON sono elencate a parte: si
    vedono aprendo il movimento che le ha prodotte, dentro `figlie`. Elencarle
    anche fuori le farebbe comparire due volte, e trasformerebbe una spesa al bar
    in tre righe da leggere. Nei totali però pesano come tutte le altre."""
    with SessionLocal() as db:
        wn = {w.id: w.nome for w in db.query(Wallet).all()}
        cn = {c.id: c.nome for c in db.query(Category).all()}
        q = select(Transaction).where(
            Transaction.deleted.is_(False),
            Transaction.parent_tx_id.is_(None),
        ).order_by(Transaction.data.desc(), Transaction.id.desc())
        if anno and mese:
            start, end = _range_mese(anno, mese)
            q = q.where(Transaction.data >= start, Transaction.data < end)
        if limit:
            q = q.limit(limit)
        rows = list(db.execute(q).scalars().all())
        # le figlie dei movimenti mostrati, per il dettaglio a scomparsa
        ids = [t.id for t in rows]
        gen = {}
        if ids:
            for f in db.query(Transaction).filter(
                    Transaction.parent_tx_id.in_(ids),
                    Transaction.deleted.is_(False)).order_by(Transaction.id).all():
                gen.setdefault(f.parent_tx_id, []).append({
                    "t": f,
                    "wallet": wn.get(f.wallet_id, "—"),
                    "wallet_to": wn.get(f.wallet_to_id) if f.wallet_to_id else None,
                })
    return [{
        "t": t,
        "wallet": wn.get(t.wallet_id, "—"),
        "wallet_to": wn.get(t.wallet_to_id) if t.wallet_to_id else None,
        "categoria": cn.get(t.category_id) if t.category_id else None,
        "figlie": gen.get(t.id, []),
        # quanto è uscito DAVVERO dal conto: la spesa più l'arrotondamento
        "addebito": round((t.importo or 0.0) + sum(
            f["t"].importo for f in gen.get(t.id, [])
            if f["t"].origine == ORIGINE_ARROTONDAMENTO), 2),
    } for t in rows]


def _range_mese(anno, mese):
    start = datetime(anno, mese, 1)
    end = datetime(anno + (1 if mese == 12 else 0), 1 if mese == 12 else mese + 1, 1)
    return start, end


def _ids_saldo_derivato(db) -> set:
    """Wallet il cui saldo NON nasce dai movimenti ma da fuori: oggi solo il PAC,
    il cui valore arriva dal Portafoglio (vedi valore_pac_live).

    Vanno tenuti fuori da ogni somma di liquidità. Il trasferimento del PAC toglie
    i soldi dal conto e li mette qui: se questo wallet li rimettesse nel conteggio,
    il totale non si accorgerebbe di niente — e chi somma la liquidità al valore
    dei titoli (il grafico del patrimonio) conterebbe gli stessi euro due volte,
    insieme fermi sul conto e già trasformati in titoli."""
    return {w.id for w in db.query(Wallet).filter(Wallet.deleted.is_(False)).all()
            if (w.nome or "").strip().lower() == NOME_WALLET_PAC.lower()}


def _liquidita_walk():
    """Base (saldi di apertura dei wallet attivi) + effetti ordinati per data
    dei movimenti reali sulla liquidità. Nessuna stima: solo il registro.
    Una partita di giro produce DUE effetti a due date: la spesa quando esce,
    il rimborso quando (e se) entra.
    I conti a saldo derivato restano fuori (vedi _ids_saldo_derivato)."""
    # ...ma solo se il valore vivo c'è davvero. Senza prezzi il Portafoglio vale
    # zero: allora il saldo dei movimenti è l'unica cosa vera che abbiamo di quei
    # soldi, e toglierlo li farebbe sparire dal grafico invece di spostarli.
    # È la stessa condizione con cui saldi() marca il conto come 'derivato'.
    ha_valore_vivo = valore_pac_live() is not None
    with SessionLocal() as db:
        derivati = _ids_saldo_derivato(db) if ha_valore_vivo else set()
        attivi = {w.id for w in db.query(Wallet).filter(
            Wallet.archiviato.is_(False), Wallet.deleted.is_(False)).all()} - derivati
        base = sum(w.saldo_iniziale or 0.0 for w in db.query(Wallet).filter(
            Wallet.deleted.is_(False)).all() if w.id in attivi)
        effetti = []
        for t in db.query(Transaction).filter(Transaction.deleted.is_(False)).all():
            imp = t.importo or 0.0
            if t.tipo == TIPO_ENTRATA and t.wallet_id in attivi:
                effetti.append((t.data, +imp))
            elif t.tipo == TIPO_USCITA and t.wallet_id in attivi:
                effetti.append((t.data, -imp))
            elif t.tipo == TIPO_TRASFERIMENTO:
                if t.wallet_id in attivi:
                    effetti.append((t.data, -imp))
                if t.wallet_to_id in attivi:
                    effetti.append((t.data, +imp))
            elif t.tipo == TIPO_GIRO:
                if t.wallet_id in attivi:
                    effetti.append((t.data, -imp))
                if t.importo_ricevuto is not None and t.wallet_to_id in attivi:
                    effetti.append((t.data_ricevuto or t.data, +(t.importo_ricevuto or 0.0)))

    effetti.sort(key=lambda x: x[0])
    return base, effetti


def liquidita_alle_date(dates) -> list[float]:
    """Liquidità totale (wallet attivi) a ciascuna delle date indicate (ordinate
    crescenti), ricostruita dai movimenti: usata dal grafico del patrimonio."""
    base, effetti = _liquidita_walk()
    out, cum, i = [], base, 0
    for b in dates:
        while i < len(effetti) and effetti[i][0] < b:
            cum += effetti[i][1]
            i += 1
        out.append(round(cum, 2))
    return out


def prima_data_movimento():
    """Data del primo effetto registrato (None se il registro è vuoto).
    Considera anche i rimborsi delle partite di giro: possono precedere la spesa."""
    with SessionLocal() as db:
        d1 = db.query(func.min(Transaction.data)).filter(Transaction.deleted.is_(False)).scalar()
        d2 = db.query(func.min(Transaction.data_ricevuto)).filter(Transaction.deleted.is_(False)).scalar()
    return min((d for d in (d1, d2) if d), default=None)


def data_inizio():
    """Inizio del tracking del patrimonio: la data dei saldi di apertura
    (DATA_INIZIO, 4/7/2026), o la prima data di movimento se precedente. Il
    grafico del patrimonio non mostra nulla prima di questa data."""
    prima = prima_data_movimento()
    return min(DATA_INIZIO, prima) if prima else DATA_INIZIO


def serie_liquidita_12m() -> list[float]:
    """Liquidità totale (wallet attivi) a fine di ognuno degli ultimi 12 mesi,
    RICOSTRUITA dai movimenti reali. Ultimo punto = oggi. Niente stime."""
    now = datetime.now()
    bounds = []
    for k in range(11, 0, -1):
        y, m = _mesi_indietro_ym(now, k - 1)   # inizio del mese successivo al k-esimo
        bounds.append(datetime(y, m, 1))
    bounds.append(now)
    return liquidita_alle_date(bounds)


def _mesi_indietro_ym(now, k):
    y, m = now.year, now.month - k
    while m <= 0:
        m += 12
        y -= 1
    return y, m


def riepilogo_mese(anno, mese):
    start, end = _range_mese(anno, mese)
    T = Transaction
    _alive = T.deleted.is_(False)
    with SessionLocal() as db:
        entrate = db.query(func.coalesce(func.sum(T.importo), 0.0)).filter(
            T.tipo == TIPO_ENTRATA, T.data >= start, T.data < end, _alive).scalar() or 0.0
        uscite = db.query(func.coalesce(func.sum(T.importo), 0.0)).filter(
            T.tipo == TIPO_USCITA, T.data >= start, T.data < end, _alive).scalar() or 0.0
        gruppi = _gruppi_giro(db)
        per_cat = db.query(Category.nome, func.sum(T.importo)).join(
            Category, Category.id == T.category_id).filter(
            T.tipo == TIPO_USCITA, T.data >= start, T.data < end, _alive).group_by(
            Category.id).order_by(func.sum(T.importo).desc()).all()
    # Partite di giro CHIUSE: in entrate/uscite conta SOLO il netto della partita
    # (Σ rientri − Σ spese). Netto > 0 = entrata all'ultimo rientro; netto < 0 =
    # uscita all'ultima spesa. Le aperte restano neutre; le gambe intere non
    # compaiono mai qui (muovono solo i saldi dei portafogli).
    entrate, uscite = float(entrate), float(uscite)
    for rows in gruppi.values():
        rec = _riassumi_giro(rows)
        if rec["aperta"]:
            continue
        netto = rec["netto"]
        if netto > 0 and rec["ultimo_rientro"] and start <= rec["ultimo_rientro"] < end:
            entrate += netto
        elif netto < 0 and rec["ultima_spesa"] and start <= rec["ultima_spesa"] < end:
            uscite += -netto
    spese = [{"nome": n, "tot": round(float(s), 2)} for n, s in per_cat]
    max_spesa = max((s["tot"] for s in spese), default=0.0)
    for s in spese:
        s["perc"] = round(s["tot"] / max_spesa * 100, 1) if max_spesa else 0
    return {
        "entrate": round(float(entrate), 2),
        "uscite": round(float(uscite), 2),
        "saldo": round(float(entrate) - float(uscite), 2),
        "spese_categoria": spese,
        "anno": anno, "mese": mese,
    }


# ============================================================================
#  API JSON per la PWA e il sync (v2, vedi PIANO-V2.md). SOLA LETTURA in Fase 1;
#  il canale di scrittura/fusione arriva con la Fase 4. I riferimenti tra record
#  usano lo `uid` (stabile tra dispositivi), MAI l'id interno (che varia).
# ============================================================================
def calendario_spese(anno, mese):
    """Le uscite giorno per giorno del mese: il calendario in pagina.

    Stessa definizione di «uscita» del riepilogo (una partita di giro chiusa
    conta per il netto, alla data dell'ultima gamba), altrimenti due riquadri
    della stessa pagina racconterebbero due mesi diversi.

    L'intensità non è proporzionale al massimo ma al RANGO del giorno fra i
    giorni con spesa: con un regalo da 75 € e il resto sotto i 20, una scala
    lineare dipingerebbe un quadrato acceso e trenta spenti — vero ma illeggibile.
    """
    start, end = _range_mese(anno, mese)
    T = Transaction
    with SessionLocal() as db:
        righe = db.query(T.data, T.importo).filter(
            T.tipo == TIPO_USCITA, T.data >= start, T.data < end,
            T.deleted.is_(False)).all()
        gruppi = _gruppi_giro(db)

    per_giorno = {}
    for data, importo in righe:
        per_giorno[data.day] = per_giorno.get(data.day, 0.0) + float(importo or 0.0)
    for rows in gruppi.values():
        rec = _riassumi_giro(rows)
        if rec["aperta"] or rec["netto"] >= 0:
            continue
        quando = rec["ultima_spesa"]
        if quando and start <= quando < end:
            per_giorno[quando.day] = per_giorno.get(quando.day, 0.0) + (-rec["netto"])

    scala = sorted(v for v in per_giorno.values() if v > 0)

    def livello(v):
        if v <= 0:
            return 0
        return min(4, int(bisect_left(scala, v) / len(scala) * 4) + 1)

    now = datetime.now()
    ultimo = (end - timedelta(days=1)).day
    oggi = now.day if (now.year, now.month) == (anno, mese) else None
    giorni = []
    for g in range(1, ultimo + 1):
        tot = round(per_giorno.get(g, 0.0), 2)
        giorni.append({
            "g": g,
            "tot": tot,
            "liv": livello(tot),
            # i giorni non ancora arrivati non sono giorni «senza spese»
            "futuro": oggi is not None and g > oggi,
            "oggi": g == oggi,
        })
    return {
        "giorni": giorni,
        "vuote_prima": start.weekday(),      # 0 = lunedì
        "con_spesa": len(scala),
        "max": round(scala[-1], 2) if scala else 0.0,
    }


def accantonato_mese(anno, mese) -> float:
    """Quanto è finito nel salvadanaio durante il mese, al netto di quello che ne
    è uscito (quando la banca compra). Sono gli arrotondamenti più il saveback.

    Serve a «Dove è finito il mese»: senza questa voce quei soldi finirebbero in
    «rimasto liquido», cioè l'app direbbe che puoi spenderli — e non puoi."""
    start, end = _range_mese(anno, mese)
    T = Transaction
    with SessionLocal() as db:
        w = db.query(Wallet).filter(Wallet.deleted.is_(False)).filter(
            func.lower(func.trim(Wallet.nome)) == NOME_WALLET_NASCOSTI.lower()).first()
        if w is None:
            return 0.0
        periodo = (T.deleted.is_(False), T.data >= start, T.data < end)
        dentro = db.query(func.coalesce(func.sum(T.importo), 0.0)).filter(
            *periodo, T.wallet_to_id == w.id, T.tipo == TIPO_TRASFERIMENTO).scalar() or 0.0
        dentro += db.query(func.coalesce(func.sum(T.importo), 0.0)).filter(
            *periodo, T.wallet_id == w.id, T.tipo == TIPO_ENTRATA).scalar() or 0.0
        fuori = db.query(func.coalesce(func.sum(T.importo), 0.0)).filter(
            *periodo, T.wallet_id == w.id, T.tipo.in_((TIPO_TRASFERIMENTO, TIPO_USCITA))).scalar() or 0.0
    return round(dentro - fuori, 2)


def destinazioni_mese(anno, mese):
    """Dove è finito il mese: le entrate divise in speso, investito, accantonato
    e rimasto liquido. È l'unico punto dell'app in cui il PAC compare accanto
    alle spese.

    Nessun doppio conteggio: il versamento PAC è un TRASFERIMENTO (conto →
    conto PAC), quindi non compare né in entrate né in uscite del riepilogo.
    Rimasto liquido = entrate − speso − investito − accantonato, e il conto torna
    esatto anche col saveback: quello entra nelle entrate ma non passa mai dal
    conto, e infatti riesce subito dalla parte dell'accantonato.

    Senza entrate nel mese non c'è niente da dividere e la funzione torna None:
    meglio un riquadro assente che una torta con una fetta sola.
    """
    riep = riepilogo_mese(anno, mese)
    entrate = riep["entrate"]
    if entrate <= 0:
        return None

    start, end = _range_mese(anno, mese)
    investito = 0.0
    try:
        from portfolio import versamenti
        investito = round(sum(
            (v["importo"] or 0.0) for v in versamenti.lista()
            if v["data"] and start.date() <= v["data"] < end.date()), 2)
    except Exception:
        investito = 0.0      # il PAC è un extra: senza, restano speso e rimasto

    speso = riep["uscite"]
    accantonato = accantonato_mese(anno, mese)
    rimasto = round(entrate - speso - investito - accantonato, 2)
    # se hai speso e investito più di quanto è entrato, le percentuali non
    # possono stare sulle entrate: la base diventa il totale uscito.
    base = max(entrate, speso + investito + max(accantonato, 0.0))

    def pct(v):
        return round(v / base * 100, 1) if base else 0.0

    voci = [
        {"key": "speso", "val": speso, "pct": pct(speso)},
        {"key": "investito", "val": investito, "pct": pct(investito)},
    ]
    # la voce compare solo se c'è: un «accantonato 0,00 €» fisso sarebbe rumore
    # per chi non usa una carta che arrotonda
    if accantonato:
        voci.append({"key": "accantonato", "val": accantonato,
                     "pct": pct(max(accantonato, 0.0))})
    voci.append({"key": "rimasto", "val": rimasto, "pct": pct(max(rimasto, 0.0))})

    return {
        "entrate": entrate,
        "voci": voci,
        "in_rosso": rimasto < 0,
    }


def uscite_per_categoria_mese(anno, mese) -> dict:
    """{categoria minuscola: {'n': quante volte, 'tot': quanto}} per il mese.
    Serve al riquadro «che cosa cambia» accanto al modulo: sapere che è la
    terza volta che compare una categoria è il contesto che manca quando
    registri un movimento."""
    start, end = _range_mese(anno, mese)
    T = Transaction
    with SessionLocal() as db:
        rows = db.query(Category.nome, func.count(T.id), func.sum(T.importo)).join(
            Category, Category.id == T.category_id).filter(
            T.tipo == TIPO_USCITA, T.data >= start, T.data < end,
            T.deleted.is_(False)).group_by(Category.id).all()
    return {(n or "").strip().lower(): {"n": int(c or 0), "tot": round(float(s or 0.0), 2)}
            for n, c, s in rows}


def spesa_top(anno, mese):
    """La singola uscita più grossa del mese (None se non ce ne sono).
    Un numero secco che una media non racconta: 17,83 al giorno non dice che
    un giorno solo ne sono usciti 75."""
    start, end = _range_mese(anno, mese)
    T = Transaction
    with SessionLocal() as db:
        row = db.query(T).filter(
            T.tipo == TIPO_USCITA, T.data >= start, T.data < end,
            T.deleted.is_(False)).order_by(T.importo.desc()).first()
        if row is None:
            return None
        cat = db.query(Category.nome).filter(
            Category.id == row.category_id).scalar() if row.category_id else None
    return {"importo": round(float(row.importo or 0.0), 2), "data": row.data,
            "descrizione": (row.descrizione or "").strip(), "categoria": cat}


def _iso(dt):
    return dt.isoformat() if dt else None


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def stato_sync() -> dict:
    """Fotografia dello stato Finanze per la dashboard della PWA: portafogli (con
    saldo attuale), categorie e sintesi del mese. Ogni record porta uid/rev/updated_at."""
    now = datetime.now()
    with SessionLocal() as db:
        smap = _saldi_map(db)
        ws = list(db.execute(select(Wallet).order_by(Wallet.ordine, Wallet.id)).scalars().all())
        cats = list(db.execute(select(Category).order_by(Category.nome)).scalars().all())
        wallets = [{
            "uid": w.uid, "nome": w.nome, "tipo": w.tipo,
            "saldo_iniziale": round(w.saldo_iniziale or 0.0, 2),
            "saldo": round(smap.get(w.id, 0.0), 2),
            "colore": w.colore, "ordine": w.ordine,
            "archiviato": bool(w.archiviato), "deleted": bool(w.deleted),
            "rev": w.rev, "updated_at": _iso(w.updated_at),
        } for w in ws]
        categorie = [{
            "uid": c.uid, "nome": c.nome, "kind": c.kind,
            "archiviato": bool(c.archiviato), "deleted": bool(c.deleted),
            "rev": c.rev, "updated_at": _iso(c.updated_at),
        } for c in cats]
    riep = riepilogo_mese(now.year, now.month)
    totale = round(sum(w["saldo"] for w in wallets if not w["archiviato"]), 2)
    return {
        "wallets": wallets, "categorie": categorie, "totale": totale,
        "mese": {"anno": now.year, "mese": now.month, "entrate": riep["entrate"],
                 "uscite": riep["uscite"], "saldo": riep["saldo"]},
        "generato": _iso(now),
    }


def movimenti_sync(since=None, limit=None) -> list[dict]:
    """Movimenti in formato sync: tutti i campi + uid/rev/updated_at, riferimenti
    per uid. Con `since` (ISO-8601) restituisce solo quelli modificati DOPO quel
    momento (delta per la sincronizzazione)."""
    since_dt = _parse_iso(since) if isinstance(since, str) else since
    with SessionLocal() as db:
        wuid = {w.id: w.uid for w in db.query(Wallet).all()}
        cuid = {c.id: c.uid for c in db.query(Category).all()}
        # il genitore di una riga generata viaggia per uid: gli id locali non
        # sono gli stessi su due dispositivi
        tuid = {t.id: t.uid for t in db.query(Transaction).all()}
        q = select(Transaction).order_by(Transaction.updated_at.desc(), Transaction.id.desc())
        if since_dt:
            q = q.where(Transaction.updated_at > since_dt)
        if limit:
            q = q.limit(limit)
        rows = list(db.execute(q).scalars().all())
    return [{
        "uid": t.uid, "tipo": t.tipo, "data": _iso(t.data),
        "importo": round(t.importo or 0.0, 2),
        "wallet_uid": wuid.get(t.wallet_id),
        "wallet_to_uid": wuid.get(t.wallet_to_id) if t.wallet_to_id else None,
        "categoria_uid": cuid.get(t.category_id) if t.category_id else None,
        "descrizione": t.descrizione,
        "giro_id": t.giro_id, "giro_aperta": bool(t.giro_aperta),
        "importo_ricevuto": (round(t.importo_ricevuto, 2) if t.importo_ricevuto is not None else None),
        "data_ricevuto": _iso(t.data_ricevuto), "controparte": t.controparte,
        "origine": t.origine or "", "parent_uid": tuid.get(t.parent_tx_id),
        "rev": t.rev, "updated_at": _iso(t.updated_at), "deleted": bool(t.deleted),
    } for t in rows]
