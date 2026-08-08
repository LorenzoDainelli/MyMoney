"""Le icone dell'app per la schermata Home del telefono.

Perché generate e non disegnate a mano: servono in tre misure diverse, e tenerle
allineate a occhio è il modo migliore per ritrovarsi con tre logo leggermente
diversi. Qui la forma è descritta una volta sola e le misure escono da quella.

Perché un encoder PNG scritto a mano: Pillow non è fra le dipendenze dell'app, e
aggiungere una libreria da 3 MB per fare tre quadrati con una lettera sopra
sarebbe un pessimo scambio. Un PNG non compresso è zlib più quattro blocchi.

    python scripts/genera_icone.py

Rigenera `static/icone/*.png`. Va lanciato solo se si cambia il marchio.
"""
import struct
import sys
import zlib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DESTINAZIONE = APP_DIR / "static" / "icone"

# Palette MyMoney (la stessa delle email e della web app): lime pistacchio con
# inchiostro scuro sopra. Vedi CLAUDE.md.
LIME = (0xA6, 0xDA, 0x47)
INCHIOSTRO = (0x1B, 0x2A, 0x05)


def _png(larghezza: int, altezza: int, pixel) -> bytes:
    """Un PNG a colori veri. `pixel(x, y)` torna (r, g, b)."""
    righe = bytearray()
    for y in range(altezza):
        righe.append(0)                      # filtro 0 = nessuno
        for x in range(larghezza):
            righe.extend(pixel(x, y))

    def blocco(nome: bytes, dati: bytes) -> bytes:
        return (struct.pack(">I", len(dati)) + nome + dati
                + struct.pack(">I", zlib.crc32(nome + dati) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + blocco(b"IHDR", struct.pack(">IIBBBBB", larghezza, altezza, 8, 2, 0, 0, 0))
            + blocco(b"IDAT", zlib.compress(bytes(righe), 9))
            + blocco(b"IEND", b""))


def _dentro_la_emme(x: float, y: float, lato: float) -> bool:
    """La lettera M, descritta come quattro tratti spessi.

    Due montanti verticali e due diagonali che scendono al centro. Le misure
    sono frazioni del lato, così la lettera cresce insieme all'icona invece di
    diventare un francobollo sulle misure grandi.
    """
    sx, sy = x / lato, y / lato                      # coordinate 0..1
    if not (0.30 <= sy <= 0.70):
        return False
    spessore = 0.052
    # i due montanti
    if abs(sx - 0.29) <= spessore or abs(sx - 0.71) <= spessore:
        return True
    # le due diagonali: da ciascun montante giù fino al centro (a metà altezza)
    t = (sy - 0.30) / 0.20                            # 0 in alto → 1 a metà
    if 0.0 <= t <= 1.0:
        sinistra = 0.29 + t * (0.50 - 0.29)
        destra = 0.71 - t * (0.71 - 0.50)
        if abs(sx - sinistra) <= spessore or abs(sx - destra) <= spessore:
            return True
    return False


def icona(lato: int) -> bytes:
    """Lime fino ai bordi, con la M sopra.

    Niente angoli arrotondati disegnati da noi: li mette il sistema, e ognuno
    con la sua forma — iOS uno smusso, Android a volte un cerchio. Un'icona a
    fondo pieno regge tutti e due i ritagli (è quello che il manifest chiama
    `maskable`); una con gli angoli già tondi verrebbe ritagliata due volte.
    """
    def pixel(x, y):
        px, py = x + 0.5, y + 0.5
        return INCHIOSTRO if _dentro_la_emme(px, py, lato) else LIME
    return _png(lato, lato, pixel)


def main() -> int:
    DESTINAZIONE.mkdir(parents=True, exist_ok=True)
    # 180 = apple-touch-icon (iPhone) · 192 e 512 = manifest (Android)
    for lato in (180, 192, 512):
        nome = f"icona-{lato}.png"
        (DESTINAZIONE / nome).write_bytes(icona(lato))
        print(f"  {nome}")
    print(f"scritte in {DESTINAZIONE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
