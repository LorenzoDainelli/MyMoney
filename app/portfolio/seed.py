"""Precarico del portafoglio iniziale (38 asset, somma target = 100%).

Trentasette hanno una percentuale target e si dividono il PAC; il trentottesimo
(l'ETC oro) ha target 0 perché non riceve versamenti programmati ma solo gli
arrotondamenti della carta: sta in lista per esistere, non per essere comprato.

Fonte delle percentuali: l'allocazione target indicata dall'utente
(aggiornata il 10/07/2026: niente più importi fissi, solo percentuali).
Fonte di ticker/ISIN: l'anagrafica gia' verificata in config/portfolio.yaml del
news-monitor (cosi' la lista e' scritta una volta sola).

I dati di DETTAGLIO (prezzi, AUM, TER, holdings...) NON sono qui e NON vanno mai
inventati: arrivano dalle fonti dati. Qui c'e' solo l'anagrafica.
"""
from sqlalchemy import Boolean, Float, Integer, String

from shared.db import SessionLocal, engine
from shared.schema import aggiungi_colonne
from portfolio.models import Position, TIPO_ETF, TIPO_AZIONE

# Nomi CORTI per le tabelle (scelti dall'utente, senza la parola "ETF"):
# il nome ufficiale completo resta in `nome` e compare nella scheda di dettaglio.
NOMI_BREVI = {
    "IWDA": "Global",
    "CSPX": "S&P 500",
    "CNDX": "Nasdaq 100",
    "VHYL": "Dividend",
    "XDWH": "Pharmaceutical",
    "NATO": "Defense",
    "NUKL": "Nuclear & Uranium",
    "XDWM": "Materials",
    "GIFL": "Infrastructure",
    "UKRN": "Ukraine Reconstruction",
    "HEAL": "MedTech",
    "EGLN": "Oro",
}


def migra_schema():
    """Colonne aggiunte dopo la prima release (create_all non altera le tabelle
    esistenti): idempotente, e valido su entrambi i motori (shared/schema.py)."""
    with engine.connect() as c:
        aggiungi_colonne(c, "portfolio_positions", (
            ("nome_breve", String(80), ""),
            ("versato_totale", Float(), 0.0),
        ))
        aggiungi_colonne(c, "portfolio_versamenti", (
            ("tx_id", Integer(), None),
            ("ora", String(5), ""),
            # I versamenti già registrati sono tutti il PAC vero: partire da
            # False è la risposta giusta per la storia che c'è.
            ("fuori_piano", Boolean(), False),
        ))


# (nome, tipo, categoria, ticker, isin, pct_target, importo_fisso, note)
SEED = [
    # ===================== ETF (somma 60%) =====================
    ("iShares Core MSCI World UCITS ETF USD (Acc)", TIPO_ETF, "Azionario globale", "IWDA", "IE00B4L5Y983", 20.0, None, ""),
    ("iShares Core S&P 500 UCITS ETF USD (Acc)", TIPO_ETF, "Azionario USA", "CSPX", "IE00B5BMR087", 6.0, None, ""),
    ("iShares Nasdaq 100 UCITS ETF (Acc)", TIPO_ETF, "Tech USA", "CNDX", "IE00B53SZB19", 2.0, None, ""),
    ("Vanguard FTSE All-World High Dividend Yield UCITS ETF (Acc)", TIPO_ETF, "Dividendi globali", "VHYL", "IE00BK5BR626", 6.0, None, ""),
    ("Xtrackers MSCI World Health Care UCITS ETF 1C", TIPO_ETF, "Sanita'", "XDWH", "IE00BM67HK77", 4.0, None, ""),
    ("HANetf Future of Defence UCITS ETF (NATO)", TIPO_ETF, "Difesa", "NATO", "IE000OJ5TQP4", 4.0, None, ""),
    ("VanEck Uranium and Nuclear Technologies UCITS ETF A", TIPO_ETF, "Nucleare & uranio", "NUKL", "IE000M7V94E1", 4.0, None, ""),
    ("Xtrackers MSCI World Materials UCITS ETF 1C", TIPO_ETF, "Materiali", "XDWM", "IE00BM67HS53", 4.0, None, ""),
    ("iShares Global Infrastructure UCITS ETF USD (Acc)", TIPO_ETF, "Infrastrutture", "GIFL", "IE000CK5G8J7", 4.0, None, ""),
    ("Defiance Ukraine Reconstruction UCITS ETF", TIPO_ETF, "Ricostruzione Ucraina", "UKRN", "IE000R8PO127", 2.0, None, ""),
    ("iShares Healthcare Innovation UCITS ETF (Acc)", TIPO_ETF, "Innovazione sanitaria", "HEAL", "IE00BYZK4776", 4.0, None, ""),

    # ===================== AZIONI (somma 40%) =====================
    ("ASML Holding", TIPO_AZIONE, "Semiconduttori", "ASML", "NL0010273215", 3.0, None, ""),
    ("NVIDIA", TIPO_AZIONE, "Semiconduttori / AI", "NVDA", "US67066G1040", 3.0, None, ""),
    ("Taiwan Semiconductor (TSMC)", TIPO_AZIONE, "Semiconduttori", "TSM", "US8740391003", 3.0, None, ""),
    ("Microsoft", TIPO_AZIONE, "Tech / Cloud", "MSFT", "US5949181045", 2.0, None, ""),
    ("Alphabet (Google)", TIPO_AZIONE, "Tech / Internet", "GOOG", "US02079K3059", 2.0, None, ""),
    ("Broadcom", TIPO_AZIONE, "Semiconduttori", "AVGO", "US11135F1012", 2.0, None, ""),
    ("Intel", TIPO_AZIONE, "Semiconduttori", "INTC", "US4581401001", 2.0, None, ""),
    ("SpaceX", TIPO_AZIONE, "Spazio & difesa", "SPCX", "US84615Q1031", 2.0, None,
     "Neo-quotata su Nasdaq dal 12 giugno 2026: alta volatilita' post-IPO. "
     "Annotare qui la scadenza del lock-up quando nota."),
    ("Apple", TIPO_AZIONE, "Tech / Hardware", "AAPL", "US0378331005", 2.0, None, ""),
    ("SanDisk", TIPO_AZIONE, "Hardware / Storage", "SNDK", "US80004C2008", 2.0, None, ""),
    ("Western Digital", TIPO_AZIONE, "Hardware / Storage", "WDC", "US9581021055", 2.0, None, ""),
    ("Oracle", TIPO_AZIONE, "Tech / Cloud", "ORCL", "US68389X1054", 1.0, None, ""),
    ("Samsung Electronics", TIPO_AZIONE, "Tech / Hardware", "SSNLF", "US7960508882", 1.0, None, ""),
    ("Palantir Technologies", TIPO_AZIONE, "Software / AI", "PLTR", "US69608A1088", 1.0, None, ""),
    ("JPMorgan Chase", TIPO_AZIONE, "Finanza / Banche", "JPM", "US46625H1005", 1.0, None, ""),
    ("Eli Lilly", TIPO_AZIONE, "Farmaceutica", "LLY", "US5324571083", 1.0, None, ""),
    ("Amgen", TIPO_AZIONE, "Farmaceutica / Biotech", "AMGN", "US0311621009", 1.0, None, ""),
    ("Amazon", TIPO_AZIONE, "Tech / E-commerce", "AMZN", "US0231351067", 1.0, None, ""),
    ("Meta Platforms", TIPO_AZIONE, "Tech / Social", "META", "US30303M1027", 1.0, None, ""),
    ("Walmart", TIPO_AZIONE, "Retail / Consumi", "WMT", "US9311421039", 1.0, None, ""),
    ("Netflix", TIPO_AZIONE, "Media / Streaming", "NFLX", "US64110L1061", 1.0, None, ""),
    ("Coca-Cola", TIPO_AZIONE, "Beni di consumo", "KO", "US1912161007", 1.0, None, ""),
    ("IBM", TIPO_AZIONE, "Tech / Cloud", "IBM", "US4592001014", 1.0, None, ""),
    ("Shell", TIPO_AZIONE, "Energia", "SHEL", "GB00BP6MXD84", 1.0, None, ""),
    ("Johnson & Johnson", TIPO_AZIONE, "Farmaceutica / Salute", "JNJ", "US4781601046", 1.0, None, ""),
    ("Take-Two Interactive", TIPO_AZIONE, "Gaming / Media", "TTWO", "US8740541094", 1.0, None, ""),

    # ===================== FUORI ALLOCAZIONE (target 0%) =====================
    # Non fa parte del PAC da 100 €: ci arrivano SOLO gli arrotondamenti e il
    # saveback della carta Trade Republic, che comprano qui a inizio mese. Il
    # target a 0 lo tiene fuori dalla ripartizione (versamenti._riparti scarta
    # chi ha pct_target 0), così la somma degli altri resta 100%.
    ("iShares Physical Gold ETC", TIPO_ETF, "Oro", "EGLN", "IE00B4ND3602", 0.0, None,
     "Destinazione degli arrotondamenti e del saveback della carta Trade Republic. "
     "Tecnicamente un ETC (oro fisico), non un ETF: qui sta fra gli ETF perché il "
     "sistema ha due soli tipi."),
]


def applica_nomi_brevi() -> int:
    """Compila `nome_breve` (solo se vuoto) per i ticker noti: così anche un DB
    già popolato riceve i nomi corti, senza sovrascrivere personalizzazioni."""
    with SessionLocal() as db:
        n = 0
        for p in db.query(Position).all():
            breve = NOMI_BREVI.get((p.ticker or "").upper(), "")
            if breve and not (p.nome_breve or "").strip():
                p.nome_breve = breve
                n += 1
        if n:
            db.commit()
        return n


def assicura_posizioni_mancanti() -> int:
    """Aggiunge le posizioni del SEED che il DB non ha ancora (confronto per ISIN).

    Serve ai database già popolati, dove `seed_if_empty` non fa nulla: un titolo
    nuovo in lista deve comparire anche a chi usa l'app da mesi. Solo AGGIUNTE —
    a differenza di `allinea_al_seed` non aggiorna e non cancella niente, quindi
    non può toccare i dati personali (quantità, note, ordine scelto da te)."""
    with SessionLocal() as db:
        if db.query(Position).first() is None:
            return 0                      # DB vuoto: ci pensa seed_if_empty
        gia = {(p.isin or "").strip().upper() for p in db.query(Position).all()}
        prossimo = max((p.ordine or 0) for p in db.query(Position).all()) + 1
        n = 0
        for nome, tipo, cat, ticker, isin, pct, fisso, note in SEED:
            if isin.upper() in gia:
                continue
            db.add(Position(nome=nome, nome_breve=NOMI_BREVI.get(ticker, ""),
                            tipo=tipo, categoria=cat, ticker=ticker, isin=isin,
                            pct_target=pct, importo_fisso=fisso, note=note,
                            ordine=prossimo + n))
            n += 1
        if n:
            db.commit()
        return n


def seed_if_empty() -> int:
    """Inserisce il portafoglio iniziale solo se la tabella e' vuota.

    Ritorna il numero di posizioni inserite (0 se c'erano gia' dei dati: in quel
    caso non tocca nulla, per non sovrascrivere le tue modifiche).
    """
    with SessionLocal() as db:
        if db.query(Position).first() is not None:
            return 0
        for i, (nome, tipo, cat, ticker, isin, pct, fisso, note) in enumerate(SEED):
            db.add(Position(
                nome=nome, nome_breve=NOMI_BREVI.get(ticker, ""), tipo=tipo,
                categoria=cat, ticker=ticker, isin=isin,
                pct_target=pct, importo_fisso=fisso, note=note, ordine=i,
            ))
        db.commit()
        return len(SEED)


def allinea_al_seed() -> dict:
    """Allinea il DB al SEED per ISIN: aggiorna target/anagrafica, aggiunge le
    posizioni nuove, rimuove quelle non più in lista. PRESERVA i dati personali
    (quantità, valore posseduto, data ultimo acquisto, note dell'utente).
    Da usare quando l'utente comunica un nuovo portafoglio target."""
    per_isin = {isin: (nome, tipo, cat, ticker, pct, fisso, note)
                for nome, tipo, cat, ticker, isin, pct, fisso, note in SEED}
    agg = nuovi = rimossi = 0
    with SessionLocal() as db:
        esistenti = {(p.isin or "").upper(): p for p in db.query(Position).all()}
        for i, (nome, tipo, cat, ticker, isin, pct, fisso, note) in enumerate(SEED):
            p = esistenti.get(isin.upper())
            if p is None:
                db.add(Position(nome=nome, nome_breve=NOMI_BREVI.get(ticker, ""),
                                tipo=tipo, categoria=cat, ticker=ticker,
                                isin=isin, pct_target=pct, importo_fisso=fisso,
                                note=note, ordine=i))
                nuovi += 1
            else:
                p.nome, p.tipo, p.categoria, p.ticker = nome, tipo, cat, ticker
                p.nome_breve = NOMI_BREVI.get(ticker, "")
                p.pct_target, p.importo_fisso, p.ordine = pct, fisso, i
                agg += 1
        for isin, p in esistenti.items():
            if isin not in {k.upper() for k in per_isin}:
                db.delete(p)
                rimossi += 1
        db.commit()
    return {"aggiornate": agg, "nuove": nuovi, "rimosse": rimossi}
