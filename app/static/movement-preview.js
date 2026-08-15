/* MyMoney — «che cosa cambia»: l'effetto del movimento PRIMA di salvarlo.

   Il modulo si compilava alla cieca: sceglievi un portafoglio, salvavi, e se
   era quello sbagliato lo scoprivi giorni dopo scorrendo la tabella. Qui il
   saldo del conto e il totale del mese si aggiornano mentre scrivi.

   Nessun calcolo nuovo: i saldi e le uscite per categoria arrivano già
   calcolati dal server (finance/routes.py), il JS fa solo la somma dell'unico
   movimento in corso. */
/* Senza argomenti: aggancia OGNI modulo presente. Sulla pagina Finanze ce ne
   sono due — quello della pagina e quello che il «＋» cala nel pannello — e
   hanno gli stessi id, perche' sono lo stesso template incluso due volte.
   `getElementById` risponde sempre col primo: l'anteprima del pannello leggeva
   l'importo della pagina dietro, e i campi della carta che arrotonda nel
   pannello non comparivano mai. Da qui in giu' non si cerca piu' nel
   documento, si cerca dentro il proprio modulo. */
function mmCollegaModulo(form) {
  if (!form) {
    Array.prototype.forEach.call(document.querySelectorAll('[id="mov-form"]'),
      function (f) { mmCollegaModulo(f); });
    return;
  }
  if (form.dataset.collegato === '1') return;
  var scope = form.closest('.tel-modulo, .mm-add') || document;
  function q(id) {
    return scope.querySelector('[id="' + id + '"]') ||
           document.querySelector('[id="' + id + '"]');
  }
  var host = q('mov-effetto');
  // i due <script> coi dati sono identici in tutte le copie: basta il primo
  var elD = document.querySelector('[id="mov-effetto-dati"]');
  var elT = document.querySelector('[id="mov-effetto-testi"]');
  if (!host || !elD || !elT) return;
  form.dataset.collegato = '1';
  var D, T;
  try { D = JSON.parse(elD.textContent); T = JSON.parse(elT.textContent); } catch (e) { return; }

  // Stessa formattazione di shared/formatting.format_eur: sulla stessa pagina
  // due modi di scrivere 1.816,89 si notano subito.
  function eur(n) {
    var neg = Number(n) < 0;
    var p = Math.abs(Number(n)).toFixed(2).split('.');
    return '€ ' + (neg ? '-' : '') + p[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.') + ',' + p[1];
  }
  // Il saveback con i decimali che ha davvero (shared/formatting.decimali_utili):
  // "0,4045" resta intero, "0,1300" torna "0,13". Senza € davanti: va in un campo.
  function numSaveback(n) {
    var p = Math.abs(Number(n)).toFixed(4).split('.');
    var dec = p[1].replace(/0+$/, '');
    while (dec.length < 2) dec += '0';
    return p[0] + ',' + dec;
  }
  // "1.234,56" / "1234.56" / "20" → numero. Stessa tolleranza del server.
  function num(s) {
    s = String(s || '').replace(/[^\d.,-]/g, '').trim();
    if (!s) return 0;
    if (s.indexOf(',') >= 0) s = s.replace(/\./g, '').replace(',', '.');
    var v = parseFloat(s);
    return isFinite(v) ? v : 0;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function fill(tpl, vals) {
    return String(tpl).replace(/\{(\w+)\}/g, function (_, k) { return vals[k] != null ? vals[k] : ''; });
  }

  function val(name) { var el = form.querySelector('[name="' + name + '"]'); return el ? el.value : ''; }

  // --- carta che arrotonda ------------------------------------------------
  // Stesse regole del server (finance/service.py: arrotondamento, saveback_dovuto),
  // verificate sulla carta: arrotondamento il 30/07/2026, saveback all'1% esatto
  // (non troncato) l'08/08/2026. Qui è solo l'anteprima: al salvataggio il server
  // rifà i conti, e se scrivi un importo a mano vince il tuo.
  // dentro il PROPRIO modulo: questi campi stanno nel form
  var boxCarta = form.querySelector('[id="mov-carta"]');
  var inArr = form.querySelector('[id="mov-arr"]'), onArr = form.querySelector('[id="mov-arr-on"]');
  var inSav = form.querySelector('[id="mov-sav"]'), onSav = form.querySelector('[id="mov-sav-on"]');
  // «manuale» = quell'importo l'hai scritto tu, quindi il calcolo non lo tocca.
  // In modifica arriva già segnato dal server (data-mio), così riaprire un
  // movimento corretto a mano non te lo fa perdere alla prima lettera digitata.
  var manuale = {
    arr: !!(inArr && inArr.dataset.mio),
    sav: !!(inSav && inSav.dataset.mio)
  };
  // In modifica il saveback di QUESTO movimento è già dentro il totale del mese:
  // se non lo togliessi, il tetto risulterebbe più pieno di quanto è e la
  // proposta scenderebbe a ogni riapertura della stessa spesa.
  var savGia = Math.max(0, ((D.sav_gia || 0) - (inSav && inSav.value ? num(inSav.value) : 0)));

  function alProssimoEuro(imp) {
    if (!imp || imp <= 0) return 0;
    return Math.round((Math.floor(imp) + 1 - imp) * 100) / 100;   // 8,00 -> 1,00
  }
  // l'1% ESATTO, non troncato ai centesimi: su 40,45 € sono 0,4045 €
  function saveback(imp, pct, tetto, gia) {
    if (!imp || imp <= 0 || !pct) return 0;
    var v = Math.round(imp * pct * 100) / 10000;                  // 40,45 × 1% -> 0,4045
    if (tetto > 0) v = Math.min(v, Math.max(0, Math.round((tetto - gia) * 10000) / 10000));
    return Math.max(0, v);
  }
  function carta() {
    var tipo = (form.querySelector('#mov-tipo') || {}).value || 'uscita';
    var w = D.wallets[String(val('wallet_id'))];
    // solo le USCITE: un trasferimento (il PAC parte da questa carta) non è un pagamento
    if (!boxCarta || tipo !== 'uscita' || !w || !w.carta) return null;
    return { w: w, r: w.carta };
  }
  function aggiornaCarta() {
    if (!boxCarta) return null;
    var c = carta();
    if (!c) { boxCarta.style.display = 'none'; if (inArr) inArr.value = ''; if (inSav) inSav.value = ''; return null; }
    var imp = num(val('importo'));
    var arr = c.r.arr && onArr.checked ? alProssimoEuro(imp) : 0;
    var sav = onSav.checked ? saveback(imp, c.r.pct, c.r.tetto, savGia) : 0;
    if (!manuale.arr || !onArr.checked) inArr.value = imp ? eur(arr).replace('€ ', '') : '';
    if (!manuale.sav || !onSav.checked) inSav.value = imp ? numSaveback(sav) : '';
    inArr.disabled = !onArr.checked;
    inSav.disabled = !onSav.checked;
    var a = onArr.checked ? num(inArr.value) : 0, s = onSav.checked ? num(inSav.value) : 0;
    boxCarta.style.display = '';
    form.querySelector('[id="mov-carta-tit"]').textContent =
      imp ? fill(T.ctit, { w: c.w.nome, tot: eur(imp + a) }) : '';
    form.querySelector('[id="mov-sav-lbl"]').textContent = fill(T.csav, { pct: c.r.pct });
    var pieno = c.r.tetto > 0 && savGia >= c.r.tetto;
    form.querySelector('[id="mov-carta-nota"]').textContent =
      pieno ? T.ctetto : fill(T.cnota, { w: D.salvadanaio });
    return { arr: a, sav: s, nome: D.salvadanaio, conto: c.w };
  }

  function nota(testo, tono) {
    var col = tono === 'neg' ? 'var(--neg)' : 'var(--muted)';
    var bg = tono === 'neg' ? 'var(--neg-bg)' : 'var(--surface-alt)';
    var bd = tono === 'neg' ? 'var(--neg-bd)' : 'var(--border)';
    return '<div style="margin-top:10px;background:' + bg + ';border:1px solid ' + bd +
      ';border-radius:var(--r-sm);padding:8px 10px;font-size:12px;line-height:var(--lh-relaxed);color:' + col + ';">' +
      esc(testo) + '</div>';
  }

  function conto(w, prima, dopo) {
    var s = '<div class="muted" style="font-size:12px;">' + esc(w.nome) + '</div>';
    if (w.derivato) {
      return s + '<div class="num" style="font-size:17px;font-weight:700;color:var(--ink);margin-top:2px;">' +
        eur(prima) + '</div>' + nota(T.derived);
    }
    return s + '<div class="mm-eff-row num" style="margin-top:2px;">' +
      '<span class="mm-eff-prima" style="font-size:14px;">' + eur(prima) + '</span>' +
      '<span class="muted" style="font-size:12px;">→</span>' +
      '<span style="font-size:17px;font-weight:700;color:' + (dopo < 0 ? 'var(--neg)' : 'var(--ink)') + ';">' + eur(dopo) + '</span>' +
      '</div>';
  }

  function mese(etichetta, prima, dopo, colore) {
    return '<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);">' +
      '<div class="muted" style="font-size:12px;">' + esc(etichetta) + '</div>' +
      '<div class="mm-eff-row num" style="margin-top:2px;">' +
      '<span class="mm-eff-prima" style="font-size:13px;">' + eur(prima) + '</span>' +
      '<span class="muted" style="font-size:12px;">→</span>' +
      '<span style="font-size:15px;font-weight:700;color:' + colore + ';">' + eur(dopo) + '</span>' +
      '</div></div>';
  }

  function contesto() {
    var c = (val('categoria') || '').trim();
    if (!c) return '';
    var r = D.cat[c.toLowerCase()];
    if (!r) return nota(fill(T.catnew, { cat: c }));
    return nota(fill(r.n === 1 ? T.cat1 : T.cat, { cat: c, n: r.n, tot: eur(r.tot) }));
  }

  // --- le stesse due righe sulle SPESE di una partita di giro ---------------
  // Una spesa da farsi rimborsare resta una spesa fatta con la carta: la banca
  // arrotonda lo stesso, e quei soldi restano nel salvadanaio anche quando il
  // rimborso arriva. Il rimborso riguarda la spesa, non l'arrotondamento.
  function giroCarte() {
    var righe = form.querySelectorAll('.giro-spesa-row');
    var consumato = 0;          // il tetto del saveback si consuma fra le gambe
    for (var i = 0; i < righe.length; i++) {
      var row = righe[i];
      var box = row.querySelector('.giro-carta');
      if (!box) continue;
      var w = D.wallets[String((row.querySelector('[name="spesa_wallet"]') || {}).value)];
      var inA = row.querySelector('.giro-arr'), inS = row.querySelector('.giro-sav');
      var onA = row.querySelector('.giro-arr-on'), onS = row.querySelector('.giro-sav-on');
      if (!w || !w.carta) {
        // niente regole: campi vuoti, ma NON rimossi — le liste del modulo sono
        // parallele e un campo mancante sfaserebbe tutte le righe
        box.style.display = 'none';
        inA.value = ''; inS.value = '';
        continue;
      }
      var imp = num((row.querySelector('[name="spesa_importo"]') || {}).value);
      var arr = w.carta.arr && onA.checked ? alProssimoEuro(imp) : 0;
      var sav = onS.checked ? saveback(imp, w.carta.pct, w.carta.tetto,
                                       (D.sav_gia || 0) + consumato) : 0;
      if (!inA.dataset.mio || !onA.checked) inA.value = imp ? eur(arr).replace('€ ', '') : '';
      if (!inS.dataset.mio || !onS.checked) inS.value = imp ? numSaveback(sav) : '';
      inA.disabled = !onA.checked;
      inS.disabled = !onS.checked;
      consumato += onS.checked ? num(inS.value) : 0;
      box.style.display = '';
      row.querySelector('.giro-sav-lbl').textContent = fill(T.csav, { pct: w.carta.pct });
      row.querySelector('.giro-carta-tit').textContent =
        imp ? fill(T.ctit, { w: w.nome, tot: eur(imp + (onA.checked ? num(inA.value) : 0)) }) : '';
    }
  }

  function vuoto() {
    host.innerHTML = '<p class="faint" style="margin:0;font-size:13px;line-height:var(--lh-relaxed);">' +
      esc(T.empty) + '</p>';
  }

  function render() {
    var tipo = (form.querySelector('#mov-tipo') || {}).value || 'uscita';
    if (tipo === 'giro') {
      aggiornaCarta(); giroCarte();
      host.innerHTML = nota(T.giro).replace('margin-top:10px;', '');
      return;
    }

    var extra = aggiornaCarta();
    var imp = num(val('importo'));
    var w = D.wallets[String(val('wallet_id'))];
    if (!imp || !w) { vuoto(); return; }

    // con la carta che arrotonda dal conto esce la spesa PIÙ l'arrotondamento,
    // ma di spesa ne hai fatta solo la prima: due numeri diversi, tutti e due veri
    if (extra && (extra.arr || extra.sav)) {
      var s2 = conto(w, w.saldo, w.saldo - imp - extra.arr);
      s2 += mese(T.out, D.mese.uscite, D.mese.uscite + imp, 'var(--neg)');
      if (extra.sav) s2 += mese(T['in'], D.mese.entrate, D.mese.entrate + extra.sav, 'var(--pos)');
      s2 += '<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);">' +
        '<div class="muted" style="font-size:12px;">' + esc(extra.nome) + '</div>' +
        '<div class="mm-eff-row num" style="margin-top:2px;">' +
        '<span style="font-size:15px;font-weight:700;color:var(--ink);">+ ' +
        eur(extra.arr + extra.sav) + '</span></div></div>';
      s2 += contesto();
      if (!w.derivato && w.saldo - imp - extra.arr < 0) s2 += nota(T.neg, 'neg');
      host.innerHTML = s2;
      return;
    }

    var s = '';
    if (tipo === 'entrata') {
      s += conto(w, w.saldo, w.saldo + imp);
      s += mese(T['in'], D.mese.entrate, D.mese.entrate + imp, 'var(--pos)');
    } else if (tipo === 'trasferimento') {
      var w2 = D.wallets[String(val('wallet_to_id'))];
      s += conto(w, w.saldo, w.saldo - imp);
      if (w2) s += '<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);">' +
        conto(w2, w2.saldo, w2.saldo + imp) + '</div>';
      s += nota(T.transfer);
      if (!w.derivato && w.saldo - imp < 0) s += nota(T.neg, 'neg');
      host.innerHTML = s;
      return;
    } else {
      s += conto(w, w.saldo, w.saldo - imp);
      s += mese(T.out, D.mese.uscite, D.mese.uscite + imp, 'var(--neg)');
      s += contesto();
    }
    if (!w.derivato && tipo !== 'entrata' && w.saldo - imp < 0) s += nota(T.neg, 'neg');
    host.innerHTML = s;
  }

  // Un importo scritto a mano non va più riscritto dal calcolo: è una decisione
  // tua, e disfarla mentre continui a digitare sarebbe peggio che non proporre
  // niente. Torna automatico se spegni e riaccendi l'interruttore.
  if (inArr) inArr.addEventListener('input', function () { manuale.arr = true; });
  if (inSav) inSav.addEventListener('input', function () { manuale.sav = true; });
  if (onArr) onArr.addEventListener('change', function () { manuale.arr = false; });
  if (onSav) onSav.addEventListener('change', function () { manuale.sav = false; });
  // Stessa cosa sulle righe della partita di giro, ma in delega: quelle righe
  // nascono e spariscono mentre compili, quindi non si possono agganciare una
  // per una all'avvio.
  form.addEventListener('input', function (e) {
    var t = e.target;
    if (t.classList && (t.classList.contains('giro-arr') || t.classList.contains('giro-sav'))) {
      t.dataset.mio = '1';
    }
  });
  form.addEventListener('change', function (e) {
    var t = e.target;
    if (!t.classList) return;
    if (t.classList.contains('giro-arr-on')) {
      var a = t.closest('.giro-spesa-row').querySelector('.giro-arr'); a.dataset.mio = '';
    } else if (t.classList.contains('giro-sav-on')) {
      var s = t.closest('.giro-spesa-row').querySelector('.giro-sav'); s.dataset.mio = '';
    }
  });

  form.addEventListener('input', render);
  form.addEventListener('change', render);
  render();
}

/* Il modulo non nasce piu' solo dentro la pagina Finanze: il «+» della barra
   del telefono lo cala in un pannello, con la sua fetch, DOPO che questo file
   e' gia' stato letto. Un IIFE avrebbe girato a vuoto una volta sola e trovato
   la pagina senza modulo, quindi ora e' una funzione con un nome: la chiama
   app.js quando il pannello e' arrivato. Chiamarla dove il modulo non c'e' non
   costa niente — esce subito alla prima riga. */
window.mmCollegaModulo = mmCollegaModulo;
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mmCollegaModulo);
} else {
  mmCollegaModulo();
}
