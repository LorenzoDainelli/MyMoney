"""L'estrattore di fatti e il montaggio del prompt per superficie.

Due cose vanno difese, perché sono l'anima del nuovo agente:
1. i fatti nascono da un confronto con la STORIA dell'utente e hanno delle
   SOGLIE: sotto quelle non sono fatti, sono rumore;
2. se non c'è niente di notevole, il prompt deve dirlo esplicitamente al modello,
   invece di lasciarlo libero di inventare significato.
Nessuna rete: la chiamata al modello è finta.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import insights
import shared.ai as ai


# --------------------------- l'estrattore ---------------------------
def _riepiloghi(monkeypatch, per_mese: dict, movimenti=None):
    """Finge lo storico mensile: {(anno, mese): {entrate, uscite, categorie{}}}."""
    def finto(anno, mese):
        d = per_mese.get((anno, mese), {})
        spese = [{"nome": n, "tot": t} for n, t in sorted(
            d.get("categorie", {}).items(), key=lambda x: -x[1])]
        return {"entrate": d.get("entrate", 0.0), "uscite": d.get("uscite", 0.0),
                "saldo": d.get("entrate", 0.0) - d.get("uscite", 0.0),
                "spese_categoria": spese, "anno": anno, "mese": mese}

    import finance.service as fin
    monkeypatch.setattr(fin, "riepilogo_mese", finto)
    monkeypatch.setattr(fin, "lista_movimenti", lambda **k: movimenti or [])


def test_categoria_sopra_la_propria_mediana(monkeypatch):
    _riepiloghi(monkeypatch, {
        (2026, 7): {"uscite": 100.0, "categorie": {"Abbonamenti": 47.0}},
        (2026, 6): {"uscite": 90.0, "categorie": {"Abbonamenti": 22.0}},
        (2026, 5): {"uscite": 88.0, "categorie": {"Abbonamenti": 22.0}},
        (2026, 4): {"uscite": 92.0, "categorie": {"Abbonamenti": 20.0}},
    })
    f = insights.fatti_finanze(oggi=datetime(2026, 7, 20))
    sopra = [x for x in f if x.chiave == "cat:abbonamenti:sopra"]
    assert sopra, "lo scostamento oltre soglia deve produrre un fatto"
    assert "22,00 €" in sopra[0].testo and "47,00 €" in sopra[0].testo
    assert sopra[0].dati["mediana"] == 22.0


def test_sotto_soglia_non_e_un_fatto(monkeypatch):
    """Piccoli scostamenti e piccoli importi NON devono generare rumore."""
    _riepiloghi(monkeypatch, {
        (2026, 7): {"uscite": 100.0, "categorie": {"Bar": 24.0, "Briciole": 90.0}},
        (2026, 6): {"uscite": 100.0, "categorie": {"Bar": 22.0, "Briciole": 3.0}},
        (2026, 5): {"uscite": 100.0, "categorie": {"Bar": 21.0, "Briciole": 3.0}},
    })
    chiavi = [x.chiave for x in insights.fatti_finanze(oggi=datetime(2026, 7, 20))]
    assert "cat:bar:sopra" not in chiavi        # +9%: sotto MIN_SCOSTAMENTO
    assert "cat:briciole:sopra" in chiavi       # +2900% su 90 €: quello sì


def test_abitudine_sparita(monkeypatch):
    _riepiloghi(monkeypatch, {
        (2026, 7): {"uscite": 50.0, "categorie": {"Spesa": 50.0}},
        (2026, 6): {"uscite": 80.0, "categorie": {"Spesa": 50.0, "Palestra": 30.0}},
        (2026, 5): {"uscite": 80.0, "categorie": {"Spesa": 50.0, "Palestra": 30.0}},
        (2026, 4): {"uscite": 80.0, "categorie": {"Spesa": 50.0, "Palestra": 30.0}},
    })
    f = {x.chiave: x for x in insights.fatti_finanze(oggi=datetime(2026, 7, 20))}
    assert "cat:palestra:sparita" in f
    assert "3" in f["cat:palestra:sparita"].testo      # presente in 3 mesi


def test_niente_storia_niente_fatti(monkeypatch):
    """Con un mese solo di dati non esiste ancora un'abitudine: nessun confronto."""
    _riepiloghi(monkeypatch, {(2026, 7): {"uscite": 500.0, "categorie": {"Viaggi": 500.0}}})
    chiavi = [x.chiave for x in insights.fatti_finanze(oggi=datetime(2026, 7, 20))]
    assert not any(c.startswith("cat:viaggi:sopra") for c in chiavi)


def test_numeri_all_italiana():
    assert insights._eur(1234.5) == "1.234,50 €"
    assert insights._pct(36.9, 1) == "36,9%"
    assert insights._pct(4.2, 1, segno=True) == "+4,2%"


def test_ordinati_per_forza(monkeypatch):
    _riepiloghi(monkeypatch, {
        (2026, 7): {"uscite": 600.0, "categorie": {"Viaggi": 500.0, "Libri": 30.0}},
        (2026, 6): {"uscite": 120.0, "categorie": {"Viaggi": 100.0, "Libri": 20.0}},
        (2026, 5): {"uscite": 120.0, "categorie": {"Viaggi": 100.0, "Libri": 20.0}},
    })
    f = insights.fatti_finanze(oggi=datetime(2026, 7, 20))
    f.sort(key=lambda x: x.forza, reverse=True)
    assert f[0].dati.get("categoria") == "Viaggi"   # 500 € batte 30 €


# --------------------------- il prompt ---------------------------
def _cattura_prompt(monkeypatch):
    visto = {}

    def finto_call(prompt, system=ai.SYSTEM_PROMPT, timeout=20, _model=None, **kw):
        visto["prompt"] = prompt
        visto["system"] = system
        visto["strumenti"] = kw.get("strumenti")
        return "Testo di prova.\nConfidenza: media"

    monkeypatch.setattr(ai, "_call", finto_call)
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    return visto


def test_i_fatti_finiscono_nel_prompt(monkeypatch):
    visto = _cattura_prompt(monkeypatch)
    fatti = [insights.Fatto(chiave="k", testo="Gli abbonamenti sono raddoppiati.",
                            forza=80.0)]
    res = ai._genera("dashboard", fatti=fatti)
    assert res["ok"] and res["conf"] == "media"
    assert "Gli abbonamenti sono raddoppiati." in visto["prompt"]
    assert "NON ricalcolare" in visto["prompt"]


def test_senza_fatti_niente_novita_inventate(monkeypatch):
    """Zero fatti non vuol dire zero testo: si può descrivere il presente. Ma
    NON si può spacciarlo per una novità."""
    visto = _cattura_prompt(monkeypatch)
    ai._genera("dashboard", fatti=[])
    p = visto["prompt"]
    assert "NESSUN FATTO NOTEVOLE" in p
    assert "non c'è NIENTE DI NUOVO da segnalare" in p
    assert "spacciarlo per una novità no" in p


def test_il_quadro_da_sostanza_senza_inventare(monkeypatch):
    visto = _cattura_prompt(monkeypatch)
    monkeypatch.setattr(insights, "quadro",
                        lambda oggi=None: ["Portafoglio: 37 titoli.",
                                           "Liquidità sui conti: 63,78 €."])
    ai._genera("dashboard", fatti=[])
    p = visto["prompt"]
    assert "Portafoglio: 37 titoli." in p
    assert "Liquidità sui conti: 63,78 €." in p
    # è sfondo, non notizia: non ci si apre il discorso
    assert "NON aprire il discorso da qui" in p


def test_il_quadro_non_va_dove_non_c_entra(monkeypatch):
    """Sulla scheda di un titolo si parla dello strumento, non del suo bilancio."""
    visto = _cattura_prompt(monkeypatch)
    monkeypatch.setattr(insights, "quadro",
                        lambda oggi=None: ["Liquidità sui conti: 63,78 €."])
    ai._genera("titolo", contesto="ETF globale")
    assert "63,78" not in visto["prompt"]


def test_ogni_superficie_ha_il_suo_registro(monkeypatch):
    visto = _cattura_prompt(monkeypatch)
    ai._genera("titolo")
    titolo = visto["prompt"]
    ai._genera("metrica")
    metrica = visto["prompt"]
    assert "divulgatore" in titolo and "traduttore" in metrica
    assert titolo != metrica


def test_le_regole_anti_piattume_sono_nel_system(monkeypatch):
    visto = _cattura_prompt(monkeypatch)
    ai._genera("dashboard")
    sistema = visto["system"]
    assert "APRI DAL FATTO PIÙ FORTE" in sistema
    assert "NON INVENTARE MAI NUMERI" in sistema
    assert "MAI segnali operativi" in sistema        # la regola di sempre resta


def test_senza_chiave_non_chiama_il_modello(monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: False)
    assert ai._genera("dashboard")["error"] == "no_key"


# --------------------------- quanta storia esiste ---------------------------
def _orizzonte_finto(monkeypatch, inizio, movimenti_dal=None):
    import finance.service as fin
    monkeypatch.setattr(fin, "data_inizio", lambda: inizio)
    import portfolio.versamenti as v
    monkeypatch.setattr(v, "lista", lambda: [])


def test_app_appena_nata_niente_confronti(monkeypatch):
    """Il caso vero: app dal 4/7/2026, oggi 24/7/2026. Venti giorni."""
    _orizzonte_finto(monkeypatch, datetime(2026, 7, 4))
    oz = insights.orizzonte(oggi=datetime(2026, 7, 24))
    assert oz["giorni"] == 20
    assert oz["mesi_completi"] == 0            # nemmeno un mese passato completo

    testo = insights.come_testo_orizzonte(oz)
    assert "20 giorni" in testo
    assert "NON esiste nessun mese passato completo" in testo
    assert "«di solito»" in testo


def test_con_qualche_mese_alle_spalle(monkeypatch):
    _orizzonte_finto(monkeypatch, datetime(2026, 1, 10))
    oz = insights.orizzonte(oggi=datetime(2026, 7, 24))
    assert oz["mesi_completi"] == 5            # feb, mar, apr, mag, giu
    assert "mesi passati completi disponibili: 5" in insights.come_testo_orizzonte(oz)


def test_mercato_e_possesso_restano_distinti(monkeypatch):
    _orizzonte_finto(monkeypatch, datetime(2026, 7, 4))
    testo = insights.come_testo_orizzonte(insights.orizzonte(oggi=datetime(2026, 7, 24)))
    assert "storia DI MERCATO" in testo
    assert "non il suo guadagno" in testo


def test_l_orizzonte_e_in_cima_ai_prompt_sui_dati_dell_utente(monkeypatch):
    """Dove si parla dei SOLI dati dell'utente (dashboard, finanze, titolo) il
    vincolo è quanta storia esiste nell'app."""
    visto = _cattura_prompt(monkeypatch)
    monkeypatch.setattr(insights, "come_testo_orizzonte",
                        lambda oz=None: "QUANTA STORIA ESISTE: 20 giorni.\n")
    for superficie in ("dashboard", "finanze", "titolo"):
        ai._genera(superficie)
        assert "QUANTA STORIA ESISTE: 20 giorni." in visto["prompt"], superficie


def test_sulle_metriche_l_orizzonte_e_rovesciato(monkeypatch):
    """Sulle metriche il vincolo è OPPOSTO: molte misurano la storia di mercato
    (lunga anni), e bollarle come «basate su pochi giorni di dati dell'app» era
    proprio l'errore da togliere. Quindi lì NON entra l'orizzonte generico, ma una
    nota che distingue mercato e possesso."""
    visto = _cattura_prompt(monkeypatch)
    monkeypatch.setattr(insights, "come_testo_orizzonte",
                        lambda oz=None: "QUANTA STORIA ESISTE: 20 giorni.\n")
    ai._genera("metrica")
    assert "QUANTA STORIA ESISTE: 20 giorni." not in visto["prompt"]
    assert "storia di MERCATO" in visto["prompt"]
    assert "non è il suo guadagno" in visto["prompt"]


def test_la_regola_sui_periodi_e_nel_system(monkeypatch):
    visto = _cattura_prompt(monkeypatch)
    ai._genera("dashboard")
    s = visto["system"]
    assert "NON PARLARE MAI DI PERIODI PIÙ LUNGHI DELLA STORIA CHE HAI" in s
    assert "non è il suo guadagno" in s


# ------------- il contributo si misura su cio' che l'utente ha vissuto -------------
class _Pos:
    def __init__(self, ticker, versato):
        self.ticker, self.versato_totale = ticker, versato
        self.nome_vista, self.tipo, self.is_fisso = ticker, "Azione", False
        self.id, self.pct_target = 1, 10.0


def _vista(monkeypatch, righe, totale):
    import portfolio.service as pf, portfolio.analytics as an, portfolio.market as mk
    monkeypatch.setattr(pf, "vista_portafoglio", lambda: {
        "righe": righe, "totale": totale, "ha_totale": totale > 0,
        "n_prezzi": len(righe), "n_ticker": len(righe), "ultimo_agg": ""})
    monkeypatch.setattr(an, "look_through", lambda **k: {"settori": [], "n_titoli": len(righe)})
    # se il rendimento a 12 mesi venisse ancora usato, questo lo farebbe esplodere
    monkeypatch.setattr(mk, "get_perf_snapshot", lambda: {"SNDK": 3492.0, "IWDA": 8.0})


def test_il_contributo_non_usa_il_rendimento_a_12_mesi(monkeypatch):
    """SNDK ha fatto +3492% sul mercato, ma l'utente ci ha messo 2 € e ne valgono
    2,01: il suo contributo e' di un centesimo, non del 60%."""
    righe = [
        {"p": _Pos("SNDK", 2.0), "valore": 2.01},
        {"p": _Pos("IWDA", 98.0), "valore": 97.68},
    ]
    _vista(monkeypatch, righe, 99.69)
    fatti = insights.fatti_portafoglio()
    motori = [f for f in fatti if ":contributo" in f.chiave]
    # il titolo che pesa sul risultato e' IWDA (-0,32 €), non SNDK (+0,01 €)
    assert not any("SNDK" in f.testo for f in motori)
    if motori:
        assert "IWDA" in motori[0].testo


def test_niente_contributo_senza_versato(monkeypatch):
    """Se non si sa quanto e' stato versato, non si puo' dire cosa ha reso."""
    righe = [{"p": _Pos("SNDK", 0.0), "valore": 500.0}]
    _vista(monkeypatch, righe, 500.0)
    assert not [f for f in insights.fatti_portafoglio() if ":contributo" in f.chiave]


def test_gli_spiccioli_non_sono_un_fatto(monkeypatch):
    """PAC appena partito: scostamenti da pochi centesimi non meritano una frase."""
    righe = [{"p": _Pos("SNDK", 50.0), "valore": 50.02},
             {"p": _Pos("IWDA", 50.0), "valore": 49.99}]
    _vista(monkeypatch, righe, 100.01)
    assert not [f for f in insights.fatti_portafoglio() if ":contributo" in f.chiave]


def test_il_contributo_dice_versato_e_valore(monkeypatch):
    righe = [{"p": _Pos("IWDA", 100.0), "valore": 88.0},
             {"p": _Pos("SNDK", 100.0), "valore": 100.5}]
    _vista(monkeypatch, righe, 188.5)
    f = [x for x in insights.fatti_portafoglio() if ":contributo" in x.chiave][0]
    assert "IWDA" in f.testo and "100,00 €" in f.testo and "88,00 €" in f.testo
    assert "TUO" in f.testo


def test_niente_numeri_di_mercato_come_notizia(monkeypatch):
    visto = _cattura_prompt(monkeypatch)
    ai._genera("dashboard")
    assert "non è mai una notizia" in visto["system"]
    assert "3445% a 3492%" in visto["system"]


def test_niente_mesi_in_cui_l_app_non_esisteva(monkeypatch):
    """Un mese prima dell'inizio del tracking NON e' "un mese senza spese":
    passarlo all'agente invita a confronti falsi."""
    import finance.service as fin
    import finance.routes as fr
    monkeypatch.setattr(fin, "data_inizio", lambda: datetime(2026, 7, 4))
    monkeypatch.setattr(fin, "saldi", lambda: {"totale": 64.0, "liquido": 64.0,
                                               "righe": [1] * 7})
    _riepiloghi(monkeypatch, {(2026, 7): {"uscite": 35.0, "categorie": {"Spesa": 15.0}}})
    monkeypatch.setattr(fr, "datetime", __import__("datetime").datetime)

    testo = fr._contesto_finanze()
    assert "2026-05" not in testo and "2026-06" not in testo
    assert "2026-07" in testo


# --------------------------- l'archivio giornaliero ---------------------------
# Fino a ieri l'unico passato disponibile erano i mesi civili completi, che
# all'inizio della vita dell'app non esistono: ecco perché l'agente aveva così
# poco da dire. Con l'archivio (shared/storico.py) si può finalmente confrontare
# l'utente con sé stesso dopo pochi giorni — ma solo dove i dati ci sono davvero.

def _finto_confronto(monkeypatch, dati):
    from shared import storico
    monkeypatch.setattr(storico, "confronto", lambda giorni=7: dati)


def test_il_mercato_e_i_tuoi_versamenti_restano_due_cose(monkeypatch):
    """«Il portafoglio è salito di 110 €» non dice niente se 100 li hai messi tu."""
    _finto_confronto(monkeypatch, {
        "giorni": 8, "da": date(2026, 7, 19), "a": date(2026, 7, 27),
        "patrimonio": 120.0, "investito": 110.0, "liquido": 0.0,
        "versato": 100.0, "mercato": 10.0, "risultato_eur": 10.0})
    fatti = insights.fatti_storico()
    testo = " ".join(f.testo for f in fatti)
    assert "10,00 €" in testo and "100,00 €" in testo
    assert "8 giorni" in testo
    assert any(f.chiave == "storico:mercato" for f in fatti)


def test_sotto_un_euro_non_c_e_niente_da_spiegare(monkeypatch):
    """Un PAC appena partito muove centesimi: non sono fatti, sono rumore."""
    _finto_confronto(monkeypatch, {
        "giorni": 7, "da": date(2026, 7, 20), "a": date(2026, 7, 27),
        "patrimonio": 0.4, "investito": 0.4, "liquido": 0.0,
        "versato": 0.0, "mercato": 0.4, "risultato_eur": 0.4})
    assert insights.fatti_storico() == []


def test_due_giorni_non_sono_un_confronto(monkeypatch):
    _finto_confronto(monkeypatch, {
        "giorni": 1, "da": date(2026, 7, 26), "a": date(2026, 7, 27),
        "patrimonio": 50.0, "investito": 50.0, "liquido": 0.0,
        "versato": 0.0, "mercato": 50.0, "risultato_eur": 50.0})
    assert insights.fatti_storico() == []


def test_archivio_vuoto_nessun_fatto(monkeypatch):
    _finto_confronto(monkeypatch, None)
    assert insights.fatti_storico() == []


def test_l_orizzonte_dichiara_quante_giornate_ha_in_archivio(monkeypatch):
    """L'agente deve sapere entro quanti giorni può spingersi a confrontare."""
    testo = insights.come_testo_orizzonte({
        "inizio": datetime(2026, 7, 4), "giorni": 23, "mesi_completi": 0,
        "giorni_pac": 11, "giorni_archivio": 9})
    assert "giornate registrate in archivio: 9" in testo
