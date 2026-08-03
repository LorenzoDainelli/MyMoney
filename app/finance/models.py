"""Tabelle delle finanze personali: portafogli (conti/wallet), categorie, movimenti.

Quattro tipi di movimento:
- entrata        -> aumenta il saldo del wallet
- uscita         -> diminuisce il saldo del wallet
- trasferimento  -> sposta denaro da un wallet all'altro (NON cambia il patrimonio
                    totale, cambia solo i due saldi)
- giro           -> "partita di giro": spese che qualcuno rimborsa (es. la pagano
                    i genitori). Una partita può avere PIÙ spese e PIÙ rientri, su
                    portafogli/date/persone diversi: sono più righe che condividono
                    lo stesso `giro_id`. Ogni riga è una gamba:
                    · SPESA   -> importo>0, wallet_id, categoria/descrizione/data
                                 (importo_ricevuto NULL)
                    · RIENTRO -> importo_ricevuto>0, wallet_to_id (dove entra),
                                 controparte (da chi), data_ricevuto (importo=0)
                    (le vecchie partite a riga singola hanno le due gambe insieme:
                    importo>0 E importo_ricevuto>0 — restano valide, sono un gruppo
                    di una riga sola.)
                    I saldi si muovono davvero riga per riga; nelle statistiche
                    entrate/uscite conta SOLO la differenza netta della partita
                    (Σ rientri − Σ spese) e solo quando la partita è CHIUSA:
                    netto > 0 = entrata (all'ultimo rientro),
                    netto < 0 = uscita (all'ultima spesa).
                    `giro_aperta` = partita ancora "in attesa di rimborso": i saldi
                    sono già aggiornati ma il netto NON conta finché non la chiudi.

Prefisso tabelle 'finance_' per non interferire col modulo portafoglio.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime, Text, Boolean, ForeignKey, event
from sqlalchemy.orm import Mapped, mapped_column, Session

from shared.db import Base

TIPO_ENTRATA = "entrata"
TIPO_USCITA = "uscita"
TIPO_TRASFERIMENTO = "trasferimento"
TIPO_GIRO = "giro"                     # partita di giro (spesa rimborsata)
TIPI_MOVIMENTO = (TIPO_ENTRATA, TIPO_USCITA, TIPO_TRASFERIMENTO, TIPO_GIRO)

# Tipi di wallet (per etichetta/icona); valori liberi, questi sono i noti.
TIPI_WALLET = ("contanti", "carta", "conto", "investimento", "altro")


class Wallet(Base):
    __tablename__ = "finance_wallets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    tipo: Mapped[str] = mapped_column(String(20), default="altro")
    saldo_iniziale: Mapped[float] = mapped_column(Float, default=0.0)  # saldo di apertura
    note: Mapped[str] = mapped_column(Text, default="")
    ordine: Mapped[int] = mapped_column(Integer, default=0)
    archiviato: Mapped[bool] = mapped_column(Boolean, default=False)
    # accento brand della card (design: strisciolina in alto + chip + barra);
    # vuoto = card neutra con i colori standard del tema
    colore: Mapped[str] = mapped_column(String(20), default="")
    # --- carta con arrotondamento (Trade Republic) ---
    # Ogni pagamento con la carta arrotonda al PROSSIMO euro (anche sulle cifre
    # tonde: 8,00 -> 9,00) e matura l'1% di saveback. Sono due cose diverse: la
    # differenza è denaro TUO che cambia tasca, il saveback è denaro della banca
    # che entra. Qui si conserva solo la regola; gli importi restano correggibili
    # movimento per movimento, perché la banca non è tenuta a fare i nostri conti.
    arrotonda: Mapped[bool] = mapped_column(Boolean, default=False)
    saveback_pct: Mapped[float] = mapped_column(Float, default=0.0)   # % (1.0 = 1%)
    saveback_tetto: Mapped[float] = mapped_column(Float, default=0.0)  # €/mese, 0 = nessuno
    uid: Mapped[str] = mapped_column(String(32), default="", index=True)          # sync v2
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # sync v2
    rev: Mapped[int] = mapped_column(Integer, default=1)                          # sync v2
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)                 # sync v2 (tombstone)


class Category(Base):
    __tablename__ = "finance_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10), default="")   # "" | uscita | entrata
    archiviato: Mapped[bool] = mapped_column(Boolean, default=False)
    uid: Mapped[str] = mapped_column(String(32), default="", index=True)          # sync v2
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # sync v2
    rev: Mapped[int] = mapped_column(Integer, default=1)                          # sync v2
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)                 # sync v2 (tombstone)


class Transaction(Base):
    __tablename__ = "finance_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(15))               # entrata|uscita|trasferimento|giro
    data: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)   # ora locale (app locale)
    importo: Mapped[float] = mapped_column(Float, default=0.0)  # sempre positivo
    wallet_id: Mapped[int] = mapped_column(ForeignKey("finance_wallets.id"))
    # trasferimento: wallet di destinazione — giro: wallet dove entra il rimborso
    wallet_to_id: Mapped[int | None] = mapped_column(ForeignKey("finance_wallets.id"), nullable=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("finance_categories.id"), nullable=True)
    metodo: Mapped[str] = mapped_column(String(60), default="")  # legacy: non più usato (colonna lasciata per non migrare)
    descrizione: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # --- solo partite di giro (tipo == "giro") ---
    giro_id: Mapped[str] = mapped_column(String(32), default="")   # raggruppa le gambe di una partita
    giro_aperta: Mapped[bool] = mapped_column(Boolean, default=False)  # partita in attesa di rimborso
    importo_ricevuto: Mapped[float | None] = mapped_column(Float, nullable=True)   # gamba RIENTRO
    data_ricevuto: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    controparte: Mapped[str] = mapped_column(String(80), default="")  # da chi (babbo, mamma, ...)
    # --- righe generate da un altro movimento (arrotondamento e saveback) ---
    # Una spesa con la carta TR produce TRE righe: la spesa vera, il trasferimento
    # dell'arrotondamento, l'entrata del saveback. Le ultime due puntano qui alla
    # prima. Nel registro si vede solo il genitore (le figlie stanno nel dettaglio),
    # ma nei conti pesano tutte: nascoste dalla lista non vuol dire fuori dai totali.
    # Cancellare il genitore cancella le figlie: da sole non vorrebbero dire niente.
    parent_tx_id: Mapped[int | None] = mapped_column(
        ForeignKey("finance_transactions.id"), nullable=True, index=True)
    # come è nata la riga figlia: "arrotondamento" | "saveback" ("" per le altre)
    origine: Mapped[str] = mapped_column(String(20), default="")
    # --- metadati di sincronizzazione multi-dispositivo (v2, vedi PIANO-V2.md) ---
    uid: Mapped[str] = mapped_column(String(32), default="", index=True)          # identità stabile tra dispositivi
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # ultima modifica (per la fusione)
    rev: Mapped[int] = mapped_column(Integer, default=1)                          # versione del record (sale a ogni modifica)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)                 # tombstone (soft-delete attivo dalla Fase 4)

    @property
    def giro_kind(self) -> str | None:
        """Che gamba è questa riga di partita di giro:
        'spesa' | 'rientro' | 'combo' (vecchia riga con entrambe). None se non è un giro."""
        if self.tipo != TIPO_GIRO:
            return None
        ha_spesa = (self.importo or 0.0) > 0
        ha_rientro = self.importo_ricevuto is not None
        if ha_spesa and ha_rientro:
            return "combo"
        return "rientro" if ha_rientro else "spesa"

    @property
    def giro_importo_display(self) -> float:
        """Importo con segno da mostrare nel registro per questa gamba:
        −spesa, +rientro, o la differenza per le vecchie righe combo."""
        k = self.giro_kind
        if k == "rientro":
            return round(self.importo_ricevuto or 0.0, 2)
        if k == "combo":
            return round((self.importo_ricevuto or 0.0) - (self.importo or 0.0), 2)
        return round(-(self.importo or 0.0), 2)   # spesa


# ---------------------------------------------------------------------------
# Timbratura automatica dei metadati di sync (v2). Un solo punto centrale, così
# ogni riga porta con sé identità e versione a prescindere da CHI la modifica:
#   - creazione  -> uid (se manca), updated_at, rev=1
#   - modifica   -> updated_at aggiornato, rev incrementato
# È il fondamento della sincronizzazione multi-dispositivo (vedi PIANO-V2.md,
# Fase 4); qui NON cambia nulla di visibile: i dati si comportano come prima.
# ---------------------------------------------------------------------------
_MODELLI_SYNC = (Wallet, Category, Transaction)


def _sync_is_importing() -> bool:
    """True se sync.py sta importando (flag thread-local): in quel caso NON
    si devono ri-timbrare rev/updated_at (restano quelli del dispositivo sorgente).

    Il flag arriva da shared/sync_ctx.py e NON da shared/sync.py: quest'ultimo,
    all'import, registra i suoi agganci su SQLAlchemy, e visto che questa
    funzione gira DENTRO un salvataggio, importarlo qui aggiungerebbe un aggancio
    mentre SQLAlchemy sta percorrendo la lista degli agganci — che fallisce.
    Si vedeva solo al primissimo salvataggio su un database nuovo, quindi mai sul
    PC di casa e sempre su un server appena acceso."""
    from shared.sync_ctx import _is_importing
    return _is_importing()


@event.listens_for(Session, "before_flush")
def _timbra_metadati_sync(session, flush_context, instances):
    now = datetime.now()
    importing = _sync_is_importing()
    for obj in session.new:
        if isinstance(obj, _MODELLI_SYNC):
            if not getattr(obj, "uid", ""):
                obj.uid = uuid.uuid4().hex
            # Durante l'import i valori sono già settati dal dispositivo sorgente
            if not importing:
                if not getattr(obj, "updated_at", None):
                    obj.updated_at = now
                if not getattr(obj, "rev", None):
                    obj.rev = 1
    for obj in session.dirty:
        if isinstance(obj, _MODELLI_SYNC) and session.is_modified(obj, include_collections=False):
            if not importing:
                obj.updated_at = now
                obj.rev = (obj.rev or 0) + 1
