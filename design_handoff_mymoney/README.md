# Design freeze v1.0 — la copia di riferimento

In questa cartella c'è **solo `styles/`**, e non è un doppione per sbaglio: è la
copia di riferimento del design system, tenuta uguale a quella che l'app serve
davvero da `app/static/`.

Serve a una cosa precisa. `styles.css`, `mymoney.css` e i `tokens/` si
**sostituiscono in blocco** quando il design cambia: qualunque regola scritta
dentro di loro se ne va con loro, senza un errore e senza un test rosso. Era già
successo — il 23/07/2026 quarantuno righe per la leggibilità del testo
dell'agente erano finite dentro `mymoney.css` e sono sparite così.

Il guardiano è `app/tests/test_telefono.py::test_il_freeze_e_identico_all_handoff`:
confronta byte per byte gli undici fogli qui dentro con quelli in `app/static/`.
Se qualcuno scrive nel posto sbagliato, il test diventa rosso.

**Quindi:** le regole nuove vanno in `app/static/aggiunte.css`, che è nostro e non
verrà sostituito. Qui dentro non si scrive niente, mai.

⚠️ Attenzione se un giorno pensi di cancellare questa cartella: il test **salta**
invece di fallire quando i file non ci sono (`pytest.skip`). Sparirebbe il
guardiano senza che niente diventi rosso.

---

Il resto del pacchetto di handoff originale — i sorgenti React di riferimento
(`design_reference/`) e il prompt per applicare il design — è stato rimosso il
18/08/2026: quel lavoro è finito, il design vive nei template veri. Se serve
ritrovarlo, sta nella storia di git prima di quella data.

La fonte autorevole del freeze resta il pacchetto consegnato dal designer, fuori
da questo repo.
