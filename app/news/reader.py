"""Lettura delle notizie dal news-monitor (sezione Notizie, Fase 5).

Il robot-notizie gira nel cloud e committa lo stato in `state/` alla radice del
repo. Qui leggiamo `state/predictions.json` (le notizie analizzate, con impatto,
confidenza e rilevanza) e prepariamo card pronte da mostrare, nello stesso stile
visivo delle email. **Sola lettura**: l'app non modifica nulla del robot.
"""
import json
import subprocess
import urllib.parse

from shared.config import APP_DIR, CACHE_DIR

# state/ sta alla radice del repo, un livello sopra app/
STATE_DIR = APP_DIR.parent / "state"
PREDICTIONS = STATE_DIR / "predictions.json"
# copia scaricata da GitHub (origin/main) all'avvio: il robot committa lì.
# Sta in CACHE_DIR e non in data/ perché su un server quel disco è di sola
# lettura: è una copia di comodo, si riscarica, non è un dato da custodire.
REMOTE_CACHE = CACHE_DIR / "news_remote.json"

# Impatto -> (freccia, classe .pill del design system). I COLORI vivono nel CSS
# (light/dark), qui usiamo solo le classi: niente hex fissi -> temi coerenti.
# Frecce come nel design freeze: ↗ positivo · → neutro · ↘ negativo.
_IMPACT = {
    "positivo": ("↗", "green"),  # .pill.green -> var(--pos)
    "neutro":   ("→", "gray"),   # .pill.gray  -> grigio neutro
    "negativo": ("↘", "red"),    # .pill.red   -> var(--neg)
}


def _host(url: str) -> str:
    """Nome della testata dal dominio (design: '04/07 · Reuters')."""
    try:
        host = urllib.parse.urlsplit(url or "").netloc
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return ""


def _data_breve(iso: str) -> str:
    """'2026-07-04' -> '04/07' (formato del design)."""
    s = str(iso or "")[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}"
    return s


def _norm_impact(val) -> str:
    s = str(val or "").lower()
    if "positiv" in s:
        return "positivo"
    if "negativ" in s:
        return "negativo"
    return "neutro"


def _norm_conf(val) -> str:
    s = str(val or "").lower()
    if "alt" in s:
        return "alta"
    if "bass" in s:
        return "bassa"
    return "media"


def _overall_class(imp: dict) -> str:
    """Variante .card dal rail di sinistra secondo l'impatto netto:
    'green' (pos) / 'red' (neg) / '' (neutro, card liscia). Il colore è nel CSS."""
    vals = [_norm_impact((imp or {}).get(k)) for k in ("breve", "medio", "lungo")]
    pos, neg = vals.count("positivo"), vals.count("negativo")
    if pos > neg:
        return "green"
    if neg > pos:
        return "red"
    return ""


def _rel_class(score) -> str:
    """Classe .badge per la rilevanza (soglie INVARIATE: 70 critico, 50 report).
    high=critico (rosso) · mid=report (giallo) · low=info (grigio). Colori nel CSS."""
    try:
        s = int(score)
    except (TypeError, ValueError):
        s = 0
    if s >= 70:
        return "high"
    if s >= 50:
        return "mid"
    return "low"


def refresh_from_origin() -> bool:
    """Scarica l'ultima versione delle notizie dal repo GitHub: il robot nel
    cloud committa `state/predictions.json` su origin/main, quindi all'avvio
    facciamo `git fetch` e leggiamo il file DA origin/main (il working tree e i
    tuoi file locali NON vengono toccati). Se git o la rete mancano, si resta
    sui dati locali: mai far fallire l'app per questo."""
    repo = str(APP_DIR.parent)
    try:
        subprocess.run(["git", "-C", repo, "fetch", "origin", "main", "--quiet"],
                       check=True, timeout=30, capture_output=True)
        out = subprocess.run(
            ["git", "-C", repo, "show", "origin/main:state/predictions.json"],
            check=True, timeout=15, capture_output=True)
        data = json.loads(out.stdout.decode("utf-8"))
        if not isinstance(data.get("items"), list):
            return False
        REMOTE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        REMOTE_CACHE.write_text(json.dumps(data, ensure_ascii=False),
                                encoding="utf-8")
        return True
    except Exception:
        return False


def _read_items(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("items", [])
    except (OSError, json.JSONDecodeError):
        return []


def _load_items():
    """Notizie locali (state/) e scaricate da GitHub: vince il set più recente.

    `predictions.json` contiene anche le notizie **analizzate ma non inviate**
    (`inviata: false`): servono al robot come storico per verificare a posteriori
    le proprie stime, NON vanno mostrate qui. Qui si vede ciò che è arrivato per
    email — altrimenti la sezione si riempie di notizie marginali (filosofia:
    ridurre il rumore). Le voci vecchie non hanno il campo: erano tutte inviate.
    """
    locali = _read_items(PREDICTIONS)
    remote = _read_items(REMOTE_CACHE)

    def ultima(items):
        return max((str(it.get("data", ""))[:10] for it in items if it.get("data")),
                   default="")

    scelti = remote if ultima(remote) >= ultima(locali) and remote else locali
    return [it for it in scelti if it.get("inviata", True)]


def news_cards(limit: int = 30):
    """Card pronte per il template, ordinate per data (recente) e rilevanza."""
    items = _load_items()
    items.sort(key=lambda it: (str(it.get("data", "")), it.get("rilevanza", 0) or 0),
               reverse=True)
    cards = []
    for it in items[:limit]:
        imp = it.get("impatto") or {}
        impatti = []
        for hk, key in (("short", "breve"), ("medium", "medio"), ("long", "lungo")):
            vw = _norm_impact(imp.get(key))
            arrow, pill = _IMPACT[vw]
            impatti.append({"hk": hk, "vw": vw, "arrow": arrow, "pill": pill})
        cards.append({
            "ticker": it.get("ticker", ""),
            "titolo": it.get("titolo", ""),
            "tipo_evento": it.get("tipo_evento", ""),
            "rilevanza": it.get("rilevanza", ""),
            "rel_class": _rel_class(it.get("rilevanza")),
            "confidenza": _norm_conf(it.get("confidenza")),
            "data": str(it.get("data", ""))[:10],
            "data_it": _data_breve(it.get("data")),
            "url": it.get("url", ""),
            "fonte": _host(it.get("url", "")),
            "impatti": impatti,
            "bordo_class": _overall_class(imp),
        })
    return cards


def latest_date() -> str:
    """Data della notizia più recente, in gg/mm/aaaa (etichetta 'aggiornato')."""
    ds = [str(it.get("data", ""))[:10] for it in _load_items() if it.get("data")]
    if not ds:
        return ""
    s = max(ds)
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
    return s
