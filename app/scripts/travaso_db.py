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

**Le impostazioni sono l'eccezione, e non copiarle tutte è il punto.** In quella
tabella stanno mescolate tre cose molto diverse: preferenze innocue (tema,
lingua, fuso), **segreti** (la chiave Gemini, il client secret del Drive, la
chiave Vertex) e roba che appartiene al **dispositivo** su cui gira l'app (il
segreto del secondo fattore del server, il diario del corriere Drive). Copiarle
in blocco vorrebbe dire portare i segreti dentro un database in rete — proprio
ciò che il piano dice di non fare (`docs/PIANO-CLOUD.md` §3.3) — e cancellare il
secondo fattore del server, perché sul PC non esiste.

Quindi qui vale la regola opposta a quella del resto dello script: **viaggia
solo ciò che è nell'elenco**. Una chiave nuova nasce «non viaggia», e
dimenticarsi di escluderla non è più possibile. Quelle che restano indietro
vengono stampate, così non se ne perde nessuna per silenzio.

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

# ── le impostazioni: l'elenco di ciò che VIAGGIA ────────────────────────────
TABELLA_IMPOSTAZIONI = "shared_settings"
COLONNA_CHIAVE = "chiave"

IMPOSTAZIONI_CHE_VIAGGIANO = frozenset({
    # come vuoi vedere l'app
    "ui_theme", "ui_lang", "ui_anim",
    # dove sei: decide la data dei movimenti e ogni orario mostrato
    "fuso_orario",
    # come è configurato l'agente — sono scelte, non chiavi.
    #
    # ATTENZIONE, qui c'è una trappola già scattata per davvero (08/08/2026):
    # `ai_provider` viaggia, `vertex_service_account_json` no — è un segreto, e
    # sta nell'elenco qui sotto. Di là restava scritto «vertex» senza il modo di
    # parlarci: l'agente era spento IN SILENZIO, con le vecchie letture ancora
    # sulle pagine a far sembrare tutto normale. **Una scelta può viaggiare dove
    # la sua chiave non può.** Continua a viaggiare (è una preferenza, come
    # `ai_mode`), ma adesso `ai.get_provider()` se ne accorge e ripiega su
    # Studio, e le Impostazioni lo dicono invece di tacere.
    "ai_mode", "ai_provider", "gemini_model",
    "vertex_model", "vertex_project", "vertex_location",
    # quello che l'agente ha già scritto: si rigenera, ma perderlo vorrebbe dire
    # aprire il server con tutti i riquadri vuoti
    "dash_ai", "fin_ai", "ai_take_IWDA",
    "ai_metrica_risultato", "ai_metrica_valore",
    # fotografie già calcolate: senza `wealth_cache` il grafico del patrimonio
    # nasce vuoto e resta vuoto finché non lo si ricostruisce
    "wealth_cache", "perf12m_snapshot", "risk_snapshot",
})

# Restano indietro APPOSTA, e si sa perché. Serve a distinguerle da quelle che
# nessuno ha ancora classificato: le prime sono una decisione, le seconde una
# dimenticanza, e in fondo allo script vengono stampate in due elenchi diversi.
IMPOSTAZIONI_CHE_RESTANO = {
    "gemini_api_key": "segreto — va in Secret Manager, non in un database in rete",
    "vertex_service_account_json": "segreto — idem",
    "drive_client_secret": "segreto — idem",
    "drive_client_id": "del corriere Drive, che va in pensione",
    "drive_token": "segreto del corriere Drive",
    "drive_seen": "diario del corriere Drive",
    "drive_last_sync": "diario del corriere Drive",
    "drive_last_upload_hash": "diario del corriere Drive",
    "sync_device_id": "è l'identità di QUESTO PC: sul server sarebbe una bugia",
    "sync_needs_update": "stato del corriere Drive",
    "auth_totp_segreto": "il secondo fattore è del SERVER: qui non c'è, e "
                         "portarci un vuoto lo cancellerebbe",
    "auth_totp_tentativi": "conteggio degli errori, del server",
}

# Le sole chiavi la cui PRESENZA di là è un guaio, e che quindi vengono
# controllate alla fine. L'elenco è corto apposta: sono i segreti, e basta.
#
# Le altre che non viaggiano non stanno qui perché la loro presenza di là non
# vuol dire niente di male — `sync_device_id`, per dirne una, il server se la
# scrive da solo la prima volta che tocca un movimento. Metterla fra i controlli
# darebbe un falso allarme a ogni travaso riuscito, e un allarme che suona
# sempre è un allarme che si impara a ignorare.
SEGRETI_CHE_NON_DEVONO_ARRIVARE = frozenset({
    "gemini_api_key",
    "vertex_service_account_json",
    "drive_client_secret",
    "drive_token",
})


def _impostazioni_che_passano(righe: list[dict]) -> tuple[list[dict], list, list]:
    """Divide le impostazioni in: viaggiano · restano apposta · sconosciute."""
    passano, note, sconosciute = [], [], []
    for r in righe:
        k = r.get(COLONNA_CHIAVE)
        if k in IMPOSTAZIONI_CHE_VIAGGIANO:
            passano.append(r)
        elif k in IMPOSTAZIONI_CHE_RESTANO:
            note.append((k, IMPOSTAZIONI_CHE_RESTANO[k]))
        else:
            sconosciute.append(k)
    return passano, note, sconosciute


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
                if t.name == TABELLA_IMPOSTAZIONI:
                    # Solo le righe che stiamo per riscrivere. Un DELETE secco
                    # qui porterebbe via il segreto del secondo fattore del
                    # server — che sul PC non esiste e quindi non tornerebbe.
                    c.execute(t.delete().where(
                        t.c[COLONNA_CHIAVE].in_(sorted(IMPOSTAZIONI_CHE_VIAGGIANO))))
                else:
                    c.execute(text(f'DELETE FROM "{t.name}"'))
        print("  destinazione svuotata (le impostazioni del server restano)")

    resta_indietro, mai_viste = [], []
    rimandate = []   # (tabella, [(id, {colonna: valore})])
    for t in tabelle:
        auto = [c for c in AUTO_RIFERIMENTI.get(t.name, []) if c in t.c]
        with src.connect() as cs:
            righe = [dict(r._mapping) for r in cs.execute(select(t).order_by(*t.primary_key.columns))]
        if t.name == TABELLA_IMPOSTAZIONI:
            righe, resta_indietro, mai_viste = _impostazioni_che_passano(righe)
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

    if resta_indietro:
        print("\n  impostazioni lasciate qui, apposta:")
        for chiave, perche in resta_indietro:
            print(f"    {chiave:32s} {perche}")
    if mai_viste:
        # Non è un errore, ma nemmeno una cosa da ignorare: qualcuno ha aggiunto
        # un'impostazione e nessuno ha detto se debba viaggiare.
        print("\n  ATTENZIONE — impostazioni che non conosco, quindi restano qui:")
        for chiave in mai_viste:
            print(f"    {chiave}")
        print("    Guardale: se devono viaggiare, vanno messe in "
              "IMPOSTAZIONI_CHE_VIAGGIANO.")

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
    """Conta le righe di qua e di là. Zero differenze o è un fallimento.

    Sulle impostazioni il conto giusto non è «quante ce n'erano» ma «quante
    dovevano viaggiare»: confrontare i totali farebbe fallire ogni travaso
    riuscito, e — peggio — passare per riuscito un travaso che ha copiato anche
    ciò che non doveva.
    """
    print("\n  verifica:")
    problemi = 0
    for t in tabelle:
        if t.name == TABELLA_IMPOSTAZIONI:
            elenco = sorted(IMPOSTAZIONI_CHE_VIAGGIANO)
            conta = select(func.count()).select_from(t).where(
                t.c[COLONNA_CHIAVE].in_(elenco))
            with src.connect() as cs, dst.connect() as cd:
                a = cs.execute(conta).scalar()
                b = cd.execute(conta).scalar()
                fuggite = cd.execute(
                    select(t.c[COLONNA_CHIAVE]).where(
                        t.c[COLONNA_CHIAVE].in_(sorted(SEGRETI_CHE_NON_DEVONO_ARRIVARE)))
                ).scalars().all()
            stato = "ok" if a == b else "DIVERSO"
            if a != b:
                problemi += 1
            print(f"    {t.name:34s} {a:>6} -> {b:>6}  {stato} (solo quelle che viaggiano)")
            # I segreti del PC non devono essere arrivati. Se ci sono, o è un
            # travaso vecchio o qualcosa è sfuggito: in tutti e due i casi si
            # deve saperlo, non scoprirlo un anno dopo.
            if fuggite:
                problemi += 1
                print(f"    {'':34s} SEGRETI ARRIVATI DI LA': {', '.join(sorted(fuggite))}")
            continue
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
