"""Travaso dei dati dal file SQLite a un database PostgreSQL.

Serve una volta sola, il giorno in cui l'app passa dal PC a un server. Copia
tutte le tabelle mantenendo gli **id originali** — le righe si riferiscono l'una
all'altra tramite quei numeri (un movimento sa qual è il suo portafoglio, una
riga generata sa qual è la spesa da cui nasce), quindi cambiarli spezzerebbe
tutto.

Uso:
    python scripts/travaso_db.py --da <file.db> --a <indirizzo-postgres> [--svuota]

Esempio (il database di prova qui sul PC):
    python scripts/travaso_db.py \\
        --da data/finanza.db \\
        --a "postgresql+psycopg://postgres:LAPASSWORD@127.0.0.1:55432/mymoney_dev" \\
        --svuota

Alla fine confronta il numero di righe tabella per tabella e si ferma con un
errore se anche una sola non torna: un travaso «quasi riuscito» è peggio di uno
fallito, perché non te ne accorgi.

L'indirizzo del database contiene la password: NON va scritto in un file del
repo né incollato in chat. Passalo da riga di comando o da variabile d'ambiente.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, func, insert, inspect, select, text  # noqa: E402


def _metadata():
    """Le tabelle dell'app, importate una volta sola."""
    from shared.db import Base
    import finance.models        # noqa: F401
    import portfolio.models      # noqa: F401
    import shared.ai_memory      # noqa: F401
    import shared.settings_store  # noqa: F401
    import shared.storico        # noqa: F401
    from portfolio import market  # noqa: F401
    try:
        from portfolio import analytics  # noqa: F401
    except ImportError:
        pass
    return Base.metadata


# Colonne che puntano a un'altra riga della STESSA tabella: vanno riempite in un
# secondo momento, altrimenti una riga figlia potrebbe arrivare prima della madre.
AUTO_RIFERIMENTI = {"finance_transactions": ["parent_tx_id"]}


def travasa(url_sorgente: str, url_destinazione: str, svuota: bool = False) -> int:
    md = _metadata()
    src = create_engine(url_sorgente)
    dst = create_engine(url_destinazione)

    insp_src = inspect(src)
    tabelle = [t for t in md.sorted_tables if insp_src.has_table(t.name)]
    saltate = [t.name for t in md.sorted_tables if not insp_src.has_table(t.name)]
    if saltate:
        print(f"  (non presenti nella sorgente, saltate: {', '.join(saltate)})")

    md.create_all(dst)

    if svuota:
        with dst.begin() as c:
            for t in reversed(tabelle):
                c.execute(text(f'DELETE FROM "{t.name}"'))
        print("  destinazione svuotata")

    rimandate = []   # (tabella, [(id, {colonna: valore})])
    for t in tabelle:
        auto = [c for c in AUTO_RIFERIMENTI.get(t.name, []) if c in t.c]
        with src.connect() as cs:
            righe = [dict(r._mapping) for r in cs.execute(select(t).order_by(*t.primary_key.columns))]
        if not righe:
            print(f"  {t.name:34s} 0")
            continue

        if auto:
            pk = list(t.primary_key.columns)[0].name
            da_rimettere = []
            for r in righe:
                valori = {c: r[c] for c in auto if r.get(c) is not None}
                if valori:
                    da_rimettere.append((r[pk], valori))
                for c in auto:
                    r[c] = None
            if da_rimettere:
                rimandate.append((t, da_rimettere))

        with dst.begin() as cd:
            # a blocchi: un unico INSERT da migliaia di righe può non passare
            for i in range(0, len(righe), 500):
                cd.execute(insert(t), righe[i:i + 500])
        print(f"  {t.name:34s} {len(righe)}")

    # secondo giro: i riferimenti interni, ora che tutte le righe esistono
    for t, coppie in rimandate:
        pk = list(t.primary_key.columns)[0]
        with dst.begin() as cd:
            for rid, valori in coppie:
                cd.execute(t.update().where(pk == rid).values(**valori))
        print(f"  {t.name}: {len(coppie)} riferimenti interni ricollegati")

    _risincronizza_contatori(dst, tabelle)
    return _verifica(src, dst, tabelle)


def _risincronizza_contatori(dst, tabelle) -> None:
    """PostgreSQL tiene un contatore per gli id automatici. Avendo inserito gli
    id a mano, il contatore è rimasto a zero: senza questo, il primo movimento
    nuovo proverebbe a usare l'id 1 — che esiste già — e fallirebbe."""
    if dst.dialect.name != "postgresql":
        return
    with dst.begin() as c:
        for t in tabelle:
            for col in t.primary_key.columns:
                seq = c.execute(text(
                    "SELECT pg_get_serial_sequence(:t, :c)"),
                    {"t": t.name, "c": col.name}).scalar()
                if not seq:
                    continue
                c.execute(text(
                    f'SELECT setval(:s, GREATEST(COALESCE((SELECT MAX("{col.name}") '
                    f'FROM "{t.name}"), 0), 1), '
                    f'(SELECT MAX("{col.name}") FROM "{t.name}") IS NOT NULL)'),
                    {"s": seq})
    print("  contatori degli id risincronizzati")


def _verifica(src, dst, tabelle) -> int:
    """Conta le righe di qua e di là. Zero differenze o è un fallimento."""
    print("\n  verifica:")
    problemi = 0
    for t in tabelle:
        with src.connect() as cs, dst.connect() as cd:
            a = cs.execute(select(func.count()).select_from(t)).scalar()
            b = cd.execute(select(func.count()).select_from(t)).scalar()
        stato = "ok" if a == b else "DIVERSO"
        if a != b:
            problemi += 1
        print(f"    {t.name:34s} {a:>6} -> {b:>6}  {stato}")
    return problemi


def main() -> int:
    p = argparse.ArgumentParser(description="Travaso SQLite -> PostgreSQL")
    p.add_argument("--da", required=True, help="file .db di partenza (o URL SQLAlchemy)")
    p.add_argument("--a", required=True, help="URL del database di arrivo")
    p.add_argument("--svuota", action="store_true",
                   help="cancella le tabelle di arrivo prima di copiare")
    args = p.parse_args()

    sorgente = args.da
    if not sorgente.startswith(("sqlite:", "postgresql")):
        percorso = Path(sorgente).resolve()
        if not percorso.exists():
            print(f"file non trovato: {percorso}")
            return 2
        sorgente = f"sqlite:///{percorso}"

    print(f"da:  {sorgente}")
    print(f"a:   {args.a.split('@')[-1]}")   # mai stampare la password
    print()
    problemi = travasa(sorgente, args.a, svuota=args.svuota)
    if problemi:
        print(f"\n{problemi} tabelle non tornano: travaso NON riuscito.")
        return 1
    print("\nTutto travasato e verificato.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
