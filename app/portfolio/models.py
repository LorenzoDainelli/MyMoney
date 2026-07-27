"""Tabella delle posizioni del portafoglio.

Una 'posizione' = un titolo che vuoi seguire (ETF o azione) con la sua quota
target. I campi di mercato (prezzo, ecc.) NON stanno qui: vivono in market.py
in tabelle separate, così i tuoi dati restano puliti e mai 'inventati'.
"""
from datetime import date, datetime, timezone

from sqlalchemy import String, Float, Integer, Date, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base

TIPO_ETF = "ETF"
TIPO_AZIONE = "Azione"


class Position(Base):
    __tablename__ = "portfolio_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- anagrafica (inserita/modificata da te dall'interfaccia) ---
    nome: Mapped[str] = mapped_column(String(200))
    # nome corto per le tabelle (es. "Global" per IWDA); il nome ufficiale
    # completo resta in `nome` e si vede nella scheda di dettaglio
    nome_breve: Mapped[str] = mapped_column(String(80), default="")
    tipo: Mapped[str] = mapped_column(String(20), default=TIPO_AZIONE)   # ETF | Azione
    categoria: Mapped[str] = mapped_column(String(120), default="")      # tema/settore
    ticker: Mapped[str] = mapped_column(String(30), default="")
    isin: Mapped[str] = mapped_column(String(20), default="")

    # --- allocazione ---
    # Quota target in %. Per i titoli a importo fisso (Take-Two) resta 0 e si usa
    # importo_fisso: l'app mostra a parte la % implicita.
    pct_target: Mapped[float] = mapped_column(Float, default=0.0)
    importo_fisso: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- quanto possiedi davvero (lo riempi tu, può restare vuoto) ---
    quantita: Mapped[float | None] = mapped_column(Float, nullable=True)
    valore_posseduto: Mapped[float | None] = mapped_column(Float, nullable=True)  # in €
    data_ultimo_acquisto: Mapped[date | None] = mapped_column(Date, nullable=True)
    # totale versato su questo titolo (somma dei PAC): esatto, non dipende dai prezzi
    versato_totale: Mapped[float] = mapped_column(Float, default=0.0)

    note: Mapped[str] = mapped_column(Text, default="")
    ordine: Mapped[int] = mapped_column(Integer, default=0)  # ordine di visualizzazione

    @property
    def is_fisso(self) -> bool:
        return self.importo_fisso is not None

    @property
    def nome_vista(self) -> str:
        """Nome da mostrare in elenco: quello corto se c'è, altrimenti l'ufficiale."""
        return (self.nome_breve or "").strip() or self.nome


class Versamento(Base):
    """Un versamento PAC: un acquisto distribuito su più titoli in una data.

    È l'evento (data, importo, conto di provenienza). Il dettaglio per titolo
    (quanto e quante quote) sta nelle `VersamentoRiga`, così il PAC è
    modificabile ed eliminabile e le quantità delle posizioni si possono
    annullare esattamente."""
    __tablename__ = "portfolio_versamenti"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[date] = mapped_column(Date)
    ora: Mapped[str] = mapped_column(String(5), default="")      # "HH:MM" locale, facoltativa
    importo: Mapped[float] = mapped_column(Float, default=0.0)   # totale investito (€)
    conto: Mapped[str] = mapped_column(String(80), default="")   # conto di provenienza (informativo)
    note: Mapped[str] = mapped_column(Text, default="")
    # movimento di Finanze collegato: il TRASFERIMENTO dal conto di provenienza
    # al portafoglio "PAC investimenti". Uno solo per versamento (viene
    # aggiornato/eliminato insieme al PAC, mai duplicato).
    tx_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    creato_il: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class VersamentoRiga(Base):
    """Una riga di un versamento: quanto è finito su un titolo e quante quote.

    Conserva il DELTA applicato alla posizione, così eliminare/modificare il
    versamento ripristina esattamente le quantità."""
    __tablename__ = "portfolio_versamento_righe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    versamento_id: Mapped[int] = mapped_column(Integer, index=True)
    position_id: Mapped[int] = mapped_column(Integer, index=True)
    isin: Mapped[str] = mapped_column(String(20), default="")
    ticker: Mapped[str] = mapped_column(String(30), default="")
    euro: Mapped[float] = mapped_column(Float, default=0.0)        # € destinati a questo titolo
    qta: Mapped[float | None] = mapped_column(Float, nullable=True)  # quote aggiunte (None se prezzo n/d)
    prezzo_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    fonte: Mapped[str] = mapped_column(String(16), default="")     # live | storico | n/d
