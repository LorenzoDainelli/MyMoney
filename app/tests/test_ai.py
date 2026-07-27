"""Test dell'agente AI: astrazione del provider (Studio vs Vertex AI).

Verifica che lo switch di provider cambi SOLO endpoint e autenticazione, senza
toccare il corpo della richiesta né la logica di parsing. Nessuna rete vera: la
`urlopen` è finta e, per Vertex, il token è stubbato (così i test girano anche
senza google-auth installato).
"""
import json
import sys
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.db import Base
import shared.settings_store as store_mod
import shared.ai as ai


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    import shared.db as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(store_mod, "SessionLocal", TestSession)
    # la cache credenziali Vertex è a livello di modulo: azzerala tra i test
    ai._vertex_cache["fp"] = None
    ai._vertex_cache["creds"] = None
    yield


class FakeResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(captured, text="OK"):
    def _f(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode("utf-8"))
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return FakeResp({"candidates": [{"content": {"parts": [{"text": text}]}}]})
    return _f


# ── provider di default e sblocco ────────────────────────────────────────────

def test_default_provider_e_studio():
    assert ai.get_provider() == "studio"
    assert ai.is_configured() is False           # nessuna chiave

    store_mod.set_setting("gemini_api_key", "AIzaTEST")
    assert ai.is_configured() is True


def test_provider_sconosciuto_ripiega_su_studio():
    store_mod.set_setting("ai_provider", "qualcosa")
    assert ai.get_provider() == "studio"


def test_set_provider_valida():
    ai.set_provider("vertex")
    assert ai.get_provider() == "vertex"
    ai.set_provider("boh")                        # ignorato
    assert ai.get_provider() == "vertex"


# ── modello: ogni provider ha il suo (i 2.0 su Vertex danno 404) ─────────────

def test_ogni_provider_ha_il_suo_modello():
    # default per provider
    assert ai.get_model() == ai.DEFAULT_MODEL                 # studio → 2.0-flash
    ai.set_provider("vertex")
    assert ai.get_model() == ai.DEFAULT_MODEL_VERTEX          # vertex → 2.5-flash

    # il modello scelto su un provider NON contamina l'altro
    ai.set_model("gemini-2.5-pro")
    assert store_mod.get_setting("vertex_model") == "gemini-2.5-pro"
    ai.set_provider("studio")
    assert ai.get_model() == ai.DEFAULT_MODEL                 # studio resta al suo
    ai.set_model("gemini-2.0-flash-lite")
    ai.set_provider("vertex")
    assert ai.get_model() == "gemini-2.5-pro"                 # vertex ricorda il suo


def test_fallback_404_usa_il_default_del_provider(monkeypatch):
    """Un nome di modello inesistente su Vertex deve ripiegare sul 2.5, non sul 2.0."""
    ai.set_provider("vertex")
    store_mod.set_setting("vertex_project", "p")
    store_mod.set_setting("vertex_service_account_json", "{}")
    store_mod.set_setting("vertex_model", "gemini-2.0-flash")   # inesistente su Vertex
    monkeypatch.setattr(ai, "_vertex_access_token", lambda: "TOK")

    visti = []

    def _urlopen(req, timeout=None):
        visti.append(req.full_url)
        if len(visti) == 1:                       # primo tentativo → 404
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return FakeResp({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    assert ai._call("ciao") == "OK"
    assert "gemini-2.0-flash:generateContent" in visti[0]
    assert f"{ai.DEFAULT_MODEL_VERTEX}:generateContent" in visti[1]


# ── Studio: endpoint + header + parsing ──────────────────────────────────────

def test_studio_call_costruisce_endpoint_e_header(monkeypatch):
    store_mod.set_setting("gemini_api_key", "AIzaTEST")
    cap = {}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(cap, "ciao"))

    out = ai._call("prompt di prova")
    assert out == "ciao"
    assert cap["url"] == ai.STUDIO_ENDPOINT.format(model=ai.DEFAULT_MODEL)
    assert cap["headers"]["x-goog-api-key"] == "AIzaTEST"
    assert "authorization" not in cap["headers"]
    # la chiave NON deve mai finire nell'URL
    assert "AIzaTEST" not in cap["url"]
    assert cap["data"]["contents"][0]["parts"][0]["text"] == "prompt di prova"


# ── Vertex: is_configured, endpoint (global/regione), header ─────────────────

def test_vertex_non_configurato_non_e_pronto():
    ai.set_provider("vertex")
    assert ai.is_configured() is False            # manca progetto + service account
    store_mod.set_setting("vertex_project", "mymoney-502422")
    assert ai.is_configured() is False            # manca ancora il service account
    store_mod.set_setting("vertex_service_account_json", '{"type":"service_account"}')
    assert ai.is_configured() is True


def test_vertex_endpoint_global_e_regionale():
    store_mod.set_setting("vertex_project", "mymoney-502422")
    # default = global → host senza prefisso di regione
    url = ai._vertex_endpoint("gemini-2.0-flash")
    assert url == ("https://aiplatform.googleapis.com/v1/projects/mymoney-502422"
                   "/locations/global/publishers/google/models/gemini-2.0-flash:generateContent")
    # regione esplicita → host con prefisso
    store_mod.set_setting("vertex_location", "europe-west1")
    url = ai._vertex_endpoint("gemini-2.0-flash")
    assert url.startswith("https://europe-west1-aiplatform.googleapis.com/")
    assert "/locations/europe-west1/" in url


def test_vertex_call_usa_endpoint_vertex_e_bearer(monkeypatch):
    ai.set_provider("vertex")
    store_mod.set_setting("vertex_project", "mymoney-502422")
    store_mod.set_setting("vertex_service_account_json", '{"type":"service_account"}')
    # token stubbato: niente google-auth, niente rete
    monkeypatch.setattr(ai, "_vertex_access_token", lambda: "TOK123")
    cap = {}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(cap, "risposta"))

    out = ai._call("analisi")
    assert out == "risposta"
    assert "aiplatform.googleapis.com" in cap["url"]
    assert "/publishers/google/models/" in cap["url"]
    assert cap["headers"]["authorization"] == "Bearer TOK123"
    assert "x-goog-api-key" not in cap["headers"]
    assert "TOK123" not in cap["url"]              # token solo negli header


def test_vertex_senza_credenziali_solleva_no_key():
    ai.set_provider("vertex")
    with pytest.raises(RuntimeError):
        ai._vertex_access_token()


# ── orario del movimento dettato (prima veniva sempre forzato a mezzogiorno) ──

def test_norm_ora():
    assert ai._norm_ora("14:13") == "14:13"
    assert ai._norm_ora("9:30") == "09:30"        # ora a una cifra -> zero davanti
    assert ai._norm_ora("14.13") == "14:13"       # il punto al posto dei due punti
    assert ai._norm_ora("") == "12:00"            # non detto -> mezzogiorno neutro
    assert ai._norm_ora(None) == "12:00"
    assert ai._norm_ora("25:00") == "12:00"       # ore/minuti fuori scala -> ripiego
    assert ai._norm_ora("12:75") == "12:00"


class _Nome:
    def __init__(self, nome):
        self.nome = nome


def _proposta(monkeypatch, payload):
    store_mod.set_setting("gemini_api_key", "AIzaTEST")
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps(payload))
    return ai.parse_movimento("...", [_Nome("Conto")], [_Nome("Bar")], oggi="2026-07-20")


def test_orario_dettato_finisce_nel_form(monkeypatch):
    """'alle 14:13' deve arrivare al campo datetime-local, non diventare 12:00."""
    p = _proposta(monkeypatch, {"tipo": "uscita", "importo": 12, "data": "2026-07-20",
                                "ora": "14:13", "confidenza": "alta"})
    assert p["data_local"] == "2026-07-20T14:13"


def test_orario_non_detto_resta_mezzogiorno(monkeypatch):
    p = _proposta(monkeypatch, {"tipo": "uscita", "importo": 12, "data": "2026-07-20",
                                "ora": "", "confidenza": "media"})
    assert p["data_local"] == "2026-07-20T12:00"


# ── esito test_connection quando mancano le librerie Vertex ──────────────────

def test_esito_test_mappa_vertexlibs(monkeypatch):
    from shared import settings_routes
    assert settings_routes._esito_test(False, "vertex_libs_mancanti") == "vertexlibs"

    ai.set_provider("vertex")
    store_mod.set_setting("vertex_project", "p")
    store_mod.set_setting("vertex_service_account_json", "{}")

    def _boom():
        raise RuntimeError("vertex_libs_mancanti")
    monkeypatch.setattr(ai, "_vertex_access_token", _boom)
    ok, detail = ai.test_connection()
    assert ok is False
    assert detail == "vertex_libs_mancanti"


# ── modalità PROATTIVA: l'interruttore che non faceva niente ─────────────────
# Le Impostazioni promettono «commenta all'apertura» e la modalità si poteva
# selezionare, ma `get_mode()` veniva letto solo per ridisegnare la tendina:
# chi la accendeva non otteneva nulla. Qui si difende che la promessa valga.

def _lettura_di(ore_fa):
    from datetime import timedelta
    return json.dumps({"text": "x", "conf": "media",
                       "when": (datetime.now() - timedelta(hours=ore_fa)).isoformat()})


def test_a_domanda_non_rigenera_mai_da_sola(monkeypatch):
    ai.set_mode(ai.MODE_ON_DEMAND)
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    store_mod.set_setting("prova_ai", _lettura_di(999))
    chiamate = []
    assert ai.forse_rigenera("prova_ai", lambda: chiamate.append(1)) is False
    assert chiamate == []


def test_proattivo_rigenera_una_lettura_vecchia(monkeypatch):
    ai.set_mode(ai.MODE_PROACTIVE)
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    store_mod.set_setting("prova_ai", _lettura_di(ai.ORE_PROATTIVO + 1))
    fatto = threading.Event()
    assert ai.forse_rigenera("prova_ai", fatto.set) is True
    assert fatto.wait(5), "la rigenerazione non è partita"


def test_proattivo_non_rigenera_una_lettura_fresca(monkeypatch):
    """Aprire dieci volte la dashboard non deve costare dieci chiamate."""
    ai.set_mode(ai.MODE_PROACTIVE)
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    store_mod.set_setting("prova_ai", _lettura_di(1))
    chiamate = []
    assert ai.forse_rigenera("prova_ai", lambda: chiamate.append(1)) is False
    assert chiamate == []


def test_proattivo_senza_chiave_resta_fermo(monkeypatch):
    ai.set_mode(ai.MODE_PROACTIVE)
    monkeypatch.setattr(ai, "is_configured", lambda: False)
    store_mod.set_setting("prova_ai", "")
    assert ai.forse_rigenera("prova_ai", lambda: None) is False


def test_una_lettura_mai_scritta_e_da_rigenerare(monkeypatch):
    ai.set_mode(ai.MODE_PROACTIVE)
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    store_mod.set_setting("prova_ai", "")
    assert ai.eta_lettura("prova_ai") is None
    fatto = threading.Event()
    assert ai.forse_rigenera("prova_ai", fatto.set) is True
    assert fatto.wait(5)
