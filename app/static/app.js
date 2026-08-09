// JS dell'app — interazioni del design freeze v1.0.

/* 0) Attrezzi del telefono, condivisi dai due pannelli che salgono dal fondo:
      il dettaglio di una posizione (§5) e il pannello «Altro» (in fondo al file).
      Sono dichiarazioni di funzione, quindi valgono in tutto il file a
      prescindere da dove stanno scritte.

      `mmTrascina` è il gesto che più di ogni altro distingue un pannello di
      un'app da un riquadro di un sito: si tira giù e se ne va, senza cercare
      la ✕. Trenta righe, nessuna libreria. */
function mmTelefono() { return window.matchMedia('(max-width: 760px)').matches; }

// `foglio` è il pannello, `chiudi()` lo chiude davvero, `riposo` è la trasforma
// che ha da aperto (stringa vuota se gliela dà una classe CSS).
function mmTrascina(foglio, chiudi, riposo) {
  var y0 = null, dy = 0, transizione = '';
  riposo = riposo || '';

  foglio.addEventListener('touchstart', function (e) {
    // Solo se il contenuto è già in cima: altrimenti il dito che riporta su una
    // lista lunga chiuderebbe il pannello ogni volta.
    if (e.touches.length !== 1 || foglio.scrollTop > 0) return;
    y0 = e.touches[0].clientY;
    dy = 0;
    transizione = foglio.style.transition;
    foglio.style.transition = 'none';       // mentre trascini comanda il dito
  }, { passive: true });

  foglio.addEventListener('touchmove', function (e) {
    if (y0 === null) return;
    var d = e.touches[0].clientY - y0;
    if (d <= 0) { dy = 0; foglio.style.transform = riposo; return; }   // su, no
    dy = d;
    // Non passivo apposta: senza questo iOS fa rimbalzare il pannello mentre lo
    // tiri, e il gesto sembra rotto.
    e.preventDefault();
    foglio.style.transform = 'translateY(' + d + 'px)';
  }, { passive: false });

  function fine() {
    if (y0 === null) return;
    var quanto = dy;
    y0 = null; dy = 0;
    foglio.style.transition = transizione;
    foglio.style.transform = riposo;
    // Un quarto dell'altezza, al massimo 90 punti: sotto è un tocco storto
    // mentre si scorre, sopra è la volontà di chiudere.
    if (quanto > Math.min(90, foglio.offsetHeight * 0.25)) chiudi();
  }
  foglio.addEventListener('touchend', fine, { passive: true });
  foglio.addEventListener('touchcancel', fine, { passive: true });
}

// 1) Conferma di eliminazione INLINE (come nel prototipo: Conferma/Annulla al
//    posto del bottone, reversibile). Testi tradotti via data-confirm/data-cancel;
//    se mancano si torna al window.confirm classico (data-msg).
document.addEventListener('submit', function (e) {
  var f = e.target;
  if (!(f.classList && f.classList.contains('js-confirm'))) return;
  if (f.dataset.confirmed === '1') return;
  if (!f.dataset.confirm) {                       // fallback: dialog nativo
    if (!window.confirm(f.dataset.msg || 'OK?')) e.preventDefault();
    return;
  }
  e.preventDefault();
  if (f.dataset.armed === '1') return;
  f.dataset.armed = '1';
  var btn = f.querySelector('[type=submit]');
  if (btn) btn.style.display = 'none';
  var wrap = document.createElement('span');
  wrap.style.cssText = 'display:inline-flex;gap:6px;white-space:nowrap;';
  var ok = document.createElement('button');
  ok.type = 'button'; ok.className = 'btn sm danger'; ok.textContent = f.dataset.confirm;
  var no = document.createElement('button');
  no.type = 'button'; no.className = 'btn sm ghost'; no.textContent = f.dataset.cancel || '✕';
  ok.addEventListener('click', function () { f.dataset.confirmed = '1'; f.submit(); });
  no.addEventListener('click', function () { wrap.remove(); if (btn) btn.style.display = ''; f.dataset.armed = ''; });
  wrap.appendChild(ok); wrap.appendChild(no);
  f.appendChild(wrap);
});

// 2) Count-up dell'hero (dashboard): [data-countup] con il valore finale.
//    Con animazioni spente (o reduced-motion) il numero appare subito.
(function () {
  var els = document.querySelectorAll('[data-countup]');
  if (!els.length) return;
  var lang = document.documentElement.lang || 'it';
  var still = document.documentElement.dataset.anim === 'spente' ||
    (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  els.forEach(function (el) {
    var end = parseFloat(el.dataset.countup);
    if (isNaN(end)) return;
    var fmt = function (v) {
      return '€ ' + v.toLocaleString(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    };
    if (still) { el.textContent = fmt(end); return; }
    var t0 = null, dur = 900;
    function step(ts) {
      if (!t0) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);        // ease-out cubico
      el.textContent = fmt(end * eased);
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  });
})();

// 3) Toggle di un blocco (es. form inline "Aggiungi posizione"):
//    <button data-toggle="#id" data-alt="Chiudi">Aggiungi</button>
document.addEventListener('click', function (e) {
  var b = e.target.closest('[data-toggle]');
  if (!b) return;
  var el = document.querySelector(b.dataset.toggle);
  if (!el) return;
  var open = el.style.display !== 'none';
  el.style.display = open ? 'none' : '';
  if (b.dataset.alt) {
    var cur = b.dataset.alt;
    b.dataset.alt = b.textContent.trim();
    b.textContent = cur;
  }
  if (!open) {
    var first = el.querySelector('input, select, textarea');
    if (first) first.focus();
  }
});

// 4) PAC: al variare dell'importo le quote si ricalcolano LIVE (come nel
//    prototipo). Le righe portano data-target (percentuale); il totale è
//    quote + importi fissi (data-fixed sul campo).
(function () {
  var inp = document.getElementById('pac-importo');
  if (!inp) return;
  var lang = document.documentElement.lang || 'it';
  var eur = function (v) {
    return '€ ' + v.toLocaleString(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  function ricalcola() {
    var raw = (inp.value || '').replace(/\./g, '').replace(',', '.');
    var imp = parseFloat(raw);
    if (isNaN(imp) || imp < 0) return;
    var somma = 0;
    document.querySelectorAll('[data-target]').forEach(function (cell) {
      var q = Math.round(imp * parseFloat(cell.dataset.target)) / 100;
      somma += q;
      cell.textContent = eur(q);
    });
    var fissi = parseFloat(inp.dataset.fixed || '0') || 0;
    var tot = document.getElementById('pac-totale');
    if (tot) tot.textContent = eur(somma + fissi);
  }
  inp.addEventListener('input', ricalcola);
})();

// 5) Drawer del dettaglio posizione (PositionDetail del freeze): i link con
//    data-drawer aprono il dettaglio in un pannello (backdrop sfocato, ESC/X/
//    click fuori per chiudere). Senza JS o in caso di errore si naviga
//    normalmente alla pagina.
//
//    Da dove entra dipende dallo schermo. Sul PC scivola da destra, com'è nel
//    design freeze. Sul telefono SALE DAL FONDO: un pannello che entra di lato
//    su uno schermo da 375 punti è largo quanto la pagina, quindi non è un
//    pannello — è una pagina messa storta, e infatti si leggeva così. Dal fondo
//    invece arriva dove sta il pollice, si vede cosa c'è dietro, e si chiude
//    tirandolo giù.
(function () {
  var reduce = function () { return document.documentElement.dataset.anim === 'spente'; };
  var root = null, aside = null, backdrop = null, prevOverflow = '', giu = false;

  var CHIUSO = function () { return giu ? 'translateY(101%)' : 'translateX(102%)'; };
  var APERTO = function () { return giu ? 'translateY(0)' : 'translateX(0)'; };

  function onKey(e) { if (e.key === 'Escape') close(); }

  function close() {
    if (!root) return;
    var r = root, a = aside, b = backdrop;
    root = null;
    document.removeEventListener('keydown', onKey);
    document.body.style.overflow = prevOverflow;
    if (reduce()) { r.remove(); return; }
    b.style.opacity = '0';
    a.style.transform = CHIUSO();
    setTimeout(function () { r.remove(); }, 260);
  }

  function open(url) {
    if (root) return;
    giu = mmTelefono();
    root = document.createElement('div');
    // Sopra la barra in basso (60) e sopra il pannello «Altro» (80), o il
    // dettaglio si aprirebbe sotto la navigazione.
    root.style.cssText = 'position:fixed;inset:0;z-index:' + (giu ? 90 : 60) + ';';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    backdrop = document.createElement('div');
    backdrop.style.cssText = 'position:absolute;inset:0;background:rgba(20,26,12,.38);backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px);opacity:0;' +
      (reduce() ? '' : 'transition:opacity var(--dur-base) var(--ease-out);');
    backdrop.addEventListener('click', close);
    aside = document.createElement('aside');
    aside.style.cssText = (giu
      ? 'position:absolute;left:0;right:0;bottom:0;max-height:88vh;' +
        'border-top:1px solid var(--border);border-radius:22px 22px 0 0;' +
        'box-shadow:0 -12px 40px -16px rgba(0,0,0,.55);' +
        'padding-bottom:env(safe-area-inset-bottom, 0px);transform:translateY(101%);' +
        (reduce() ? '' : 'transition:transform var(--dur-slow) cubic-bezier(.32,.72,0,1);')
      : 'position:absolute;top:0;right:0;height:100%;width:min(560px,94vw);' +
        'border-left:1px solid var(--border);box-shadow:var(--shadow-lg);transform:translateX(102%);' +
        (reduce() ? '' : 'transition:transform var(--dur-slow) var(--ease-out);')
    ) + 'background:var(--surface);overflow-y:auto;-webkit-overflow-scrolling:touch;';

    // Il corpo è separato dall'aside perché la presa non deve sparire quando
    // arriva l'HTML del pannello e riscrive il contenuto.
    var corpo = aside;
    if (giu) {
      var presa = document.createElement('div');
      presa.className = 'tel-presa';
      presa.setAttribute('aria-hidden', 'true');
      aside.appendChild(presa);
      corpo = document.createElement('div');
      aside.appendChild(corpo);
      mmTrascina(aside, close, 'translateY(0)');
    }
    corpo.innerHTML = '<div class="faint" style="padding:24px;">…</div>';

    root.appendChild(backdrop);
    root.appendChild(aside);
    document.body.appendChild(root);
    prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKey);
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      backdrop.style.opacity = '1';
      aside.style.transform = APERTO();
    }); });
    fetch(url + (url.indexOf('?') >= 0 ? '&' : '?') + 'panel=1')
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (h) { if (root) corpo.innerHTML = h; })
      .catch(function () { window.location.href = url; });
  }

  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-drawer-close]')) { close(); return; }
    var a = e.target.closest('a[data-drawer]');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    open(a.getAttribute('href'));
  });
})();

// 6) Form movimento. Il tipo decide cosa si vede:
//    - entrata/uscita/trasferimento -> #mov-generic (e "A (portafoglio)" solo
//      per il trasferimento);
//    - partita di giro -> #mov-giro, con liste dinamiche di SPESE e RIENTRI
//      ("+ Aggiungi spesa/rientro" clonano un modello <template>, la ✕ rimuove
//      la riga tenendone sempre almeno una) e la casella "il rimborso arriverà
//      dopo" che nasconde i rientri (partita aperta).
document.addEventListener('change', function (e) {
  if (e.target && e.target.id === 'mov-tipo') {
    var v = e.target.value, giro = v === 'giro';
    var gen = document.getElementById('mov-generic');
    var box = document.getElementById('mov-giro');
    var to = document.getElementById('mov-wallet-to');
    if (gen) gen.style.display = giro ? 'none' : '';
    if (box) box.style.display = giro ? '' : 'none';
    if (to) to.style.display = v === 'trasferimento' ? '' : 'none';
  }
  if (e.target && e.target.id === 'mov-giro-dopo') {
    var wrap = document.getElementById('giro-rientri-wrap');
    if (wrap) wrap.style.display = e.target.checked ? 'none' : '';
  }
});

document.addEventListener('click', function (e) {
  // aggiungi una riga clonando il relativo <template>
  var add = e.target.closest('#giro-add-spesa, #giro-add-rientro');
  if (add) {
    var isSpesa = add.id === 'giro-add-spesa';
    var tpl = document.getElementById(isSpesa ? 'tpl-spesa' : 'tpl-rientro');
    var list = document.getElementById(isSpesa ? 'giro-spese' : 'giro-rientri');
    if (tpl && list) {
      var node = tpl.content.firstElementChild.cloneNode(true);
      list.appendChild(node);
      var first = node.querySelector('input, select');
      if (first) first.focus();
    }
    return;
  }
  // rimuovi una riga, ma tienine sempre almeno una nella sua lista
  var rm = e.target.closest('.giro-rm');
  if (rm) {
    var row = rm.closest('.giro-row');
    if (row && row.parentNode && row.parentNode.querySelectorAll('.giro-row').length > 1) {
      row.remove();
    }
  }
});

// 7) Ordinamento tabelle: click su un'intestazione .sortable inverte/imposta
//    l'ordine della colonna (asc/desc). Il valore di confronto è data-s sul
//    <td> (numeri/ISO-date) o, in mancanza, il testo della cella; le celle
//    vuote ('' o '—') finiscono SEMPRE in fondo. Il tfoot non si tocca.
document.addEventListener('click', function (e) {
  var th = e.target.closest('th.sortable');
  if (!th) return;
  var table = th.closest('table');
  if (!table || !table.tBodies.length) return;
  var tbody = table.tBodies[0];
  var ths = Array.prototype.slice.call(th.parentNode.children);
  var idx = ths.indexOf(th);
  var dir = th.classList.contains('asc') ? 'desc' : 'asc';
  ths.forEach(function (h) { h.classList.remove('asc', 'desc'); });
  th.classList.add(dir);
  var num = th.dataset.type === 'num';
  function key(row) {
    var td = row.cells[idx];
    if (!td) return null;
    var v = (td.dataset.s !== undefined ? td.dataset.s : td.textContent).trim();
    if (v === '' || v === '—') return null;
    if (num) {
      var f = parseFloat(v.replace(',', '.'));
      return isNaN(f) ? null : f;
    }
    return v.toLowerCase();
  }
  var rows = Array.prototype.slice.call(tbody.rows)
    .filter(function (r) { return r.cells.length > 1; });
  rows.map(function (r, i) { return { r: r, k: key(r), i: i }; })
    .sort(function (a, b) {
      if (a.k === null && b.k === null) return a.i - b.i;
      if (a.k === null) return 1;
      if (b.k === null) return -1;
      if (a.k < b.k) return dir === 'asc' ? -1 : 1;
      if (a.k > b.k) return dir === 'asc' ? 1 : -1;
      return a.i - b.i;                              // stabile a parità di valore
    })
    .forEach(function (x) { tbody.appendChild(x.r); });
});

// 8) Tendina holdings degli ETF (fragment caricato al primo click).
document.addEventListener('click', function (e) {
  var btn = e.target.closest('[data-holdings]');
  if (!btn) return;
  var id = btn.getAttribute('data-holdings');
  var row = document.getElementById('hr-' + id);
  if (!row) return;
  var box = row.querySelector('.holdings-box');
  if (row.style.display === 'none' || row.style.display === '') {
    var opening = (row.style.display === 'none');
    row.style.display = opening ? 'table-row' : 'none';
    if (opening && box && box.dataset.loaded === '0') {
      box.dataset.loaded = '1';
      box.innerHTML = '<div class="faint" style="padding:12px 16px;">…</div>';
      fetch('/portafoglio/' + id + '/holdings')
        .then(function (r) { return r.text(); })
        .then(function (h) { box.innerHTML = h; })
        .catch(function () { box.dataset.loaded = '0'; box.innerHTML = '<div class="faint" style="padding:12px 16px;">—</div>'; });
    }
  } else {
    row.style.display = 'none';
  }
});

/* Prosa AI: deve stare TUTTA nel riquadro, sopra il pulsante Rigenera.
   Se sfora di poco, si stringe il carattere (fino a 12.5px) finché entra; solo
   se proprio non basta resta lo scorrimento, con la sfumatura in basso che
   compare quando c'è davvero altro testo e sparisce arrivati in fondo. */
(function () {
  var MIN_PX = 12.5;

  function adatta(el) {
    var base = parseFloat(el.dataset.fsBase || '');
    if (!base) {
      base = parseFloat(getComputedStyle(el).fontSize) || 15;
      el.dataset.fsBase = String(base);
    }
    el.style.fontSize = base + 'px';
    var px = base;
    while (el.scrollHeight > el.clientHeight && px > MIN_PX) {
      px = Math.max(MIN_PX, px - 0.5);
      el.style.fontSize = px + 'px';
    }
  }
  function aggiorna(el) {
    var altro = el.scrollHeight - el.clientHeight - el.scrollTop > 4;
    el.classList.toggle('has-more', altro);
  }
  function collega() {
    var box = document.querySelectorAll('.ai-scroll');
    for (var i = 0; i < box.length; i++) {
      (function (el) {
        adatta(el);
        aggiorna(el);
        el.addEventListener('scroll', function () { aggiorna(el); });
        if (window.ResizeObserver) {
          // cambia lo spazio disponibile -> ricalcolo il carattere che ci sta
          new ResizeObserver(function () { adatta(el); aggiorna(el); }).observe(el);
        }
      })(box[i]);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', collega);
  } else {
    collega();
  }
  window.addEventListener('resize', function () {
    var box = document.querySelectorAll('.ai-scroll');
    for (var i = 0; i < box.length; i++) { adatta(box[i]); aggiorna(box[i]); }
  });
})();


/* ==========================================================================
   Telefono: il pannello «Altro».

   Le pagine dell'app sono sette e in una barra da quattro non ci stanno. Le tre
   che restano — più tema e uscita — salgono dal fondo da qui.

   Volutamente senza librerie e senza animazioni scritte a mano: la salita è una
   transizione CSS, questo codice sposta solo una classe. Meno pezzi, meno cose
   che si possono rompere su un telefono che non abbiamo sotto mano.
   ========================================================================== */
(function () {
  var apri = document.getElementById('tel-altro');
  var velo = document.getElementById('tel-velo');
  var foglio = document.getElementById('tel-foglio-altro');
  if (!apri || !velo || !foglio) return;

  function mostra(si) {
    velo.classList.toggle('aperto', si);
    foglio.classList.toggle('aperto', si);
    apri.setAttribute('aria-expanded', si ? 'true' : 'false');
    // Senza questo la pagina dietro scorre insieme al pannello, e sembra che
    // il pannello scappi via mentre lo si legge.
    document.body.style.overflow = si ? 'hidden' : '';
  }

  apri.addEventListener('click', function () {
    mostra(!foglio.classList.contains('aperto'));
  });
  velo.addEventListener('click', function () { mostra(false); });
  // Si chiude anche tirandolo giù, come il dettaglio (§5). La trasforma da
  // aperto qui la dà la classe `.aperto`, quindi il riposo è la stringa vuota.
  mmTrascina(foglio, function () { mostra(false); }, '');
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') mostra(false);
  });
  // Toccata una voce si va da un'altra parte: il pannello non deve restare
  // aperto sotto la pagina nuova se il browser torna indietro dalla cache.
  foglio.addEventListener('click', function (e) {
    if (e.target.closest('a, button')) mostra(false);
  });
  window.addEventListener('pageshow', function () { mostra(false); });
})();


/* ==========================================================================
   Telefono: le etichette delle tabelle.

   Una tabella da sette colonne su uno schermo da 375 punti non si legge: o si
   schiaccia fino a diventare illeggibile, o si scorre di lato cercando le
   colonne. Sul telefono ogni riga diventa una scheda e ogni cella una riga
   «etichetta · valore» (il disegno è in telefono.css).

   L'etichetta la sa solo l'intestazione della colonna, e la CSS non può
   leggerla: gliela passiamo noi qui, copiandola in un attributo. Fatto così
   invece che scrivendola a mano nei template, vale per TUTTE le tabelle —
   movimenti, portafoglio, PAC — e anche per quelle che verranno, senza che
   nessuno debba ricordarsene.
   ========================================================================== */
(function () {
  function etichetta() {
    var tabelle = document.querySelectorAll('table');
    for (var t = 0; t < tabelle.length; t++) {
      var intestazioni = tabelle[t].querySelectorAll('thead th');
      if (!intestazioni.length) continue;
      var nomi = [];
      for (var i = 0; i < intestazioni.length; i++) nomi.push(intestazioni[i].textContent.trim());
      var righe = tabelle[t].querySelectorAll('tbody tr');
      for (var r = 0; r < righe.length; r++) {
        var celle = righe[r].children;
        // Una riga con colspan è un messaggio ("nessun movimento"), non dati:
        // appiccicarle l'etichetta della prima colonna direbbe una bugia.
        if (celle.length === 1 && celle[0].getAttribute('colspan')) continue;
        for (var c = 0; c < celle.length; c++) {
          var cella = celle[c];
          if (nomi[c]) cella.setAttribute('data-etichetta', nomi[c]);
          // Su un foglio largo una cella vuota è una casella bianca e non
          // disturba nessuno. In una scheda da telefono diventa una riga
          // intera che dice «Categoria —»: occupa lo spazio di un'informazione
          // per comunicare che l'informazione non c'è. Si nasconde (la regola
          // è in telefono.css, sul PC la tabella resta identica).
          var testo = cella.textContent.replace(/\s+/g, '').trim();
          var vuota = (testo === '' || testo === '—' || testo === '-');
          if (vuota && !cella.querySelector('a, button, input, svg')) {
            cella.setAttribute('data-vuota', '1');
          } else {
            cella.removeAttribute('data-vuota');
          }
        }
      }
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', etichetta);
  } else {
    etichetta();
  }
  // Alcune tabelle arrivano DOPO, caricate a parte: i sottostanti di un titolo
  // (/portafoglio/<id>/holdings) compaiono quando apri la riga. Senza questo
  // sarebbero le uniche celle senza etichetta, cioe' le uniche illeggibili sul
  // telefono. Un osservatore invece di un evento da lanciare a mano: cosi' vale
  // anche per i pezzi che verranno, senza che nessuno debba ricordarsene.
  if (typeof MutationObserver !== 'undefined') {
    var inCoda = null;
    new MutationObserver(function (cambi) {
      for (var i = 0; i < cambi.length; i++) {
        for (var j = 0; j < cambi[i].addedNodes.length; j++) {
          var n = cambi[i].addedNodes[j];
          if (n.nodeType === 1 && (n.tagName === 'TABLE' || n.querySelector && n.querySelector('table'))) {
            // raggruppo: un innerHTML solo puo' scatenare molte notifiche
            clearTimeout(inCoda);
            inCoda = setTimeout(etichetta, 0);
            return;
          }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  }
})();
