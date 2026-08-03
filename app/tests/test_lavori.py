"""I lavori periodici e la serratura dell'indirizzo che li fa partire.

Il rischio qui non è che il lavoro non parta: è che parta per chiunque. Questi
test provano soprattutto i **no**.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from shared import lavori


# ── la serratura ────────────────────────────────────────────────────────────

def test_senza_parola_dordine_non_passa_nessuno(monkeypatch):
    """Se non è configurata, si dice NO a tutti — anche a chi manda stringa vuota.
    È il caso del PC di casa, dove l'indirizzo non deve funzionare affatto."""
    import shared.config as cfg
    monkeypatch.setattr(cfg, "JOB_TOKEN", "")
    assert lavori.token_valido("") is False
    assert lavori.token_valido("qualsiasi-cosa") is False
    assert lavori.token_valido(None) is False


def test_solo_la_parola_giusta_passa(monkeypatch):
    import shared.config as cfg
    monkeypatch.setattr(cfg, "JOB_TOKEN", "parola-segreta-123")
    assert lavori.token_valido("parola-segreta-123") is True
    assert lavori.token_valido("parola-segreta-124") is False
    assert lavori.token_valido("parola-segreta-12") is False
    assert lavori.token_valido("PAROLA-SEGRETA-123") is False
    assert lavori.token_valido("") is False


# ── i lavori ────────────────────────────────────────────────────────────────

def test_un_passo_che_fallisce_non_ferma_gli_altri(monkeypatch):
    """Se Yahoo è giù, la fotografia del patrimonio si deve scattare lo stesso."""
    chiamati = []

    def esplode():
        raise RuntimeError("fonte non raggiungibile")

    monkeypatch.setattr("news.reader.refresh_from_origin", esplode)
    monkeypatch.setattr("portfolio.market.refresh_all", esplode)
    monkeypatch.setattr("portfolio.market.refresh_all_fundamentals",
                        lambda: chiamati.append("fondamentali"))
    monkeypatch.setattr("portfolio.wealth.get_cached", lambda: chiamati.append("grafico"))
    monkeypatch.setattr("finance.service.compatta_tombstone", lambda g: chiamati.append("pulizia"))
    monkeypatch.setattr("shared.storico.registra", lambda: chiamati.append("storico"))

    esito = lavori.giornaliero(includi_sync=False)

    assert esito["passi"]["notizie"].startswith("errore")
    assert esito["passi"]["prezzi"].startswith("errore")
    assert esito["passi"]["storico"] == "ok"
    assert "storico" in chiamati, "la fotografia deve scattare anche se i prezzi falliscono"


def test_niente_sync_quando_non_richiesto(monkeypatch):
    """Su un server il database è uno solo: il sync non deve nemmeno partire."""
    for nome in ("news.reader.refresh_from_origin", "portfolio.market.refresh_all",
                 "portfolio.market.refresh_all_fundamentals", "portfolio.wealth.get_cached",
                 "shared.storico.registra"):
        monkeypatch.setattr(nome, lambda *a, **k: None)
    monkeypatch.setattr("finance.service.compatta_tombstone", lambda g: None)

    esito = lavori.giornaliero(includi_sync=False)
    assert "sync_drive" not in esito["passi"]

    esito = lavori.giornaliero(includi_sync=True)
    assert "sync_drive" in esito["passi"]


def test_due_chiamate_insieme_non_fanno_il_lavoro_doppio(monkeypatch):
    """Su un server possono girare più copie dell'app: la seconda chiamata deve
    accorgersi che il lavoro è già in corso e non rifarlo."""
    partenze = []

    def lento():
        partenze.append(1)
        time.sleep(0.4)

    for nome in ("news.reader.refresh_from_origin", "portfolio.market.refresh_all_fundamentals",
                 "portfolio.wealth.get_cached", "shared.storico.registra"):
        monkeypatch.setattr(nome, lambda *a, **k: None)
    monkeypatch.setattr("finance.service.compatta_tombstone", lambda g: None)
    monkeypatch.setattr("portfolio.market.refresh_all", lento)

    esiti = {}

    def primo():
        esiti["a"] = lavori.giornaliero(includi_sync=False)

    t = threading.Thread(target=primo)
    t.start()
    time.sleep(0.1)                      # il primo è già dentro
    esiti["b"] = lavori.giornaliero(includi_sync=False)
    t.join()

    assert "saltato" in esiti["b"], "la seconda chiamata doveva essere scartata"
    assert len(partenze) == 1, "il lavoro pesante è stato fatto due volte"


def test_il_lucchetto_si_riapre_anche_se_qualcosa_esplode(monkeypatch):
    """Se un lavoro va storto il lucchetto deve tornare aperto, altrimenti
    nessun lavoro partirebbe mai più fino al riavvio."""
    def esplode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("news.reader.refresh_from_origin", esplode)
    monkeypatch.setattr("portfolio.market.refresh_all", esplode)
    monkeypatch.setattr("portfolio.market.refresh_all_fundamentals", esplode)
    monkeypatch.setattr("portfolio.wealth.get_cached", esplode)
    monkeypatch.setattr("finance.service.compatta_tombstone", esplode)
    monkeypatch.setattr("shared.storico.registra", esplode)

    lavori.giornaliero(includi_sync=False)
    secondo = lavori.giornaliero(includi_sync=False)
    assert "saltato" not in secondo, "il lucchetto e' rimasto chiuso"
