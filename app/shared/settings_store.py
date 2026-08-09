"""Impostazioni dell'app salvate nel database locale (coppie chiave-valore).

Qui dentro finiscono anche le CHIAVI API (es. Gemini). Stanno solo nel file del
database sul tuo PC, che NON viene mai caricato su GitHub (.gitignore).
Mai hardcoded, mai stampate nei log.

Funziona anche del tutto senza chiavi: le funzioni extra si sbloccano quando una
chiave viene inserita dalla pagina Impostazioni.
"""
from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, SessionLocal

# Elenco delle chiavi/opzioni riconosciute. 'tkey' = chiave di traduzione (i18n)
# dell'etichetta; 'secret' = mostrata mascherata nell'interfaccia e mai loggata.
KNOWN_SETTINGS = {
    "gemini_api_key":  {"tkey": "set.key_gemini", "secret": True},
    # Qui stavano le credenziali OAuth di Google Drive, che serviva alla copia a
    # specchio telefono↔PC. Con l'app sul cloud non c'è più niente da
    # sincronizzare (Fase 5): via le caselle e via il codice. Le righe eventualmente
    # rimaste nel database non danno fastidio e non vengono più lette.
    # Vertex AI (provider alternativo dell'agente): il JSON della chiave del
    # service account è un SEGRETO. Progetto e regione, non segreti, sono gestiti
    # a parte in settings_routes (semplici caselle di testo). Guida SETUP-VERTEX.md.
    "vertex_service_account_json": {"tkey": "set.vertex_sa", "secret": True},
}


class Setting(Base):
    __tablename__ = "shared_settings"
    chiave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valore: Mapped[str] = mapped_column(Text, default="")


def get_setting(chiave: str, default: str = "") -> str:
    with SessionLocal() as db:
        row = db.get(Setting, chiave)
        return row.valore if row else default


def set_setting(chiave: str, valore: str) -> None:
    with SessionLocal() as db:
        row = db.get(Setting, chiave)
        if row is None:
            row = Setting(chiave=chiave, valore=valore)
            db.add(row)
        else:
            row.valore = valore
        db.commit()


def all_settings() -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(Setting)).scalars().all()
        return {r.chiave: r.valore for r in rows}


def get_many(chiavi) -> dict:
    """Piu' impostazioni in UNA query (per il contesto di ogni pagina)."""
    with SessionLocal() as db:
        rows = db.execute(select(Setting).where(Setting.chiave.in_(list(chiavi)))).scalars().all()
        return {r.chiave: r.valore for r in rows}


def has_key(chiave: str) -> bool:
    """True se una chiave API è presente e non vuota (per sbloccare funzioni)."""
    return bool(get_setting(chiave, "").strip())


def masked(valore: str) -> str:
    """Mostra una chiave in modo sicuro: '••••••••1234'."""
    valore = (valore or "").strip()
    if not valore:
        return ""
    if len(valore) <= 4:
        return "••••"
    return "•" * 8 + valore[-4:]
