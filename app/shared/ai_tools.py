"""Strumenti dell'agente: quello che può andarsi a prendere da solo.

Finché l'agente riceve solo un riassunto, resta in superficie: vede "ORCL −51%"
e non può chiedersi perché. Con gli strumenti può seguire una pista — guarda un
movimento, apre lo storico di un titolo, legge le notizie su quel ticker — e
scrivere una frase che collega due cose invece di ripetere un numero.

**Regola ferma: SOLA LETTURA.** Qui non esiste nessuna funzione che scriva,
modifichi o cancelli. L'agente non tocca il database: lo consulta. Ogni
strumento è dichiarato a mano in questo file — non c'è modo che il modello ne
inventi uno, e se chiede un nome che non esiste riceve un errore e basta.

I risultati passano dal filtro privacy prima di tornare al modello, come tutto
il resto di ciò che esce da qui.
"""
from datetime import date, datetime, timedelta

from shared import tempo

MAX_RIGHE = 25          # nessuno strumento restituisce elenchi sterminati


def _giorni_fa(n: int) -> datetime:
    return tempo.adesso() - timedelta(days=n)


# ===========================================================================
#  Gli strumenti (tutti di sola lettura)
# ===========================================================================
def movimenti(giorni: int = 30, categoria: str = "", minimo: float = 0.0) -> dict:
    """Movimenti recenti, eventualmente filtrati per categoria e importo."""
    from finance import service as fin

    giorni = max(1, min(int(giorni or 30), 400))
    da = _giorni_fa(giorni)
    cat = (categoria or "").strip().lower()
    fuori = []
    for m in fin.lista_movimenti():
        t = m["t"]
        if t.data < da:
            break                      # la lista è già ordinata dal più recente
        if cat and (m["categoria"] or "").lower() != cat:
            continue
        if minimo and (t.importo or 0) < minimo:
            continue
        fuori.append({
            "data": t.data.strftime("%d/%m/%Y"),
            "tipo": t.tipo,
            "importo": round(t.importo or 0.0, 2),
            "categoria": m["categoria"] or "",
            "descrizione": (t.descrizione or "")[:80],
            "portafoglio": m["wallet"],
        })
        if len(fuori) >= MAX_RIGHE:
            break
    return {"giorni": giorni, "quanti": len(fuori), "movimenti": fuori}


def spese_per_categoria(mesi: int = 6) -> dict:
    """Quanto è finito in ogni categoria, mese per mese: è il dato che serve per
    capire se una spesa è un'abitudine o un'eccezione."""
    from finance import service as fin
    from shared.insights import _mesi_indietro

    mesi = max(1, min(int(mesi or 6), 24))
    now = tempo.adesso()
    fuori = []
    for k in range(mesi):
        y, m = _mesi_indietro(now, k)
        r = fin.riepilogo_mese(y, m)
        fuori.append({
            "mese": f"{y}-{m:02d}",
            "entrate": r["entrate"], "uscite": r["uscite"], "saldo": r["saldo"],
            "categorie": {s["nome"]: s["tot"] for s in r["spese_categoria"][:12]},
        })
    return {"mesi": fuori}


def storico_pac() -> dict:
    """I versamenti PAC registrati, con quanto è stato versato in tutto."""
    from portfolio import versamenti
    from finance import service as fin

    lista = versamenti.lista()
    pac = fin.valore_pac_live()
    return {
        # `ora_span`, non `ora`: i titoli di un PAC non vengono eseguiti tutti
        # nello stesso istante, e all'agente va detto l'arco vero
        # («09:12–17:40»), non un minuto in cui quasi niente era stato comprato.
        # `ora` senza il fuso è un numero senza significato: gli orari di un PAC
        # possono essere scritti nell'ora di un altro Paese, e l'agente che li
        # riporta deve poter dire quale.
        "versamenti": [{"data": v["data"].strftime("%d/%m/%Y"), "ora": v["ora_span"],
                        "ora_di": v["fuso_etichetta"],
                        "importo": v["importo"], "conto": v["conto"],
                        "titoli": v["n_titoli"]} for v in lista[:MAX_RIGHE]],
        "quanti": len(lista),
        "versato_totale": pac["versato"] if pac else None,
        "valore_attuale": pac["valore"] if pac else None,
    }


def posizione(ticker: str) -> dict:
    """Scheda di un titolo in portafoglio: quanto pesa, com'è andato, che roba è."""
    from portfolio import service as pf, market

    tk = (ticker or "").strip().upper()
    if not tk:
        return {"errore": "ticker mancante"}
    vista = pf.vista_portafoglio()
    riga = next((r for r in vista["righe"]
                 if (r["p"].ticker or "").upper() == tk), None)
    if riga is None:
        return {"errore": f"{tk} non è in portafoglio"}
    p = riga["p"]
    fond = market.get_fundamentals_cached(tk) or {}
    peso = (riga["valore"] / vista["totale"] * 100) if (riga["valore"] and vista["totale"]) else None
    versato = p.versato_totale or 0.0
    # NIENTE performance a 12 mesi qui: è storia del mercato avvenuta prima
    # dell'acquisto, e messa in mano all'agente ricompare puntualmente come se
    # fosse un guadagno dell'utente. Quello che conta è il SUO risultato:
    # quanto ha versato e quanto vale adesso. Il prezzo nel tempo, quando serve
    # davvero, sta nello strumento `andamento` (solo sulla scheda di un titolo).
    return {
        "ticker": tk, "nome": p.nome_vista, "tipo": p.tipo,
        "categoria": p.categoria, "pct_target": p.pct_target,
        "valore": riga["valore"], "peso_pct": round(peso, 1) if peso else None,
        "versato": versato, "prezzo_eur": riga["prezzo_eur"],
        "risultato_utente": (round(riga["valore"] - versato, 2)
                             if (riga["valore"] and versato) else None),
        # i fondamentali normalizzati chiamano il campo 'sectors' (era letto
        # 'settori', che non esisteva: tornava sempre vuoto); per un'azione, che
        # ha un solo settore in 'sector', lo confezioniamo nella stessa forma.
        "settori": (fond.get("sectors")
                    or ([{"name": fond["sector"]}] if fond.get("sector") else []))[:5],
        "div_yield": fond.get("div_yield"),
    }


def andamento(ticker: str, periodo: str = "6mo") -> dict:
    """Prezzo di un titolo a inizio/fine periodo e variazione. Niente previsioni:
    solo cos'è successo."""
    from portfolio import market

    tk = (ticker or "").strip().upper()
    if periodo not in ("1mo", "3mo", "6mo", "1y", "2y"):
        periodo = "6mo"
    serie = market.history_closes(market._yahoo_symbol(tk), periodo, "1d")
    if not serie or len(serie) < 2:
        return {"ticker": tk, "errore": "storico non disponibile"}
    primo, ultimo = serie[0], serie[-1]
    return {
        "ticker": tk, "periodo": periodo,
        "primo": round(primo, 4), "ultimo": round(ultimo, 4),
        "minimo": round(min(serie), 4), "massimo": round(max(serie), 4),
        "variazione_pct": round((ultimo / primo - 1) * 100, 2) if primo else None,
        "nota": "prezzi nella valuta di quotazione del titolo",
    }


def notizie(ticker: str = "", quante: int = 5) -> dict:
    """Le notizie già raccolte dal monitor, con l'impatto stimato e la confidenza
    che il monitor stesso ha dichiarato. Sono valutazioni, non certezze."""
    from news import reader

    tk = (ticker or "").strip().upper()
    quante = max(1, min(int(quante or 5), 12))
    fuori = []
    for c in reader.news_cards(limit=60):
        if tk and (c.get("ticker") or "").upper() != tk:
            continue
        fuori.append({
            "ticker": c.get("ticker"), "titolo": c.get("titolo"),
            "tipo": c.get("tipo_evento") or "news", "fonte": c.get("fonte"),
            "data": c.get("data_it"), "rilevanza": c.get("rilevanza"),
        })
        if len(fuori) >= quante:
            break
    return {"quante": len(fuori), "notizie": fuori}


# ===========================================================================
#  Dichiarazione per il modello (formato function calling di Gemini)
# ===========================================================================
STRUMENTI = {
    "movimenti": {
        "fn": movimenti,
        "description": "Movimenti di denaro recenti dell'utente (entrate, uscite, "
                       "trasferimenti), filtrabili per categoria e importo minimo.",
        "parameters": {
            "type": "object",
            "properties": {
                "giorni": {"type": "integer", "description": "Quanti giorni indietro (1-400)."},
                "categoria": {"type": "string", "description": "Nome esatto della categoria, o vuoto per tutte."},
                "minimo": {"type": "number", "description": "Importo minimo in euro."},
            },
        },
    },
    "spese_per_categoria": {
        "fn": spese_per_categoria,
        "description": "Entrate, uscite e spese per categoria mese per mese: serve "
                       "a capire se una spesa è un'abitudine o un'eccezione.",
        "parameters": {
            "type": "object",
            "properties": {
                "mesi": {"type": "integer", "description": "Quanti mesi indietro (1-24)."},
            },
        },
    },
    "storico_pac": {
        "fn": storico_pac,
        "description": "I versamenti PAC registrati, con il totale versato e il valore attuale.",
        "parameters": {"type": "object", "properties": {}},
    },
    "posizione": {
        "fn": posizione,
        "description": "Scheda di un titolo in portafoglio: peso, valore, versato, "
                       "risultato dell'utente, settori, rendimento da dividendo.",
        "parameters": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Es. IWDA, ORCL."}},
            "required": ["ticker"],
        },
    },
    "andamento": {
        "fn": andamento,
        "description": "Come si è mosso il prezzo di un titolo in un periodo "
                       "(primo, ultimo, minimo, massimo, variazione %).",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "periodo": {"type": "string", "description": "1mo, 3mo, 6mo, 1y o 2y."},
            },
            "required": ["ticker"],
        },
    },
    "notizie": {
        "fn": notizie,
        "description": "Notizie già raccolte dal monitor, in generale o su un "
                       "ticker: utili per spiegare PERCHÉ un titolo si è mosso.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Vuoto per tutte."},
                "quante": {"type": "integer"},
            },
        },
    },
}

# Quali strumenti ha in mano l'agente in ciascuna pagina. Sulla scheda di un
# titolo non serve rovistare nei movimenti di casa: meno strumenti = risposte
# più a fuoco, e meno dati personali in giro.
PER_SUPERFICIE = {
    "dashboard": ("movimenti", "spese_per_categoria", "storico_pac", "posizione", "notizie"),
    "finanze": ("movimenti", "spese_per_categoria"),
    "titolo": ("posizione", "andamento", "notizie"),
    "metrica": ("posizione",),
}


def dichiarazioni(superficie: str) -> list:
    """Gli strumenti della superficie, nel formato che si aspetta Gemini."""
    nomi = PER_SUPERFICIE.get(superficie, ())
    decl = []
    for nome in nomi:
        s = STRUMENTI.get(nome)
        if s:
            decl.append({"name": nome, "description": s["description"],
                         "parameters": s["parameters"]})
    return decl


def esegui(nome: str, argomenti: dict) -> dict:
    """Esegue uno strumento. Un nome sconosciuto NON è un caso da gestire con
    fantasia: è un errore che torna al modello così com'è."""
    s = STRUMENTI.get(nome)
    if s is None:
        return {"errore": f"strumento '{nome}' inesistente"}
    try:
        return s["fn"](**(argomenti or {}))
    except TypeError as e:
        return {"errore": f"argomenti non validi: {e}"}
    except Exception as e:
        return {"errore": f"non riuscito: {type(e).__name__}"}
