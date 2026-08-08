"""Sync via Google Drive (v2, Fase 5). Vedi PIANO-FASE-5.md e SYNC-PROTOCOL.md §9.

Il Drive di Lorenzo fa da "corriere" tra PC e telefono: ogni dispositivo carica
il proprio stato completo (state-<device_id>.json) nella cartella nascosta
appDataFolder e scarica quello degli altri; la fusione è il merge LWW della
Fase 4 (sync.apply_snapshot). Scope minimo `drive.appdata`: l'app vede SOLO la
sua cartellina, mai i file personali di Drive.

OAuth "installed app" con PKCE, tutto con la libreria standard (urllib): il
callback torna direttamente sull'app FastAPI (/impostazioni/drive/callback).
Credenziali e token vivono in settings_store (DB in app/data/, mai nel repo).

Privacy: token e credenziali non vengono MAI loggati; nei log niente importi,
descrizioni o nomi (solo nomi-file e contatori).
"""
import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from shared import settings_store, tempo
from shared import sync

log = logging.getLogger("mymoney.drive")

SCOPE = "https://www.googleapis.com/auth/drive.appdata"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
TIMEOUT = 25  # secondi per singola richiesta HTTP

# Flussi OAuth in corso (state → PKCE verifier). App locale mono-utente:
# un dict in memoria basta; le voci scadono dopo 10 minuti.
_pending: dict[str, dict] = {}
_PENDING_TTL = 600


class DriveError(Exception):
    """Errore Drive generico (rete, quota, 4xx/5xx non di autenticazione)."""


class DriveAuthError(DriveError):
    """Token rifiutato (401): va rinnovato o ricollegato."""


# ── configurazione / stato ──────────────────────────────────────────────────

def is_configured() -> bool:
    """True se l'utente ha incollato le credenziali OAuth (client id+secret)."""
    return bool(settings_store.get_setting("drive_client_id", "").strip()
                and settings_store.get_setting("drive_client_secret", "").strip())


def is_connected() -> bool:
    """True se abbiamo un refresh token (il collegamento è stato fatto)."""
    return bool(_load_token().get("refresh_token"))


def last_sync_info() -> dict | None:
    """Esito dell'ultima sync ({ts, ok, applied, ...}) per la pagina Impostazioni."""
    raw = settings_store.get_setting("drive_last_sync", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _load_token() -> dict:
    raw = settings_store.get_setting("drive_token", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _save_token(tok: dict) -> None:
    settings_store.set_setting("drive_token", json.dumps(tok))


def _clear_token() -> None:
    settings_store.set_setting("drive_token", "")


# ── HTTP di base (urllib, nessuna dipendenza) ───────────────────────────────

def _http(method: str, url: str, body: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes]:
    """Una richiesta HTTP. Ritorna (status, corpo). Gli errori HTTP tornano
    come status+corpo (per leggere il messaggio), gli errori di rete sollevano
    DriveError."""
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise DriveError(f"rete: {type(e).__name__}") from None


def _token_request(params: dict) -> dict:
    """POST form-encoded all'endpoint token di Google. Ritorna il JSON di
    risposta; {'error': ...} se Google rifiuta. Mai loggare params (segreti)."""
    body = urllib.parse.urlencode(params).encode()
    status, raw = _http("POST", TOKEN_URL, body,
                        {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if status != 200 and "error" not in data:
        data["error"] = f"http_{status}"
    return data


# ── OAuth: collegamento (authorization code + PKCE) ─────────────────────────

def build_auth_url(redirect_uri: str) -> str:
    """URL di consenso Google. Genera state + PKCE verifier e li tiene in
    memoria fino al callback."""
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["ts"] > _PENDING_TTL]:
        _pending.pop(k, None)

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)  # 64 char: dentro i limiti PKCE 43-128
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    _pending[state] = {"verifier": verifier, "ts": now}

    params = {
        "client_id": settings_store.get_setting("drive_client_id", "").strip(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # → refresh token
        "prompt": "consent",        # refresh token anche ai ricollegamenti
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def handle_callback(code: str, state: str, redirect_uri: str) -> tuple[bool, str]:
    """Scambia il codice del callback con i token. Ritorna (ok, codice_errore)."""
    pend = _pending.pop(state or "", None)
    if not pend:
        return False, "state"   # state sconosciuto/scaduto: non è la nostra richiesta
    data = _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings_store.get_setting("drive_client_id", "").strip(),
        "client_secret": settings_store.get_setting("drive_client_secret", "").strip(),
        "redirect_uri": redirect_uri,
        "code_verifier": pend["verifier"],
    })
    if "error" in data or not data.get("access_token"):
        log.warning("drive: scambio codice fallito (%s)", data.get("error", "sconosciuto"))
        return False, "scambio"
    _save_token({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", _load_token().get("refresh_token", "")),
        "expires_at": time.time() + float(data.get("expires_in", 3600)),
    })
    return True, ""


def get_access_token(force_refresh: bool = False) -> str | None:
    """Access token valido (rinnovato se scaduto). None se serve ricollegare.
    Se il refresh token è stato revocato (invalid_grant) il token viene
    cancellato: i dati locali restano intatti, la UI chiede di ricollegare."""
    tok = _load_token()
    if not tok.get("refresh_token"):
        return None
    if (not force_refresh and tok.get("access_token")
            and time.time() < float(tok.get("expires_at", 0)) - 60):
        return tok["access_token"]
    data = _token_request({
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": settings_store.get_setting("drive_client_id", "").strip(),
        "client_secret": settings_store.get_setting("drive_client_secret", "").strip(),
    })
    if "error" in data or not data.get("access_token"):
        err = data.get("error", "sconosciuto")
        log.warning("drive: refresh token fallito (%s)", err)
        if err == "invalid_grant":     # accesso revocato da Google
            _clear_token()
        return None
    tok["access_token"] = data["access_token"]
    tok["expires_at"] = time.time() + float(data.get("expires_in", 3600))
    if data.get("refresh_token"):
        tok["refresh_token"] = data["refresh_token"]
    _save_token(tok)
    return tok["access_token"]


def disconnect() -> None:
    """Scollega: revoca il token presso Google (best-effort) e lo dimentica.
    I dati locali e i file su Drive NON vengono toccati."""
    tok = _load_token()
    refresh = tok.get("refresh_token", "")
    if refresh:
        try:
            _http("POST", REVOKE_URL,
                  urllib.parse.urlencode({"token": refresh}).encode(),
                  {"Content-Type": "application/x-www-form-urlencoded"})
        except DriveError:
            pass  # senza rete la revoca non parte: il token locale sparisce comunque
    _clear_token()


# ── trasporto Drive (iniettabile nei test: FakeDrive con la stessa interfaccia) ──

class DriveClient:
    """Client REST minimale per la cartella appDataFolder."""

    def __init__(self, access_token: str):
        self._auth = {"Authorization": f"Bearer {access_token}"}

    def _req(self, method: str, url: str, body: bytes | None = None,
             headers: dict | None = None) -> bytes:
        h = dict(self._auth)
        if headers:
            h.update(headers)
        status, raw = _http(method, url, body, h)
        if status == 401:
            raise DriveAuthError("401")
        if status >= 400:
            err_body = raw.decode("utf-8", errors="ignore")
            if "storageQuota" in err_body or "quotaExceeded" in err_body:
                log.warning("drive: errore API (quota) - status %s", status)
                raise DriveError("quota")
            log.warning("drive: errore API (http_%s)", status)
            raise DriveError(f"http_{status}")
        return raw

    def list_state_files(self) -> list[dict]:
        """Tutti i file della cartellina nascosta: [{id, name, modifiedTime}]."""
        params = urllib.parse.urlencode({
            "spaces": "appDataFolder",
            "fields": "files(id,name,modifiedTime)",
            "pageSize": "100",
        })
        raw = self._req("GET", f"{API_BASE}/files?{params}")
        return json.loads(raw).get("files", [])

    def download(self, file_id: str) -> dict:
        raw = self._req("GET", f"{API_BASE}/files/{file_id}?alt=media")
        return json.loads(raw)

    def upload_state(self, name: str, data: dict, file_id: str | None = None) -> str:
        """Crea (multipart) o aggiorna (media) un file JSON in appDataFolder."""
        content = json.dumps(data, ensure_ascii=False, default=str).encode()
        if file_id:
            raw = self._req("PATCH",
                            f"{UPLOAD_BASE}/files/{file_id}?uploadType=media",
                            content, {"Content-Type": "application/json"})
            return json.loads(raw).get("id", file_id)
        boundary = "mm" + secrets.token_hex(12)
        meta = json.dumps({"name": name, "parents": ["appDataFolder"]}).encode()
        body = (b"--" + boundary.encode() + b"\r\n"
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                + meta + b"\r\n"
                b"--" + boundary.encode() + b"\r\n"
                b"Content-Type: application/json\r\n\r\n"
                + content + b"\r\n"
                b"--" + boundary.encode() + b"--")
        raw = self._req("POST", f"{UPLOAD_BASE}/files?uploadType=multipart",
                        body, {"Content-Type": f"multipart/related; boundary={boundary}"})
        return json.loads(raw).get("id", "")

    def delete_file(self, file_id: str) -> None:
        """Cancella un file dalla cartellina (usato per potare i vecchi backup)."""
        self._req("DELETE", f"{API_BASE}/files/{file_id}")


# ── orchestrazione ──────────────────────────────────────────────────────────

def _state_hash(snap: dict) -> str:
    """Impronta del solo CONTENUTO (record ordinati per uid): ts/diary_lines
    cambiano a ogni chiamata e non devono invalidare l'hash."""
    def _sorted(key):
        return sorted(snap.get(key, []), key=lambda r: r.get("uid", ""))
    canon = json.dumps({"wallets": _sorted("wallets"),
                        "categorie": _sorted("categorie"),
                        "movimenti": _sorted("movimenti")},
                       sort_keys=True, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def _load_seen() -> dict:
    raw = settings_store.get_setting("drive_seen", "")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _sync_with(client) -> dict:
    """Il cuore: scarica gli stati degli altri device, fondili, ricarica il tuo."""
    mine = f"state-{sync.get_device_id()}.json"
    files = client.list_state_files()
    seen = _load_seen()
    applied = skipped = errors = downloaded = future = 0

    for f in files:
        name = f.get("name", "")
        if not (name.startswith("state-") and name.endswith(".json")) or name == mine:
            continue
        if seen.get(name) == f.get("modifiedTime"):
            continue  # già applicato: niente da riscaricare
        try:
            data = client.download(f["id"])
        except DriveAuthError:
            raise
        except (DriveError, json.JSONDecodeError):
            errors += 1
            log.warning("drive: download fallito per %s", name)
            continue
        if not isinstance(data, dict):
            errors += 1
            log.warning("drive: contenuto non valido in %s", name)
            continue
        
        r = sync.apply_snapshot(data)
        applied += r.get("applied", 0); skipped += r.get("skipped", 0); errors += r.get("errors", 0)
        if r.get("future", 0):
            # Schema più nuovo del nostro: NON marcare 'seen', così dopo che
            # l'app sarà aggiornata questo stato verrà riletto e applicato
            # (coerente con la PWA). Non conta né come download né come errore.
            future += r["future"]
            continue
        downloaded += 1
        seen[name] = f.get("modifiedTime")

    # Upload del proprio stato (che ora include già la fusione appena fatta).
    snap = sync.build_snapshot()
    content_hash = _state_hash(snap)
    existing = next((f for f in files if f.get("name") == mine), None)
    uploaded = False
    if (existing is None
            or content_hash != settings_store.get_setting("drive_last_upload_hash", "")):
        client.upload_state(mine, snap, file_id=existing["id"] if existing else None)
        settings_store.set_setting("drive_last_upload_hash", content_hash)
        uploaded = True

    settings_store.set_setting("drive_seen", json.dumps(seen))
    # Se in questo giro NON abbiamo incontrato stati di schema più nuovo, l'app
    # è di nuovo allineata: spegni l'avviso "aggiorna" (apply_snapshot lo accende
    # quando serve, qui lo spegniamo quando la sync è pulita → auto-guarigione).
    if future == 0:
        settings_store.set_setting("sync_needs_update", "")
    return {"ok": True, "applied": applied, "skipped": skipped,
            "errors": errors, "downloaded": downloaded, "uploaded": uploaded, "future": future}


def sync_once(client=None) -> dict:
    """Una sincronizzazione completa contro Drive.

    Senza `client` usa il Drive vero (serve il collegamento OAuth); nei test si
    inietta un finto-Drive con la stessa interfaccia. Su 401 riprova UNA volta
    con token rinnovato. Non solleva mai: ritorna {ok: False, error: ...}.
    """
    vuoto = {"applied": 0, "skipped": 0, "errors": 0, "downloaded": 0, "uploaded": False, "future": 0}
    own_client = client is None
    if own_client:
        if not is_configured() or not is_connected():
            return {"ok": False, "error": "non_connesso", **vuoto}
        token = get_access_token()
        if not token:
            return {"ok": False, "error": "auth", **vuoto}
        client = DriveClient(token)

    result = None
    for tentativo in (1, 2):
        try:
            result = _sync_with(client)
            break
        except DriveAuthError:
            if own_client and tentativo == 1:
                token = get_access_token(force_refresh=True)
                if token:
                    client = DriveClient(token)
                    continue
            result = {"ok": False, "error": "auth", **vuoto}
            break
        except DriveError as e:
            log.warning("drive: sync fallita (%s)", e)
            err_val = "quota" if str(e) == "quota" else "drive"
            result = {"ok": False, "error": err_val, **vuoto}
            break
        except Exception:
            log.warning("drive: sync fallita", exc_info=True)
            result = {"ok": False, "error": "errore", **vuoto}
            break

    settings_store.set_setting("drive_last_sync", json.dumps(
        {"ts": tempo.adesso().isoformat(timespec="minutes"), **result}))
    return result


# ── Fase 3: copia a SPECCHIO (una sola copia autorevole su Drive) ───────────
# mirror.json fa da riferimento condiviso tra PC e telefono. Diverso dal sync a
# fusione qui sopra: "Carica" sovrascrive mirror.json col PC (tenendo un backup),
# "Scarica" rimpiazza TUTTO il PC con mirror.json (sostituzione, non fusione).
MIRROR_NAME = "mirror.json"
BAK_PREFIX = "mirror-bak-"
BAK_KEEP = 3


def _find(files: list[dict], name: str) -> dict | None:
    return next((f for f in files if f.get("name") == name), None)


def _run_drive(fn, client=None) -> dict:
    """Esegue `fn(client)` contro il Drive con il solito bootstrap del token e
    UN retry su 401. Non solleva mai: ritorna {ok: False, error} sugli errori."""
    own_client = client is None
    if own_client:
        if not is_configured() or not is_connected():
            return {"ok": False, "error": "non_connesso"}
        token = get_access_token()
        if not token:
            return {"ok": False, "error": "auth"}
        client = DriveClient(token)
    for tentativo in (1, 2):
        try:
            return fn(client)
        except DriveAuthError:
            if own_client and tentativo == 1:
                token = get_access_token(force_refresh=True)
                if token:
                    client = DriveClient(token)
                    continue
            return {"ok": False, "error": "auth"}
        except DriveError as e:
            log.warning("drive mirror: fallita (%s)", e)
            return {"ok": False, "error": "quota" if str(e) == "quota" else "drive"}
        except Exception:
            log.warning("drive mirror: fallita", exc_info=True)
            return {"ok": False, "error": "errore"}
    return {"ok": False, "error": "errore"}


def _prune_backups(client, files: list[dict]) -> None:
    """Tiene gli ultimi (BAK_KEEP-1) backup: col nuovo che stiamo per creare = BAK_KEEP."""
    baks = sorted((f for f in files if f.get("name", "").startswith(BAK_PREFIX)),
                  key=lambda f: f.get("name", ""))   # nomi con timestamp = ordine cronologico
    excess = len(baks) - (BAK_KEEP - 1)
    for f in baks[:max(0, excess)]:
        try:
            client.delete_file(f["id"])
        except DriveError:
            pass  # potatura best-effort


def mirror_status(client=None) -> dict:
    """C'è una copia sul Drive? quando è stata modificata? (per la pagina)."""
    def _do(c):
        m = _find(c.list_state_files(), MIRROR_NAME)
        if not m:
            return {"ok": True, "exists": False}
        return {"ok": True, "exists": True, "modifiedTime": m.get("modifiedTime")}
    return _run_drive(_do, client)


def mirror_carica(client=None) -> dict:
    """PC → Drive: sovrascrive mirror.json con lo stato del PC, tenendo un backup."""
    def _do(c):
        files = c.list_state_files()
        existing = _find(files, MIRROR_NAME)
        if existing is not None:
            try:
                old = c.download(existing["id"])
                bname = BAK_PREFIX + tempo.adesso().strftime("%Y-%m-%dT%H-%M-%S") + ".json"
                c.upload_state(bname, old, file_id=None)
                _prune_backups(c, files)
            except (DriveError, json.JSONDecodeError):
                pass  # il backup è best-effort: non blocca il Carica
        mirror = sync.build_mirror()
        c.upload_state(MIRROR_NAME, mirror, file_id=existing["id"] if existing else None)
        return {"ok": True, "ts": mirror.get("ts"), "counts": {
            "wallets": len(mirror.get("wallets", [])),
            "categorie": len(mirror.get("categorie", [])),
            "movimenti": len(mirror.get("movimenti", [])),
        }}
    return _run_drive(_do, client)


def mirror_scarica(client=None) -> dict:
    """Drive → PC: rimpiazza TUTTO il PC con mirror.json. Prima fa un backup
    locale recuperabile (backup_bundle_to_file)."""
    def _do(c):
        m = _find(c.list_state_files(), MIRROR_NAME)
        if not m:
            return {"ok": True, "exists": False}
        data = c.download(m["id"])
        if not isinstance(data, dict) or not isinstance(data.get("wallets"), list):
            return {"ok": False, "error": "illeggibile"}
        backup_path = sync.backup_bundle_to_file()
        r = sync.replace_all_from_snapshot(data)
        if not r.get("ok"):
            return {"ok": False, "error": "schema_nuovo" if r.get("future") else "errore",
                    "backup": str(backup_path)}
        return {"ok": True, "exists": True, "count": r.get("count", 0),
                "ts": data.get("ts"), "modifiedTime": m.get("modifiedTime"),
                "backup": str(backup_path)}
    return _run_drive(_do, client)
