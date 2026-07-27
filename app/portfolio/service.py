"""Logica del portafoglio: calcolatore PAC e riepiloghi.

Tutto OFFLINE in Fase 1. Niente segnali operativi, niente 'compra/vendi':
l'app calcola e mostra, la decisione resta sempre dell'utente.
"""
import json
from datetime import date

from sqlalchemy import select

from shared.db import SessionLocal
from shared import settings_store
from portfolio.models import Position
from portfolio import market

# ---------------------------------------------------------------------------
#  ALLINEAMENTO A TRADE REPUBLIC
#  I nostri prezzi vengono da Yahoo e per alcuni ETF europei la linea di borsa
#  è sottile (GIFL su Stoccarda vale 6,37 € da noi e 4,01 € su TR): il totale
#  che calcoliamo è una stima, e su qualche titolo è una stima storta.
#  L'utente però UN numero esatto ce l'ha: il totale che legge sul broker.
#  Con quello si fissa il totale e si riscalano le stime per titolo, con un
#  fattore unico e SEMPRE dichiarato. Non è un numero inventato: è il nostro,
#  corretto da un dato vero, e la pagina dice quando è stato preso.
# ---------------------------------------------------------------------------
CHIAVE_TR = "tr_allineamento"
GIORNI_TR_VECCHIO = 14        # oltre, l'allineamento va rinfrescato
FATTORE_TR_MAX = 3.0          # scarto oltre il quale è più probabile un errore di battitura


def allineamento_tr() -> dict | None:
    """Il totale letto su Trade Republic, con la data. None se non c'è."""
    raw = settings_store.get_setting(CHIAVE_TR, "")
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return {"totale": float(d.get("totale") or 0.0),
                "data": date.fromisoformat(d.get("data")),
                "attivo": bool(d.get("attivo", True))}
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def salva_allineamento_tr(totale: float, quando: date = None, attivo: bool = True) -> None:
    """Registra il totale del broker. `totale` <= 0 cancella l'allineamento."""
    if not totale or totale <= 0:
        settings_store.set_setting(CHIAVE_TR, "")
        return
    settings_store.set_setting(CHIAVE_TR, json.dumps({
        "totale": round(float(totale), 2),
        "data": (quando or date.today()).isoformat(),
        "attivo": bool(attivo)}))


def _info_tr(totale_stimato: float) -> dict | None:
    """Fattore di correzione e stato dell'allineamento, o None se non applicabile."""
    tr = allineamento_tr()
    if not tr or totale_stimato <= 0 or tr["totale"] <= 0:
        return None
    fattore = tr["totale"] / totale_stimato
    giorni = (date.today() - tr["data"]).days
    return {
        "totale": round(tr["totale"], 2),
        "data": tr["data"],
        "giorni": giorni,
        "vecchio": giorni > GIORNI_TR_VECCHIO,
        "stimato": round(totale_stimato, 2),
        "scarto_eur": round(tr["totale"] - totale_stimato, 2),
        "scarto_pct": round((fattore - 1) * 100, 2),
        "fattore": fattore,
        # uno scarto mostruoso è quasi sempre una virgola sbagliata, non il
        # mercato: meglio mostrarlo e NON applicarlo che riscalare tutto per 40
        "assurdo": fattore > FATTORE_TR_MAX or fattore < 1 / FATTORE_TR_MAX,
        "attivo": bool(tr["attivo"]),
    }


def lista_posizioni() -> list[Position]:
    with SessionLocal() as db:
        return list(db.execute(
            select(Position).order_by(Position.ordine, Position.id)
        ).scalars().all())


def somma_target() -> float:
    """Somma delle % target (deve fare 100; gli asset a importo fisso non contano)."""
    return round(sum(p.pct_target for p in lista_posizioni() if not p.is_fisso), 4)


def calcola_pac(importo_mensile: float) -> dict:
    """Ripartisce l'importo mensile fra gli asset secondo la % target.

    - quota per asset = importo_mensile x % target (arrotondata al centesimo)
    - asset a importo fisso (Take-Two): quota fissa, con % implicita a parte
    - controllo arrotondamenti: scostamento fra somma quote e importo
    - controllo allocazione: la somma delle % deve fare 100
    """
    posizioni = lista_posizioni()
    importo = max(0.0, float(importo_mensile or 0))

    righe, righe_fisse = [], []
    somma_pct = 0.0
    somma_quote = 0.0
    somma_fissi = 0.0

    for p in posizioni:
        if p.is_fisso:
            implicita = (p.importo_fisso / importo * 100) if importo > 0 else 0.0
            somma_fissi += p.importo_fisso
            righe_fisse.append({
                "nome": p.nome_vista, "ticker": p.ticker, "categoria": p.categoria,
                "importo": round(p.importo_fisso, 2), "pct_implicita": implicita,
            })
        else:
            quota = round(importo * p.pct_target / 100.0, 2)
            somma_pct += p.pct_target
            somma_quote += quota
            righe.append({
                "nome": p.nome_vista, "ticker": p.ticker, "tipo": p.tipo,
                "categoria": p.categoria, "pct_target": p.pct_target, "quota": quota,
            })

    somma_quote = round(somma_quote, 2)
    scostamento = round(somma_quote - importo, 2)   # per arrotondamenti ai centesimi
    return {
        "importo_mensile": round(importo, 2),
        "righe": righe,
        "righe_fisse": righe_fisse,
        "somma_pct": round(somma_pct, 4),
        "somma_quote": somma_quote,
        "somma_fissi": round(somma_fissi, 2),
        "scostamento": scostamento,
        "totale_mensile": round(somma_quote + somma_fissi, 2),
        "pct_ok": abs(somma_pct - 100.0) < 0.01,
        "n_asset": len(righe),
    }


def _valore_riga(p, prezzo_eur):
    """Valore di una posizione: quantita x prezzo (live) se possibile, altrimenti
    il valore inserito a mano. None se non si sa."""
    if prezzo_eur is not None and p.quantita:
        return round(prezzo_eur * p.quantita, 2)
    if p.valore_posseduto:
        return round(p.valore_posseduto, 2)
    return None


def vista_portafoglio() -> dict:
    """Posizioni arricchite con prezzo corrente (in euro) e valore, piu' il totale.

    I prezzi arrivano dalla cache locale (aggiornata da market.refresh_all). Se un
    prezzo non c'e', la riga lo segnala: niente valori inventati.

    Se c'è un allineamento a Trade Republic attivo, il totale viene FISSATO su
    quello e le stime per titolo riscalate con lo stesso fattore. Questo è
    l'unico punto dell'app in cui succede — così ogni pagina, l'agente e i fatti
    lavorano sullo stesso numero — e `vista["tr"]` porta con sé tutto quello che
    serve per dichiararlo. Il VERSATO non si tocca mai: quello è già esatto.
    """
    posizioni = lista_posizioni()
    qmap = market.quotes_map()
    righe = []
    totale = 0.0
    for p in posizioni:
        q = qmap.get((p.ticker or "").upper())
        prezzo_eur = q.price_eur if (q and q.ok) else None
        valore = _valore_riga(p, prezzo_eur)
        if valore:
            totale += valore
        righe.append({"p": p, "q": q, "prezzo_eur": prezzo_eur,
                      "valore": valore, "valore_stimato": valore})

    tr = _info_tr(totale)
    if tr and tr["attivo"] and not tr["assurdo"]:
        for r in righe:
            if r["valore"]:
                r["valore"] = round(r["valore"] * tr["fattore"], 2)
        totale = tr["totale"]

    ultimo = market.last_update()
    return {
        "righe": righe,
        "totale": round(totale, 2),
        "ha_totale": totale > 0,
        "ultimo_agg": market.fmt_ts(ultimo),
        "prezzi": market.stato_prezzi(),
        "tr": tr,
        "n_prezzi": sum(1 for r in righe if r["prezzo_eur"] is not None),
        "n_ticker": sum(1 for p in posizioni if (p.ticker or "").strip()),
    }


# ---------------------------------------------------------------------------
#  PREZZO MEDIO DI CARICO
#  Il numero che Trade Republic ti mette davanti e che qui non c'era, benché il
#  dato per calcolarlo fosse già tutto salvato: ogni VersamentoRiga conserva
#  quanti euro e quante quote. PMC = euro spesi / quote comprate, sulle sole
#  righe in cui il prezzo era noto (le altre non hanno quote da mediare).
# ---------------------------------------------------------------------------
def pmc_map() -> dict:
    """{position_id: {'qta', 'euro', 'pmc'}} dai versamenti registrati."""
    from portfolio.models import VersamentoRiga

    acc: dict[int, dict] = {}
    with SessionLocal() as db:
        for r in db.execute(select(VersamentoRiga)).scalars().all():
            if not r.qta:
                continue            # prezzo n/d quel giorno: niente quote, niente media
            a = acc.setdefault(r.position_id, {"qta": 0.0, "euro": 0.0})
            a["qta"] += r.qta
            a["euro"] += r.euro or 0.0
    for a in acc.values():
        a["pmc"] = round(a["euro"] / a["qta"], 4) if a["qta"] else None
        a["qta"] = round(a["qta"], 8)
        a["euro"] = round(a["euro"], 2)
    return acc


def riepilogo(vista: dict | None = None) -> dict:
    """Numeri di sintesi per la dashboard (una sola passata sulle posizioni).
    Se la pagina ha già calcolato la vista, la riusa senza rifare le query."""
    vista = vista or vista_portafoglio()
    posizioni = [r["p"] for r in vista["righe"]]
    somma = round(sum(p.pct_target for p in posizioni if not p.is_fisso), 4)
    return {
        "n_posizioni": len(posizioni),
        "n_etf": sum(1 for p in posizioni if p.tipo == "ETF"),
        "n_azioni": sum(1 for p in posizioni if p.tipo == "Azione"),
        "somma_target": somma,
        "target_ok": abs(somma - 100.0) < 0.01,
        "valore_totale": vista["totale"],
        "ha_valori": vista["ha_totale"],
        "ultimo_agg": vista["ultimo_agg"],
        "n_prezzi": vista["n_prezzi"],
        "n_ticker": vista["n_ticker"],
    }
