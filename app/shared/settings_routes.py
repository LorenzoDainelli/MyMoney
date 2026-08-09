"""Pagina Impostazioni: inserimento delle chiavi API (salvate solo in locale).

L'app funziona senza chiavi. Inserendone una si sbloccano funzioni extra (es.
l'agente AI con la chiave Gemini). Le chiavi non vengono mai mostrate in chiaro
ne' loggate.
"""
import json
import logging

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, Response

from shared.templating import templates
from shared import settings_store as store
from shared import tempo
from shared import ai
from shared import ai_memory

router = APIRouter()
log = logging.getLogger("mymoney.impostazioni")


@router.get("/impostazioni", response_class=HTMLResponse)
def impostazioni(request: Request, salvato: int = 0, ai_test: str = "",
                 ripristino: str = ""):
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
        "ripristino": ripristino,
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


# ── il backup: l'unica strada per portarsi via i dati ───────────────────────
#
# Finché l'app girava sul PC, i dati erano un file sul disco: bastava copiarlo.
# Ora stanno in un database che non è tuo, in una regione di Google, e nessuna
# pagina permetteva di tirarli fuori — la copia sul Drive era l'ultima strada, e
# se ne va con la Fase 5. Un tasto che scarica tutto è la condizione per poterla
# togliere, non un extra.

@router.get("/impostazioni/backup")
def backup():
    """Scarica TUTTO in un file JSON: conti, categorie, movimenti.

    È una lettura e basta: non cambia niente e si può premere quando si vuole.
    Per rimettere dentro un backup c'è `POST /api/finanze/import` — non ha un
    bottone apposta di proposito, perché è l'unica operazione che può cancellare
    tutto e non deve stare a un dito di distanza da quella che salva."""
    from shared import backup as mod_backup
    contenuto = json.dumps(mod_backup.build_snapshot(), ensure_ascii=False,
                           default=str, indent=2)
    nome = f"mymoney-{tempo.adesso().strftime('%Y%m%d-%H%M')}.json"
    return Response(
        content=contenuto,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.post("/impostazioni/ripristina")
async def ripristina(request: Request):
    """Ricarica un file di backup: SVUOTA e rimette dentro quello che c'è nel file.

    Non fonde di proposito. Chi arriva qui non vuole mescolare: vuole tornare a
    com'era. Prima di toccare qualcosa scrive lo stato attuale in
    `data/backups/`, così anche un ripristino sbagliato è reversibile — sul PC,
    dove quella cartella resta. Sul server è un container che si spegne, quindi
    là la rete di sicurezza vera è il file che ti sei scaricato prima.

    Sta dietro un `<details>` chiuso, non accanto al tasto che salva: è l'unica
    operazione dell'app che può cancellare tutto in un colpo.
    """
    from shared import backup as mod_backup
    form = await request.form()
    file = form.get("file")
    if not file or not hasattr(file, "read"):
        return RedirectResponse("/impostazioni?ripristino=nofile", status_code=303)
    try:
        dati = json.loads(await file.read())
    except (ValueError, UnicodeDecodeError):
        return RedirectResponse("/impostazioni?ripristino=illeggibile", status_code=303)

    try:
        mod_backup.scrivi_su_file()
    except OSError as e:                        # disco di sola lettura: non è un motivo per fermarsi
        log.warning("backup preventivo non scritto: %s", e)

    esito = mod_backup.replace_all_from_snapshot(dati)
    if esito.get("future"):
        return RedirectResponse("/impostazioni?ripristino=futuro", status_code=303)
    if not esito.get("ok"):
        return RedirectResponse("/impostazioni?ripristino=errore", status_code=303)
    return RedirectResponse(f"/impostazioni?ripristino={esito.get('count', 0)}",
                            status_code=303)


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
