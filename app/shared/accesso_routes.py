"""Le pagine della porta d'ingresso.

Il giro completo, dal bottone all'app aperta:

    /accedi              «Entra con Google»
    /accedi/google       apre il giro (state + PKCE in un cookie di 10 minuti)
    /accedi/google/ritorno   Google ci dice chi sei → **biglietto parziale**
    /accedi/attiva       solo la prima volta: si attacca l'app authenticator
    /accedi/codice       le sei cifre → **biglietto completo**, e si è dentro
    /esci                il biglietto viene buttato

Il biglietto parziale è il perno: dice «Google ti ha riconosciuto» e **non apre
niente**. Vale dieci minuti e porta a una sola pagina, quella del codice. Chi si
impossessasse dell'account Google arriverebbe esattamente fin lì.

Regola che vale per tutte le rotte qui: i motivi di un rifiuto NON si raccontano
all'utente. «Non è stato possibile entrare» e basta — sapere *quale* dei
controlli è saltato è un'informazione utile solo a chi sta provando a entrare.
Il motivo preciso finisce nei log del server, dove serve a noi.
"""
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from shared import accesso, auth, sicurezza
from shared.config import SESSION_KEY
from shared.templating import templates

router = APIRouter()

NOME_COOKIE_GIRO = "mymoney_giro"        # state + PKCE, dieci minuti
NOME_COOKIE_ATTIVA = "mymoney_attiva"    # il segreto in prova, dieci minuti
DURATA_GIRO = 600


def _dove_tornare(dove: str) -> str:
    """Solo percorsi interni: un `next` che punta fuori è un modo classico per
    far atterrare qualcuno su un finto MyMoney appena dopo il login."""
    if dove and dove.startswith("/") and not dove.startswith("//") \
            and not dove.startswith("/accedi"):
        return dove
    return "/"


def _pagina(request, nome: str, ctx: dict | None = None):
    return templates.TemplateResponse(request, nome, ctx or {})


def _metti_biglietto(resp, request, email: str, completo: bool):
    """Consegna il biglietto di sessione al browser.

    `httponly` perché nessuna pagina ha motivo di leggerlo con JavaScript, e se
    non si può leggere non lo si può nemmeno rubare da lì; `samesite=lax` perché
    un sito terzo non deve poter far partire azioni a nome nostro, ma il ritorno
    da Google (una navigazione normale) deve continuare a funzionare.
    """
    resp.set_cookie(
        auth.NOME_COOKIE, auth.crea_biglietto(email, completo),
        max_age=auth.DURATA_SESSIONE if completo else auth.DURATA_PARZIALE,
        httponly=True, samesite="lax", secure=accesso.connessione_sicura(request),
        path="/")
    return resp


# ── la porta ────────────────────────────────────────────────────────────────

@router.get("/accedi")
def accedi(request: Request, errore: str = ""):
    """Sul PC di casa non c'è niente da fare qui: si entra e basta."""
    if not auth.richiede_accesso():
        return RedirectResponse("/", status_code=303)
    if auth.utente_da_richiesta(request) is not None:
        return RedirectResponse("/", status_code=303)
    return _pagina(request, "accedi.html", {
        "errore": bool(errore), "configurato": accesso.configurato()})


@router.get("/accedi/google")
def accedi_google(request: Request, next: str = "/"):
    if not auth.richiede_accesso() or not accesso.configurato():
        return RedirectResponse("/accedi?errore=1", status_code=303)
    giro = accesso.nuovo_giro()
    giro["next"] = _dove_tornare(next)
    resp = RedirectResponse(
        accesso.url_di_google(giro, accesso.uri_di_ritorno(request)),
        status_code=303)
    # dieci minuti: il tempo di scegliere l'account, non di più
    resp.set_cookie(NOME_COOKIE_GIRO,
                    sicurezza.firma_biglietto(giro, SESSION_KEY, DURATA_GIRO),
                    max_age=DURATA_GIRO, httponly=True, samesite="lax",
                    secure=accesso.connessione_sicura(request), path="/accedi")
    return resp


@router.get(accesso.RITORNO)
def ritorno_da_google(request: Request, code: str = "", state: str = "",
                      error: str = ""):
    """Google ci rimanda qui. Da qui in poi è tutto una sequenza di rifiuti
    possibili, e ognuno finisce allo stesso modo: fuori."""
    if not auth.richiede_accesso():
        return RedirectResponse("/", status_code=303)
    fuori = RedirectResponse("/accedi?errore=1", status_code=303)
    fuori.delete_cookie(NOME_COOKIE_GIRO, path="/accedi")

    if error:                                   # l'utente ha detto «no» a Google
        return RedirectResponse("/accedi", status_code=303)
    giro = sicurezza.leggi_biglietto(
        request.cookies.get(NOME_COOKIE_GIRO, ""), SESSION_KEY)
    if not giro:
        return fuori                            # nessun giro aperto, o scaduto
    if not state or state != giro.get("state"):
        # questo ritorno non l'abbiamo chiesto noi
        return fuori
    email, motivo = accesso.chi_e_tornato(code, giro,
                                          accesso.uri_di_ritorno(request))
    if not email:
        return fuori
    if not auth.email_ammessa(email):
        # il rifiuto che conta: un account Google ce l'ha mezzo mondo
        return fuori

    # Riconosciuto. Ma non è ancora entrato: manca il codice a sei cifre.
    dopo = "/accedi/codice" if auth.totp_attivo() else "/accedi/attiva"
    dove = _dove_tornare(giro.get("next", "/"))
    resp = RedirectResponse(f"{dopo}?next={dove}", status_code=303)
    resp.delete_cookie(NOME_COOKIE_GIRO, path="/accedi")
    return _metti_biglietto(resp, request, email, completo=False)


# ── il secondo fattore ──────────────────────────────────────────────────────

@router.get("/accedi/codice")
def pagina_codice(request: Request, next: str = "/", errore: str = ""):
    email = auth.email_parziale(request)
    if not email or not auth.totp_attivo():
        return RedirectResponse("/accedi", status_code=303)
    return _pagina(request, "accedi_codice.html", {
        "email": email, "next": _dove_tornare(next),
        "errore": bool(errore), "attesa": accesso.in_pausa()})


@router.post("/accedi/codice")
def verifica_codice(request: Request, codice: str = Form(""),
                    next: str = Form("/")):
    email = auth.email_parziale(request)
    if not email or not auth.totp_attivo():
        return RedirectResponse("/accedi", status_code=303)
    dove = _dove_tornare(next)
    if accesso.in_pausa():
        return RedirectResponse(f"/accedi/codice?next={dove}&errore=1",
                                status_code=303)
    if not sicurezza.codice_valido(auth.segreto_totp(), codice):
        accesso.segna_errore()
        return RedirectResponse(f"/accedi/codice?next={dove}&errore=1",
                                status_code=303)
    accesso.azzera_errori()
    resp = RedirectResponse(dove, status_code=303)
    return _metti_biglietto(resp, request, email, completo=True)


@router.get("/accedi/attiva")
def pagina_attiva(request: Request, next: str = "/", errore: str = ""):
    """Si attacca l'app authenticator. Una volta sola nella vita dell'app.

    Due condizioni, e servono tutte e due: il biglietto parziale (Google ti ha
    riconosciuto) **e** il fatto che il secondo fattore non sia già attivo.
    Senza la seconda, chiunque arrivi fin qui potrebbe attaccarci un telefono
    nuovo — cioè scavalcare il secondo fattore invece di superarlo.
    """
    email = auth.email_parziale(request)
    if not email:
        return RedirectResponse("/accedi", status_code=303)
    if auth.totp_attivo():
        return RedirectResponse("/accedi/codice", status_code=303)
    # Il segreto in prova sta in un cookie firmato, non ancora nel database: se
    # l'attivazione si interrompe a metà non resta un secondo fattore attivo che
    # nessun telefono sa fare. Nel cookie non è un segreto svelato: è la stessa
    # cifra che l'utente sta leggendo sullo schermo per copiarla.
    proposto = request.cookies.get(NOME_COOKIE_ATTIVA, "")
    corpo = sicurezza.leggi_biglietto(proposto, SESSION_KEY) or {}
    segreto = corpo.get("segreto") or sicurezza.nuovo_segreto()
    resp = _pagina(request, "accedi_attiva.html", {
        "email": email, "next": _dove_tornare(next), "errore": bool(errore),
        "segreto": segreto, "a_gruppi": _a_gruppi(segreto),
        "uri": sicurezza.uri_authenticator(segreto, email)})
    resp.set_cookie(NOME_COOKIE_ATTIVA,
                    sicurezza.firma_biglietto({"segreto": segreto},
                                              SESSION_KEY, DURATA_GIRO),
                    max_age=DURATA_GIRO, httponly=True, samesite="lax",
                    secure=accesso.connessione_sicura(request), path="/accedi")
    return resp


@router.post("/accedi/attiva")
def conferma_attiva(request: Request, codice: str = Form(""),
                    next: str = Form("/")):
    email = auth.email_parziale(request)
    if not email:
        return RedirectResponse("/accedi", status_code=303)
    if auth.totp_attivo():
        return RedirectResponse("/accedi/codice", status_code=303)
    dove = _dove_tornare(next)
    corpo = sicurezza.leggi_biglietto(
        request.cookies.get(NOME_COOKIE_ATTIVA, ""), SESSION_KEY) or {}
    segreto = corpo.get("segreto", "")
    # Il segreto si salva SOLO dopo che un codice giusto ha dimostrato che il
    # telefono lo sa fare. Salvarlo prima vuol dire poter restare chiusi fuori
    # da casa propria per un errore di battitura.
    if not segreto or not sicurezza.codice_valido(segreto, codice):
        return RedirectResponse(f"/accedi/attiva?next={dove}&errore=1",
                                status_code=303)
    auth.imposta_segreto_totp(segreto)
    accesso.azzera_errori()
    resp = RedirectResponse(dove, status_code=303)
    resp.delete_cookie(NOME_COOKIE_ATTIVA, path="/accedi")
    return _metti_biglietto(resp, request, email, completo=True)


def _a_gruppi(segreto: str, quanti: int = 4) -> str:
    """Il segreto a gruppetti: si copia a mano, e trentadue caratteri di fila
    si sbagliano."""
    return " ".join(segreto[i:i + quanti] for i in range(0, len(segreto), quanti))


# ── uscire ──────────────────────────────────────────────────────────────────

@router.post("/esci")
def esci(request: Request):
    resp = RedirectResponse("/accedi", status_code=303)
    # `delete_cookie` da solo non basta su tutti i browser: prima lo si svuota
    # e lo si fa scadere, poi lo si cancella. Un'uscita che non esce sarebbe il
    # peggior tipo di bug — sembra funzionare.
    resp.set_cookie(auth.NOME_COOKIE, "", max_age=0, expires=0, httponly=True,
                    samesite="lax", secure=accesso.connessione_sicura(request),
                    path="/")
    resp.delete_cookie(auth.NOME_COOKIE, path="/")
    return resp


@router.get("/esci")
def esci_get(request: Request):
    """Comodità: un link vale una form. Non c'è niente da proteggere in
    un'uscita — il peggio che può fare un dispetto è farti rifare il login."""
    return esci(request)
