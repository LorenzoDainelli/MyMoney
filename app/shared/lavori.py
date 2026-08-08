"""I lavori periodici: prezzi, notizie, pulizia e la fotografia del patrimonio.

Prima stavano dentro `main.py` e partivano in un thread **a ogni avvio dell'app**.
Sul PC funziona: l'app la apri una volta e resta accesa. Su un server no, per due
motivi opposti e tutti e due sbagliati:

- il server **si spegne quando non lo usi**, quindi nei giorni in cui non apri
  l'app la fotografia del patrimonio non viene scattata e il grafico ha un buco;
- e se apri l'app dieci volte, il lavoro parte dieci volte, chiamando dieci volte
  i prezzi per niente.

Qui il lavoro diventa una funzione che si può chiamare **quando serve**: da soli
all'avvio quando giriamo sul PC, oppure da fuori (Cloud Scheduler) una volta al
giorno quando giriamo su un server.

Chi chiama non aspetta il risultato: se una fonte è giù si logga e si va avanti,
non si fa mai fallire tutto per un pezzo.
"""
import logging
import secrets
import threading
import time

log = logging.getLogger("mymoney.lavori")


def token_valido(ricevuto: str) -> bool:
    """Vero solo se chi chiama ha la parola d'ordine giusta.

    Due cose importanti, in tre righe:
    - se la parola d'ordine **non è configurata**, la risposta è sempre NO. Il
      contrario — «non è impostata, quindi lascio passare» — è il modo classico
      in cui un indirizzo resta aperto per sbaglio;
    - il confronto usa compare_digest, che impiega lo stesso tempo qualunque sia
      la differenza: senza, si potrebbe indovinare la parola un carattere alla
      volta misurando quanto ci mette a rispondere.
    """
    from shared.config import JOB_TOKEN
    if not JOB_TOKEN:
        return False
    return secrets.compare_digest(ricevuto or "", JOB_TOKEN)

# Un lavoro alla volta. Serve su un server, dove possono girare più copie
# dell'app: senza, due chiamate ravvicinate farebbero il doppio lavoro e
# scriverebbero due volte la stessa fotografia.
_in_corso = threading.Lock()
_ultimo_esito: dict = {"quando": None, "passi": {}, "durata": 0.0}


def _passo(esiti: dict, nome: str, funzione) -> None:
    """Esegue un passo e ne annota l'esito. Un passo che fallisce non ferma
    gli altri: sono indipendenti, e mezzo aggiornamento è meglio di nessuno.

    Tre esiti e non due. Alcuni passi non sollevano eccezioni: si arrangiano e
    tornano `False` — le notizie fanno così di proposito, per non far mai cadere
    l'app. Chiamare «ok» un passo che non ha fatto niente vuol dire non
    accorgersene mai, ed è così che sul server le notizie sono rimaste ferme
    senza che il rapporto dicesse una parola. Chi non torna niente resta «ok».
    """
    try:
        esito = funzione()
        esiti[nome] = "niente" if esito is False else "ok"
    except Exception as e:                      # noqa: BLE001 - qui è voluto
        esiti[nome] = f"errore: {type(e).__name__}"
        log.warning("lavoro %s fallito: %s", nome, e)


def giornaliero(includi_sync: bool = True) -> dict:
    """Tutti i lavori periodici, in ordine. Torna l'esito passo per passo.

    `includi_sync` a False su un server: con un solo database non c'è niente da
    sincronizzare, e il diario del sync vuole scrivere su disco (vedi
    docs/PIANO-CLOUD.md §2).
    """
    if not _in_corso.acquire(blocking=False):
        log.info("lavori gia' in corso, salto")
        return {"saltato": "gia' in corso"}

    inizio = time.time()
    esiti: dict = {}
    try:
        from news import reader
        from portfolio import market, wealth
        from shared import storico

        _passo(esiti, "notizie", reader.refresh_from_origin)
        _passo(esiti, "prezzi", market.refresh_all)
        _passo(esiti, "fondamentali", market.refresh_all_fundamentals)
        _passo(esiti, "grafico_patrimonio", wealth.get_cached)

        if includi_sync:
            def _sync():
                from shared import drive_sync
                if drive_sync.is_configured() and drive_sync.is_connected():
                    drive_sync.sync_once()
            _passo(esiti, "sync_drive", _sync)

        def _pulizia():
            from finance.service import compatta_tombstone
            compatta_tombstone(365)
        _passo(esiti, "pulizia", _pulizia)

        # per ultima: la fotografia di oggi vuole i prezzi già freschi
        _passo(esiti, "storico", storico.registra)
    finally:
        _in_corso.release()

    durata = round(time.time() - inizio, 1)
    _ultimo_esito.update({"quando": time.time(), "passi": esiti, "durata": durata})
    log.info("lavori finiti in %ss: %s", durata, esiti)
    return {"durata": durata, "passi": esiti}


def in_background(includi_sync: bool = True) -> None:
    """Come sopra, ma senza far aspettare chi ha aperto la pagina."""
    threading.Thread(target=giornaliero, args=(includi_sync,), daemon=True).start()


def ultimo_esito() -> dict:
    """Com'è andata l'ultima volta (per la pagina delle impostazioni)."""
    return dict(_ultimo_esito)
