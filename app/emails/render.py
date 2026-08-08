"""Genera l'HTML dell'email dal JSON del report — design MyMoney.

Stessa struttura dati di sempre (vedi prompts/report.md): il modello produce solo
i dati, qui si costruisce l'HTML. I colori sono i token del MyMoney Design System
(app/static/styles.css + tokens/) FISSATI in esadecimale: i client di posta non supportano
le variabili CSS, quindi gli stili sono tutti inline. Solo libreria standard.

Struttura attesa del JSON:
{
  "date": "22 giugno 2026",
  "test_mode": true,
  "note": "...",                                              # opz. (weekly)
  "diagnostic": {"titoli_cercati": 27, "candidati": 0, "soglia": 50},  # opz.
  "quiet": {"azioni": [...], "etf": [...]} | [...],           # opz. (weekly)
  "items": [ {ticker, tickers, also, tipo_evento, rilevanza, titolo, riassunto,
              impatto:{breve,medio,lungo}, confidenza, tag, sentiment_analisti,
              fonti:[{nome,url}]} ]
}
"""
import html
from datetime import datetime, timezone, timedelta

# ---- token MyMoney (da app/static/tokens/colors.css, tema chiaro) ----
BG = "#F4F6EF"          # --neutral-50: sfondo caldo
CARD = "#FFFFFF"        # --surface
INK = "#181B14"         # --neutral-900: testo
MUTED = "#585E4F"       # --neutral-600: testo secondario
FAINT = "#6B7160"       # --neutral-500: disclaimer
BORDER = "#E3E7D8"      # --neutral-150
BORDER_2 = "#D2D8C2"    # --neutral-200 (rail neutro)
SURFACE_ALT = "#EEF1E5" # --neutral-100: pill neutre
LIME = "#A6DA47"        # --lime-400 ★ firma
ON_LIME = "#1B2A05"     # --on-accent
LIME_DEEP = "#557C18"   # --lime-700: link e label
POS = "#1E9E5A";  POS_BG = "#E2F5EA"
NEG = "#E2474A";  NEG_BG = "#FCE9E9"
NEG_TX = "#A41F22"
WARN_BG = "#FFFBE8"; WARN_BD = "#FCE992"; WARN_TX = "#876A07"   # giallo pastello
YELLOW_SOFT = "#FEF4C4"  # --yellow-100 (badge rilevanza media)

FONT = ("system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif")

MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]

IMPACT = {
    "positivo": ("▲", POS, POS_BG),
    "neutro":   ("=", MUTED, SURFACE_ALT),
    "negativo": ("▼", NEG, NEG_BG),
}


def _oggi() -> str:
    """La data in cima all'email, nel fuso scelto nell'app.

    Prima erano due ore fisse sopra UTC: giusto per l'Italia d'estate, sbagliato
    d'inverno e sbagliato ovunque altro. Questo file gira anche fuori dall'app
    (le routine nel cloud lo chiamano da scripts/render_email.py), dove il
    database non c'è: in quel caso vale `MYMONEY_FUSO`, altrimenti l'Italia.
    """
    try:
        from shared import tempo
        d = tempo.adesso()
    except Exception:
        import os
        from zoneinfo import ZoneInfo
        try:
            zona = ZoneInfo(os.environ.get("MYMONEY_FUSO", "").strip() or "Europe/Rome")
        except Exception:
            zona = timezone.utc
        d = datetime.now(zona)
    return f"{d.day} {MESI[d.month - 1]} {d.year}"


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def pill(text, color, bg):
    return (f'<span style="display:inline-block;padding:4px 9px;border-radius:12px;'
            f'margin:2px 4px 2px 0;font-size:13px;white-space:nowrap;'
            f'color:{color};background:{bg};">{esc(text)}</span>')


def _norm_impact(val) -> str:
    """Riduce un valore d'impatto a una sola parola, anche se il modello ci ha
    messo una frase (es. 'positivo perché...' -> 'positivo')."""
    s = str(val or "").lower()
    if "positiv" in s:
        return "positivo"
    if "negativ" in s:
        return "negativo"
    return "neutro"


def _overall_color(imp: dict) -> str:
    """Colore del rail sinistro della card secondo l'impatto netto."""
    vals = [_norm_impact((imp or {}).get(k)) for k in ("breve", "medio", "lungo")]
    pos, neg = vals.count("positivo"), vals.count("negativo")
    if pos > neg:
        return POS
    if neg > pos:
        return NEG
    return BORDER_2


def _rel_badge(score) -> str:
    """Badge rilevanza (soglie invariate: 70 critico, 50 report)."""
    try:
        s = int(score)
    except (TypeError, ValueError):
        s = 0
    if s >= 70:      # critico (soglia event-check)
        bg, col = NEG_BG, NEG_TX
    elif s >= 50:    # importante (entra nei report)
        bg, col = YELLOW_SOFT, WARN_TX
    else:
        bg, col = SURFACE_ALT, MUTED
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f'font-size:12px;font-weight:700;color:{col};background:{bg};">{s}/100</span>')


def impact_block(imp: dict) -> str:
    out = ['<div style="margin:8px 0;">']
    for label, key in (("Breve", "breve"), ("Medio", "medio"), ("Lungo", "lungo")):
        val = _norm_impact((imp or {}).get(key))
        arrow, color, bg = IMPACT[val]
        out.append(pill(f"{label} {arrow} {val}", color, bg))
    return "".join(out) + "</div>"


def card(it: dict) -> str:
    tickers = it.get("tickers") or ([it.get("ticker")] if it.get("ticker") else [])
    tk = " / ".join(esc(t) for t in tickers)
    bordo = _overall_color(it.get("impatto"))
    parts = [f'<div style="width:100%;box-sizing:border-box;background:{CARD};'
             f'border:1px solid {BORDER};border-left:5px solid {bordo};'
             f'border-radius:12px;padding:14px 16px;margin:0 0 14px;">']
    # riga alto: tipo evento + badge rilevanza
    parts.append(
        f'<div style="font-size:12px;color:{LIME_DEEP};font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.4px;margin-bottom:2px;">{esc(it.get("tipo_evento","NEWS"))} '
        f'&nbsp;{_rel_badge(it.get("rilevanza"))}</div>')
    # titolo
    parts.append(f'<div style="font-size:17px;font-weight:700;color:{INK};margin:6px 0;">'
                 f'{tk + " — " if tk else ""}{esc(it.get("titolo",""))}</div>')
    # nota "tocca anche"
    if it.get("also"):
        parts.append(f'<div style="background:{WARN_BG};border:1px solid {WARN_BD};border-radius:8px;'
                     f'padding:8px 10px;margin:6px 0;font-size:13px;color:{WARN_TX};">'
                     f'⚠️ {esc(it["also"])}</div>')
    # riassunto
    parts.append(f'<div style="font-size:15px;color:{INK};line-height:1.5;margin:6px 0;">'
                 f'{esc(it.get("riassunto",""))}</div>')
    # impatto + confidenza
    parts.append(impact_block(it.get("impatto")))
    parts.append('<div>' + pill(f'Confidenza: {it.get("confidenza","n/d")}', MUTED, SURFACE_ALT) + '</div>')
    # tag
    if it.get("tag"):
        tags = "".join(pill(t, MUTED, SURFACE_ALT) for t in it["tag"])
        parts.append(f'<div style="margin-top:6px;">{tags}</div>')
    # sentiment analisti
    if it.get("sentiment_analisti"):
        parts.append(f'<div style="font-size:13px;color:{MUTED};margin-top:6px;">'
                     f'Analisti: {esc(it["sentiment_analisti"])}</div>')
    # fonti
    fonti = it.get("fonti") or []
    if fonti:
        links = " · ".join(
            f'<a href="{esc(f.get("url",""))}" style="color:{LIME_DEEP};text-decoration:none;">{esc(f.get("nome","fonte"))}</a>'
            for f in fonti)
        parts.append(f'<div style="font-size:13px;color:{MUTED};margin-top:8px;">Fonti: {links}</div>')
    parts.append("</div>")
    return "".join(parts)


def build_html(data: dict) -> str:
    date = esc(data.get("date") or _oggi())
    test = data.get("test_mode")
    title = ("[PROVA] " if test else "") + f"\U0001F4CA Monitor titoli — {date}"
    body = []
    # nota in alto (es. heartbeat del digest settimanale)
    if data.get("note"):
        body.append(f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;'
                    f'padding:12px 14px;margin:0 0 14px;font-size:14px;color:{INK};line-height:1.5;">'
                    f'{esc(data["note"])}</div>')
    items = data.get("items") or []
    if items:
        body.append(f'<div style="font-size:13px;color:{MUTED};margin:0 0 14px;">'
                    f'{len(items)} notizie selezionate, in ordine di rilevanza.</div>')
        body.extend(card(it) for it in items)
    elif data.get("diagnostic"):
        diag = data["diagnostic"]
        body.append(
            f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:12px;padding:16px;">'
            f'<div style="font-weight:700;color:{INK};font-size:16px;">EMAIL DI PROVA — sistema attivo</div>'
            f'<div style="font-size:14px;color:{INK};margin-top:8px;line-height:1.5;">'
            f'Nessuna notizia sopra soglia in questa esecuzione. '
            f'Titoli cercati: {esc(diag.get("titoli_cercati","n/d"))} · '
            f'candidati: {esc(diag.get("candidati","0"))} · '
            f'soglia: {esc(diag.get("soglia","n/d"))}.</div></div>')
    # elenco "titoli tranquilli" (digest settimanale): lista semplice oppure
    # oggetto {"azioni":[nomi], "etf":[nomi abbreviati]}
    quiet = data.get("quiet")
    if quiet:
        def _chips(names):
            return "".join(pill(n, MUTED, SURFACE_ALT) for n in names)
        if isinstance(quiet, dict):
            sezioni = ""
            for label, key in (("Azioni", "azioni"), ("ETF", "etf")):
                names = quiet.get(key) or []
                if names:
                    sezioni += (f'<div style="font-size:13px;color:{MUTED};margin:8px 0 2px;">'
                                f'{label}</div>{_chips(names)}')
        else:
            sezioni = _chips(quiet)
        body.append(f'<div style="margin-top:10px;"><div style="font-weight:700;color:{INK};'
                    f'font-size:14px;margin-bottom:6px;">\U0001F4ED Titoli tranquilli (nessuna notizia)</div>'
                    f'{sezioni}</div>')

    disclaimer = (
        "Analisi qualitativa assistita, non una previsione di prezzo e non un consiglio "
        "operativo. Le stime d'impatto hanno il livello di confidenza indicato. "
        "Fonti citate per ogni notizia.")
    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:{BG};">
<div style="width:100%;max-width:600px;margin:0 auto;padding:16px;box-sizing:border-box;
font-family:{FONT};color:{INK};">
  <div style="background:{LIME};color:{ON_LIME};border-radius:16px;padding:16px 18px;margin-bottom:16px;">
    <div style="font-size:19px;font-weight:800;letter-spacing:-.01em;">{esc(title)}</div>
  </div>
  {''.join(body)}
  <div style="font-size:12px;color:{FAINT};line-height:1.5;border-top:1px solid {BORDER};
  margin-top:8px;padding-top:12px;">{esc(disclaimer)}</div>
</div></body></html>"""
