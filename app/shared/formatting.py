"""Formattazione numeri all'italiana: euro e percentuali.

Esempi:
    format_eur(1234.5)        -> "€ 1.234,50"
    format_eur_esatto(0.4045) -> "€ 0,4045"
    format_pct(2.75)          -> "2,75%"
    format_pct(20)            -> "20%"
Regola: valute in €, percentuali con %, massimo 2 decimali, virgola decimale.
"""
import re

from markupsafe import Markup, escape


def format_eur(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    s = f"{float(value):,.{decimals}f}"          # stile inglese: 1,234.50
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")  # -> 1.234,50
    return f"€ {s}"


def decimali_utili(s: str, minimo: int = 2) -> str:
    """Toglie gli zeri finali di un numero già formattato, ma non scende sotto
    `minimo` cifre: '0,4045' resta intero, '0,1300' torna '0,13', '2,0000'
    torna '2,00'. Si aspetta la virgola come separatore decimale."""
    intero, virgola, dec = s.rpartition(",")
    if not virgola:
        return s
    dec = dec.rstrip("0")
    dec = dec + "0" * max(0, minimo - len(dec))
    return f"{intero},{dec}"


def format_eur_esatto(value, decimals: int = 4) -> str:
    """Come format_eur, ma senza nascondere i decimali che ci sono davvero.

    Serve al saveback della carta: l'1% di 40,45 € è 0,4045 €, e scriverlo
    «€ 0,40» vorrebbe dire far sparire proprio la cosa da guardare. Sui numeri
    tondi si comporta come sempre — 0,13 resta «€ 0,13», non «€ 0,1300»."""
    if value is None:
        return "—"
    s = f"{float(value):,.{decimals}f}"
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"€ {decimali_utili(s)}"


def format_pct(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    s = f"{float(value):.{decimals}f}"
    if "." in s:                                  # togli zeri inutili: 20,00 -> 20
        s = s.rstrip("0").rstrip(".")
    return f"{s.replace('.', ',')}%"


def format_compact(value) -> str:
    """Numeri grandi in forma compatta: 12,3 mld / 456 mln / 7,8 k."""
    if value is None:
        return "—"
    v = float(value)
    for div, suf in ((1e12, "T"), (1e9, "mld"), (1e6, "mln"), (1e3, "k")):
        if abs(v) >= div:
            return f"{v / div:.1f}".replace(".", ",") + f" {suf}"
    return f"{v:.0f}"


def format_qty(value) -> str:
    """Quantità possedute: fino a 4 decimali, senza zeri inutili (frazioni ETF)."""
    if value is None:
        return "—"
    s = f"{float(value):.4f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


# --- testo dell'AI: paragrafi + numeri in evidenza -------------------------
# Cifre da mettere in risalto: importi (1.234,56€ / €1.234,56) e percentuali
# (+12,3% / -4%). Servono a "dare appigli" all'occhio mentre si legge.
_RE_CIFRA = re.compile(
    r"([+-]?\d[\d. \s]*(?:,\d+)?\s*(?:€|%)|€\s*[+-]?\d[\d. \s]*(?:,\d+)?)")


def format_ai_text(testo) -> Markup:
    """Trasforma il testo dell'AI in paragrafi HTML con i numeri evidenziati.

    Il testo NON viene riscritto: si spezza sui capoversi e si avvolgono le
    cifre in <b>. Tutto è prima passato dall'escape, quindi eventuale HTML nel
    testo resta innocuo."""
    grezzo = (testo or "").strip()
    if not grezzo:
        return Markup("")
    blocchi = [b.strip() for b in re.split(r"\n\s*\n", grezzo) if b.strip()]
    if len(blocchi) == 1:                      # nessuna riga vuota: vado a capo singolo
        blocchi = [b.strip() for b in grezzo.splitlines() if b.strip()]
    out = []
    for b in blocchi:
        sicuro = str(escape(b)).replace("\n", " ")
        sicuro = _RE_CIFRA.sub(r'<b class="ai-cifra">\1</b>', sicuro)
        out.append(f"<p>{sicuro}</p>")
    return Markup("".join(out))
