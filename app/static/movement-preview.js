/* MyMoney — «che cosa cambia»: l'effetto del movimento PRIMA di salvarlo.

   Il modulo si compilava alla cieca: sceglievi un portafoglio, salvavi, e se
   era quello sbagliato lo scoprivi giorni dopo scorrendo la tabella. Qui il
   saldo del conto e il totale del mese si aggiornano mentre scrivi.

   Nessun calcolo nuovo: i saldi e le uscite per categoria arrivano già
   calcolati dal server (finance/routes.py), il JS fa solo la somma dell'unico
   movimento in corso. */
(function () {
  var host = document.getElementById('mov-effetto');
  var form = document.getElementById('mov-form');
  var elD = document.getElementById('mov-effetto-dati');
  var elT = document.getElementById('mov-effetto-testi');
  if (!host || !form || !elD || !elT) return;
  var D, T;
  try { D = JSON.parse(elD.textContent); T = JSON.parse(elT.textContent); } catch (e) { return; }

  // Stessa formattazione di shared/formatting.format_eur: sulla stessa pagina
  // due modi di scrivere 1.816,89 si notano subito.
  function eur(n) {
    var neg = Number(n) < 0;
    var p = Math.abs(Number(n)).toFixed(2).split('.');
    return '€ ' + (neg ? '-' : '') + p[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.') + ',' + p[1];
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

  function vuoto() {
    host.innerHTML = '<p class="faint" style="margin:0;font-size:13px;line-height:var(--lh-relaxed);">' +
      esc(T.empty) + '</p>';
  }

  function render() {
    var tipo = (form.querySelector('#mov-tipo') || {}).value || 'uscita';
    if (tipo === 'giro') { host.innerHTML = nota(T.giro).replace('margin-top:10px;', ''); return; }

    var imp = num(val('importo'));
    var w = D.wallets[String(val('wallet_id'))];
    if (!imp || !w) { vuoto(); return; }

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

  form.addEventListener('input', render);
  form.addEventListener('change', render);
  render();
})();
