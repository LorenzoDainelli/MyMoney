"""Configurazione unica dei template (HTML) con i filtri di formattazione.

Importato da tutte le pagine, cosi' lo stile e i filtri (€, %, quantita') sono
identici ovunque.
"""
import time

from fastapi.templating import Jinja2Templates

from shared.config import APP_DIR
from shared import formatting
from shared import i18n, prefs

# versione degli asset statici: cambia a ogni avvio, così CSS/JS aggiornati
# non restano bloccati nella cache euristica del browser (?v=... nei link)
ASSET_V = str(int(time.time()))


def _global_context(request):
    """Inietta in OGNI pagina: traduttore (t), lingua e tema correnti, elenco lingue
    e il percorso corrente (per tornare qui dopo aver cambiato lingua/tema)."""
    ui = prefs.get_ui()          # tema+lingua+animazioni in una sola query
    return {
        "t": i18n.make_translator(ui["lang"]),
        "lang": ui["lang"],
        "theme": ui["theme"],
        "anim": ui["anim"],
        "LANGS": i18n.LANGS,
        "cur_path": request.url.path,
        "V": ASSET_V,
    }


templates = Jinja2Templates(directory=str(APP_DIR / "templates"),
                            context_processors=[_global_context])
templates.env.filters["eur"] = formatting.format_eur
templates.env.filters["eur_esatto"] = formatting.format_eur_esatto   # saveback: 0,4045 €
templates.env.filters["pct"] = formatting.format_pct
templates.env.filters["qty"] = formatting.format_qty
templates.env.filters["big"] = formatting.format_compact
templates.env.filters["ai_text"] = formatting.format_ai_text
