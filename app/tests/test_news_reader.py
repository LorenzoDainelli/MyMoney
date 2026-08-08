"""Sezione Notizie: mostra solo ciò che è arrivato per email.

Dalla Fase 0 il robot registra in `predictions.json` anche i candidati **non
inviati** (`inviata: false`), che gli servono come storico per verificare a
posteriori le proprie stime. Quelli NON devono comparire nell'app, altrimenti la
sezione si riempie di notizie marginali.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news import reader  # noqa: E402


def _voce(id_, inviata=None, rilevanza=70):
    it = {"id": id_, "ticker": "AAPL", "titolo": id_, "data": "2026-07-20",
          "rilevanza": rilevanza, "confidenza": "media", "url": f"http://x/{id_}",
          "impatto": {"breve": "positivo", "medio": "neutro", "lungo": "neutro"}}
    if inviata is not None:
        it["inviata"] = inviata
    return it


def test_le_non_inviate_non_si_vedono(monkeypatch):
    voci = [_voce("inviata", True), _voce("non-inviata", False)]
    monkeypatch.setattr(reader, "_read_items", lambda p: voci if "predictions" in str(p).lower() else [])
    titoli = [c["titolo"] for c in reader.news_cards()]
    assert titoli == ["inviata"]


def test_voci_vecchie_senza_il_campo_restano_visibili(monkeypatch):
    """Retrocompatibilità: prima della Fase 0 si registravano solo le inviate."""
    voci = [_voce("storica")]                      # nessun campo 'inviata'
    monkeypatch.setattr(reader, "_read_items", lambda p: voci if "predictions" in str(p).lower() else [])
    assert [c["titolo"] for c in reader.news_cards()] == ["storica"]


def test_la_data_aggiornato_ignora_le_non_inviate(monkeypatch):
    """L'etichetta 'aggiornato' non deve riferirsi a una notizia che non si vede."""
    vecchia = _voce("inviata", True)
    nuova = _voce("non-inviata", False)
    nuova["data"] = "2026-07-25"
    monkeypatch.setattr(reader, "_read_items",
                        lambda p: [vecchia, nuova] if "predictions" in str(p).lower() else [])
    assert reader.latest_date() == "20/07/2026"


# ── scaricare le notizie senza git (Fase 2 del piano cloud) ─────────────────
# Prima si faceva `git fetch` + `git show`. Nel container del server git non
# c'è, e metterlo servirebbe solo a leggere un file che GitHub serve già in
# HTTPS: le notizie sparivano, in silenzio, perché la funzione inghiotte tutto.

import io
import json
import urllib.error


class _Risposta(io.BytesIO):
    """Il minimo che serve a `with urlopen(...) as r: r.read()`."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _finta_rete(monkeypatch, corpo):
    visti = []

    def urlopen(req, timeout=None):
        visti.append(getattr(req, "full_url", req))
        if isinstance(corpo, Exception):
            raise corpo
        return _Risposta(corpo.encode("utf-8"))

    monkeypatch.setattr(reader.urllib.request, "urlopen", urlopen)
    return visti


def test_le_notizie_arrivano_via_https(monkeypatch, tmp_path):
    dentro = {"items": [_voce("dal-robot", True)]}
    visti = _finta_rete(monkeypatch, json.dumps(dentro))
    monkeypatch.setattr(reader, "REMOTE_CACHE", tmp_path / "news.json")

    assert reader.refresh_from_origin() is True
    assert visti and visti[0].startswith("https://")
    salvato = json.loads((tmp_path / "news.json").read_text(encoding="utf-8"))
    assert salvato["items"][0]["titolo"] == "dal-robot"


def test_senza_rete_le_notizie_non_fanno_cadere_l_app(monkeypatch, tmp_path):
    """Sono un di più: se non arrivano si tengono quelle che si hanno."""
    _finta_rete(monkeypatch, urllib.error.URLError("niente rete"))
    monkeypatch.setattr(reader, "REMOTE_CACHE", tmp_path / "news.json")
    assert reader.refresh_from_origin() is False
    assert not (tmp_path / "news.json").exists()


def test_un_file_storto_non_sovrascrive_quello_buono(monkeypatch, tmp_path):
    buono = tmp_path / "news.json"
    buono.write_text('{"items": [{"titolo": "quella di ieri"}]}', encoding="utf-8")
    monkeypatch.setattr(reader, "REMOTE_CACHE", buono)

    _finta_rete(monkeypatch, "non sono affatto json")
    assert reader.refresh_from_origin() is False
    _finta_rete(monkeypatch, '{"items": "doveva essere una lista"}')
    assert reader.refresh_from_origin() is False
    assert "quella di ieri" in buono.read_text(encoding="utf-8")


def test_non_serve_piu_git(monkeypatch, tmp_path):
    """Il punto di tutta la modifica: dentro il container git non esiste."""
    import subprocess
    def esplodi(*a, **k):
        raise AssertionError("ha provato a usare git")
    monkeypatch.setattr(subprocess, "run", esplodi)
    _finta_rete(monkeypatch, json.dumps({"items": []}))
    monkeypatch.setattr(reader, "REMOTE_CACHE", tmp_path / "news.json")
    assert reader.refresh_from_origin() is True
