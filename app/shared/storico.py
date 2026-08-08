"""Lo storico dei numeri: una riga al giorno, per poter chiedersi «com'era prima».

Finora l'app teneva solo l'ULTIMO valore di tutto: `risk_snapshot`,
`perf12m_snapshot`, `wealth_cache` si sovrascrivono a ogni aggiornamento. Il
grafico del patrimonio ricostruisce il passato dai prezzi storici, ma nessuno
conservava il resto: quanto avevi versato, che risultato facevi, quanti titoli
avevi, quanto spendevi. Domande semplici come «il mio risultato è migliorato
rispetto a settimana scorsa?» non avevano risposta, e non per un limite del
calcolo: per mancanza di memoria.

Questa è anche la ragione per cui l'agente ha spesso poco da dire. Tutto
`insights.py` è costruito su confronti col passato dell'utente — ma il passato
non veniva salvato, così restavano solo i confronti fra mesi civili, che
all'inizio della vita dell'app non esistono ancora.

Una riga al giorno costa qualche decina di byte. Il valore del giorno è
l'ULTIMO noto di quel giorno (si sovrascrive): non è un prezzo di chiusura, è
la fotografia più recente che l'app aveva quando l'hai aperta. Se un giorno non
apri l'app, quel giorno semplicemente non c'è — e va bene così: è un buco vero,
non uno da riempire con una stima.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import Date, DateTime, Float, Integer, select
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, SessionLocal
from shared import tempo


class GiornoStorico(Base):
    """I numeri chiave di un giorno. La data è la chiave: una riga per giorno."""
    __tablename__ = "storico_giornaliero"

    data: Mapped[date] = mapped_column(Date, primary_key=True)
    patrimonio: Mapped[float] = mapped_column(Float, default=0.0)
    liquido: Mapped[float] = mapped_column(Float, default=0.0)
    investito: Mapped[float] = mapped_column(Float, default=0.0)
    versato: Mapped[float] = mapped_column(Float, default=0.0)
    risultato_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    entrate_mese: Mapped[float] = mapped_column(Float, default=0.0)
    uscite_mese: Mapped[float] = mapped_column(Float, default=0.0)
    n_titoli: Mapped[int] = mapped_column(Integer, default=0)
    aggiornato: Mapped[datetime] = mapped_column(DateTime, default=tempo.adesso)


def _misura(oggi: date) -> dict:
    """I numeri di adesso. Se qualcosa non è disponibile resta a zero/None:
    mai un valore inventato per riempire una colonna."""
    from finance import service as fin
    from portfolio import service as pf

    vista = pf.vista_portafoglio()
    sal = fin.saldi()
    riep = fin.riepilogo_mese(oggi.year, oggi.month)
    investito = vista["totale"] if vista["ha_totale"] else 0.0
    versato = round(sum((r["p"].versato_totale or 0.0) for r in vista["righe"]), 2)
    risultato = round(investito - versato, 2) if (investito and versato) else None
    return {
        "patrimonio": round(investito + sal["liquido"], 2),
        "liquido": round(sal["liquido"], 2),
        "investito": round(investito, 2),
        "versato": versato,
        "risultato_eur": risultato,
        "entrate_mese": riep["entrate"],
        "uscite_mese": riep["uscite"],
        "n_titoli": sum(1 for r in vista["righe"] if r["valore"]),
    }


def registra(oggi: date = None) -> dict | None:
    """Scrive (o riscrive) la riga di oggi. Idempotente: chiamala quanto vuoi."""
    oggi = oggi or tempo.oggi()
    try:
        valori = _misura(oggi)
    except Exception:
        return None            # niente dati = niente riga: mai una riga a zero finta
    with SessionLocal() as db:
        g = db.get(GiornoStorico, oggi) or GiornoStorico(data=oggi)
        for k, v in valori.items():
            setattr(g, k, v)
        g.aggiornato = tempo.adesso()
        db.add(g)
        db.commit()
    return valori


def serie(giorni: int = 90) -> list[dict]:
    """Le righe degli ultimi `giorni`, dalla più vecchia alla più recente."""
    da = tempo.oggi() - timedelta(days=max(1, giorni))
    with SessionLocal() as db:
        righe = list(db.execute(
            select(GiornoStorico).where(GiornoStorico.data >= da)
            .order_by(GiornoStorico.data)).scalars().all())
    return [{
        "data": g.data, "patrimonio": g.patrimonio, "liquido": g.liquido,
        "investito": g.investito, "versato": g.versato,
        "risultato_eur": g.risultato_eur, "entrate_mese": g.entrate_mese,
        "uscite_mese": g.uscite_mese, "n_titoli": g.n_titoli,
    } for g in righe]


def giorni_disponibili() -> int:
    """Quante giornate abbiamo davvero in archivio. Serve a NON promettere
    confronti che non possiamo fare: con due righe non esiste «la settimana
    scorsa»."""
    with SessionLocal() as db:
        return db.query(GiornoStorico).count()


def confronto(giorni: int = 7) -> dict | None:
    """Oggi contro ~`giorni` fa, se in archivio c'è una riga abbastanza vecchia.

    Si prende la riga più recente fra quelle vecchie almeno `giorni`: se apri
    l'app a giorni alterni il confronto resta possibile, e diciamo su quanti
    giorni è davvero calcolato invece di far finta che siano 7."""
    oggi = tempo.oggi()
    limite = oggi - timedelta(days=giorni)
    with SessionLocal() as db:
        ora = db.execute(select(GiornoStorico)
                         .order_by(GiornoStorico.data.desc())).scalars().first()
        prima = db.execute(
            select(GiornoStorico).where(GiornoStorico.data <= limite)
            .order_by(GiornoStorico.data.desc())).scalars().first()
    if ora is None or prima is None or ora.data == prima.data:
        return None
    return {
        "giorni": (ora.data - prima.data).days,
        "da": prima.data, "a": ora.data,
        "patrimonio": round(ora.patrimonio - prima.patrimonio, 2),
        "investito": round(ora.investito - prima.investito, 2),
        "liquido": round(ora.liquido - prima.liquido, 2),
        "versato": round(ora.versato - prima.versato, 2),
        # la parte di variazione che NON viene da nuovi versamenti: è il mercato
        "mercato": round((ora.investito - prima.investito)
                         - (ora.versato - prima.versato), 2),
        "risultato_eur": (round((ora.risultato_eur or 0) - (prima.risultato_eur or 0), 2)
                          if ora.risultato_eur is not None and prima.risultato_eur is not None
                          else None),
    }
