"""Pagina Impostazioni: inserimento delle chiavi API (salvate solo in locale).

L'app funziona senza chiavi. Inserendone una si sbloccano funzioni extra (es.
l'agente AI con la chiave Gemini). Le chiavi non vengono mai mostrate in chiaro
ne' loggate.
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from datetime import datetime

from shared.templating import templates
from shared import settings_store as store
from shared import tempo
from shared import ai
from shared import ai_memory
from shared import drive_sync

router = APIRouter()


@router.get("/impostazioni", response_class=HTMLResponse)
def impostazioni(request: Request, salvato: int = 0, ai_test: str = "", drive: str = ""):
    voci = []
    for chiave, meta in store.KNOWN_SETTINGS.items():
        valore = store.get_setting(chiave, "")
        voci.append({
            "chiave": chiave,
            "tkey": meta["tkey"],
            "secret": meta.get("secret", False),
            "presente": bool(valore.strip()),
            "mascherato": store.masked(valore) if meta.get("secret") else valore,
        })
    drive_last = drive_sync.last_sync_info()
    drive_last_stale = False
    if drive_last and drive_last.get("ts"):
        try:
            ts = datetime.fromisoformat(drive_last["ts"])
            if (tempo.adesso() - ts).days > 7:
                drive_last_stale = True
        except ValueError:
            pass

    return templates.TemplateResponse(request, "settings.html", {
        "active": "impostazioni",
        "voci": voci, "salvato": bool(salvato),
        "ai_configured": ai.is_configured(),
        # sul server la chiave viene da Secret Manager: una casella qui non
        # cambierebbe niente, e va detto invece di mostrarla
        "chiave_dal_server": ai.chiave_dal_server(),
        "ai_model": ai.get_model(),
        "ai_mode": ai.get_mode(),
        "ai_web": ai.usa_web(),
        "ai_test": ai_test,
        "MODES": ai.MODES,
        "ai_provider": ai.get_provider(),
        "ai_default_model": ai.default_model(),
        "PROVIDERS": ai.PROVIDERS,
        "vertex_project": store.get_setting("vertex_project", ""),
        "vertex_location": store.get_setting("vertex_location", "") or ai.DEFAULT_VERTEX_LOCATION,
        # dove sei: decide la data dei movimenti e ogni orario mostrato
        "fuso": tempo.nome_fuso(),
        "fuso_ora": tempo.adesso().strftime("%H:%M"),
        "FUSI": tempo.FUSI,
        "drive_msg": drive,
        "drive_configured": drive_sync.is_configured(),
        "drive_connected": drive_sync.is_connected(),
        "drive_last": drive_last,
        "drive_last_stale": drive_last_stale,
        "sync_needs_update": bool(store.get_setting("sync_needs_update", "")),
        # memoria dell'agente: deve essere LEGGIBILE e cancellabile riga per riga,
        # altrimenti diventa una scatola nera che nessuno può correggere
        "ai_ricordi": ai_memory.ricordi(),
        "ai_letture": ai_memory.ultime_letture(n=5),
    })


@router.post("/impostazioni/fuso")
def salva_fuso(fuso: str = Form("")):
    """Cambia il fuso di riferimento dell'app.

    Un nome inventato non cambia niente e non rompe niente: `tempo.imposta`
    accetta solo fusi che esistono davvero. Il valore arriva dal menù, oppure
    dal pulsante «usa quello del dispositivo», che manda quello dichiarato dal
    browser — e il browser di uno sconosciuto può dichiarare qualunque cosa.
    """
    ok = tempo.imposta(fuso)
    return RedirectResponse(f"/impostazioni?salvato={1 if ok else 0}#fuso",
                            status_code=303)


@router.post("/impostazioni/memoria/{rid}/dimentica")
def memoria_dimentica(rid: int):
    ai_memory.dimentica(rid)
    return RedirectResponse("/impostazioni#memoria", status_code=303)


@router.post("/impostazioni/memoria/svuota")
def memoria_svuota(tipo: str = Form("")):
    ai_memory.dimentica_tutto(tipo if tipo in
                              (ai_memory.TIPO_RICORDO, ai_memory.TIPO_LETTURA) else "")
    return RedirectResponse("/impostazioni#memoria", status_code=303)


@router.post("/impostazioni/ai")
def salva_ai(modello: str = Form(""), modalita: str = Form(""), web: str = Form("")):
    ai.set_model(modello)
    ai.set_mode(modalita)
    ai.set_usa_web(bool(web))
    return RedirectResponse("/impostazioni?salvato=1", status_code=303)


def _esito_test(ok: bool, detail: str) -> str:
    """Traduce l'esito grezzo di test_connection in un codice per l'interfaccia."""
    if ok:
        return "ok"
    d = (detail or "").lower()
    if d == "no_key":
        return "nokey"
    if "401" in d or "403" in d:
        return "badkey"      # chiave non valida
    if "429" in d:
        return "rate"        # limite di richieste raggiunto (free tier)
    if d == "rete":
        return "net"         # nessuna connessione
    if "vertex_libs" in d:
        return "vertexlibs"  # provider Vertex scelto ma google-auth non installato
    return "err"


@router.post("/impostazioni/ai/test")
def prova_ai():
    ok, detail = ai.test_connection()
    return RedirectResponse(f"/impostazioni?ai_test={_esito_test(ok, detail)}", status_code=303)


# ── Google Drive (Fase 5): collegamento OAuth e sync manuale ────────────────

def _drive_redirect_uri(request: Request) -> str:
    """Il callback torna su QUESTA app (client OAuth di tipo Desktop: qualunque
    porta di loopback è accettata senza registrarla)."""
    return str(request.url_for("drive_callback"))


@router.get("/impostazioni/drive/connetti")
def drive_connetti(request: Request):
    if not drive_sync.is_configured():
        return RedirectResponse("/impostazioni?drive=nocred", status_code=303)
    return RedirectResponse(drive_sync.build_auth_url(_drive_redirect_uri(request)),
                            status_code=303)


@router.get("/impostazioni/drive/callback", name="drive_callback")
def drive_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/impostazioni?drive=rifiutato", status_code=303)
    ok, _err = drive_sync.handle_callback(code, state, _drive_redirect_uri(request))
    return RedirectResponse(f"/impostazioni?drive={'ok' if ok else 'err'}", status_code=303)


@router.post("/impostazioni/drive/sync")
def drive_sync_now():
    result = drive_sync.sync_once()
    if result.get("ok"):
        return RedirectResponse("/impostazioni?drive=sync_ok", status_code=303)
    err = result.get("error")
    if err == "auth":
        esito = "auth"
    elif err == "quota":
        esito = "quota"
    else:
        esito = "sync_err"
    return RedirectResponse(f"/impostazioni?drive={esito}", status_code=303)


@router.post("/impostazioni/drive/scollega")
def drive_scollega():
    drive_sync.disconnect()
    return RedirectResponse("/impostazioni?drive=off", status_code=303)


@router.post("/impostazioni")
async def salva(request: Request):
    form = await request.form()
    for chiave in store.KNOWN_SETTINGS:
        # campo lasciato vuoto = NON cambiare (cosi' non cancelli una chiave gia' messa)
        nuovo = (form.get(chiave) or "").strip()
        if form.get(f"clear_{chiave}"):          # casella "rimuovi" spuntata
            store.set_setting(chiave, "")
        elif nuovo:
            store.set_setting(chiave, nuovo)
    # la card "Agente AI" (freeze) salva chiave+modello+modalità con un solo bottone
    if "modello" in form:
        ai.set_model((form.get("modello") or "").strip())
    if "modalita" in form:
        ai.set_mode((form.get("modalita") or "").strip())
    # provider dell'agente + configurazione Vertex (progetto/regione non segreti):
    # il service account è già gestito sopra dal ciclo su KNOWN_SETTINGS.
    if "ai_provider" in form:
        ai.set_provider((form.get("ai_provider") or "").strip())
    if "vertex_project" in form:
        store.set_setting("vertex_project", (form.get("vertex_project") or "").strip())
    if "vertex_location" in form:
        store.set_setting("vertex_location", (form.get("vertex_location") or "").strip())
    return RedirectResponse("/impostazioni?salvato=1", status_code=303)
