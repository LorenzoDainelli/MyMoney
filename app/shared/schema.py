"""Piccoli attrezzi per far evolvere le tabelle su entrambi i motori.

Quando aggiungiamo una funzione all'app spesso serve una colonna nuova. Il
comando che le crea (`create_all`) non tocca le tabelle già esistenti, quindi le
colonne nuove vanno aggiunte a mano — ed è qui che i due motori parlano lingue
diverse:

- per sapere che colonne ha una tabella, SQLite usa `PRAGMA table_info`, che su
  PostgreSQL non esiste: qui lo chiediamo a SQLAlchemy, che sa farlo per tutti;
- il tipo «data e ora» si scrive DATETIME su SQLite e TIMESTAMP su PostgreSQL:
  lo facciamo scrivere a SQLAlchemy invece che a mano;
- un sì/no vale 0 o 1 su SQLite, ma PostgreSQL vuole FALSE o TRUE e rifiuta lo
  zero: i valori di partenza li passiamo come veri valori Python, non come testo
  dentro la frase SQL.

Tutto quello che c'è qui è idempotente: si può rieseguire all'infinito, e se la
colonna c'è già non fa niente.
"""
from sqlalchemy import inspect, text


def colonne_di(conn, tabella: str) -> set[str]:
    """Nomi delle colonne di una tabella; insieme vuoto se la tabella non c'è."""
    insp = inspect(conn)
    if not insp.has_table(tabella):
        return set()
    return {c["name"] for c in insp.get_columns(tabella)}


def _letterale(valore, dialetto) -> str:
    """Il valore di partenza, scritto come lo capisce QUESTO motore."""
    if isinstance(valore, bool):
        # SQLite storicamente usa 0/1; PostgreSQL pretende FALSE/TRUE.
        if dialetto.name == "sqlite":
            return "1" if valore else "0"
        return "TRUE" if valore else "FALSE"
    if isinstance(valore, str):
        return "'" + valore.replace("'", "''") + "'"
    return str(valore)


def aggiungi_colonne(conn, tabella: str, colonne) -> None:
    """Aggiunge le colonne mancanti.

    `colonne` è una sequenza di (nome, tipo, valore_di_partenza), dove il tipo è
    un tipo SQLAlchemy già costruito (String(20), Float(), Boolean()...) e il
    valore di partenza può essere None per «nessuno».

    Se la tabella non esiste ancora non facciamo nulla: la creerà `create_all`
    con le colonne già al posto giusto.
    """
    esistenti = colonne_di(conn, tabella)
    if not esistenti:
        return
    for nome, tipo, default in colonne:
        if nome in esistenti:
            continue
        ddl = tipo.compile(dialect=conn.dialect)
        frase = f"ALTER TABLE {tabella} ADD COLUMN {nome} {ddl}"
        if default is not None:
            frase += f" DEFAULT {_letterale(default, conn.dialect)}"
        conn.execute(text(frase))
        conn.commit()


def crea_indice(conn, tabella: str, colonna: str, nome: str = "") -> None:
    """Indice su una colonna, se non c'è già. `IF NOT EXISTS` lo capiscono
    entrambi i motori, ma solo se la tabella esiste davvero."""
    if not colonne_di(conn, tabella):
        return
    nome = nome or f"ix_{tabella}_{colonna}"
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {nome} ON {tabella}({colonna})"))
    conn.commit()
