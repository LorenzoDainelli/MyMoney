"""Sezione Notizie (Fase 5): mostra le notizie del news-monitor nell'app.

Sola lettura del file di stato del robot; nessuna chiamata di rete qui.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from shared.templating import templates
from news import reader, verifica

router = APIRouter()


@router.get("/notizie", response_class=HTMLResponse)
def notizie(request: Request):
    return templates.TemplateResponse(request, "notizie.html", {
        "active": "notizie",
        "cards": reader.news_cards(limit=30),
        "aggiornato": reader.latest_date(),
    })


@router.get("/stime", response_class=HTMLResponse)
def stime(request: Request):
    """Come sono andate le stime del monitor. La verifica NON parte da sola:
    scarica lo storico di decine di titoli, quindi la si chiede col bottone."""
    return templates.TemplateResponse(request, "stime.html", {
        "active": "notizie",
        "v": verifica.dalla_cache(),
    })


@router.post("/stime/calcola")
def stime_calcola():
    verifica.calcola()
    return RedirectResponse("/stime", status_code=303)
