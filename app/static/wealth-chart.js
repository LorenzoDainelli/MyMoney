/* MyMoney — grafico del patrimonio (dashboard).
   Trasposizione 1:1 in vanilla JS del WealthChart del design freeze v1.0
   (design_reference/DashboardScreen.jsx): area + linea, gridline, etichette
   €k, baseline tratteggiata, hover con tooltip, bottoni range.
   I dati sono REALI e arrivano dal backend nel JSON #wealth-data. */
(function () {
  var host = document.getElementById('wealth-chart');
  var dataEl = document.getElementById('wealth-data');
  if (!host || !dataEl) return;
  var DATA = {};
  var LABELS = {};
  try { DATA = JSON.parse(dataEl.textContent || '{}'); } catch (e) { return; }
  try { LABELS = JSON.parse((document.getElementById('wealth-labels') || {}).textContent || '{}'); } catch (e) {}
  var ORDER = ['1G', '1S', '1M', '3M', '6M', 'YTD', '1A', '3A', '5A', '10A', 'MAX'];
  var keys = ORDER.filter(function (k) { return DATA[k] && DATA[k].v && DATA[k].v.length >= 2; });
  if (!keys.length) return;                       // resta l'avviso "in preparazione"
  var lang = document.documentElement.lang || 'it';
  var reduce = document.documentElement.dataset.anim === 'spente';

  var F = {
    eur: function (n, dec) {
      dec = (dec == null ? 2 : dec);
      return '€ ' + Number(n).toLocaleString(lang, { minimumFractionDigits: dec, maximumFractionDigits: dec });
    },
    signedEur: function (n) { return (n >= 0 ? '+' : '−') + F.eur(Math.abs(n)); },
    signedPct: function (n, dec) {
      dec = dec == null ? 1 : dec;
      return (n >= 0 ? '+' : '−') + Math.abs(n).toLocaleString(lang, { minimumFractionDigits: dec, maximumFractionDigits: dec }) + '%';
    },
  };

  // ---- struttura: bottoni range · svg · riga finale (label + guadagno) ----
  var btnRow = document.createElement('div');
  btnRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;';
  var chartWrap = document.createElement('div');
  chartWrap.style.cssText = 'width:100%;position:relative;';
  var footer = document.createElement('div');
  footer.style.cssText = 'display:flex;justify-content:space-between;align-items:baseline;margin-top:8px;';
  host.appendChild(btnRow); host.appendChild(chartWrap); host.appendChild(footer);

  var range = keys.indexOf('1A') >= 0 ? '1A' : keys[keys.length - 1];
  var hi = null;

  keys.forEach(function (k) {
    var b = document.createElement('button');
    b.type = 'button';
    b.textContent = k;
    b.dataset.k = k;
    b.addEventListener('click', function () { range = k; hi = null; render(); });
    btnRow.appendChild(b);
  });

  function styleButtons() {
    Array.prototype.forEach.call(btnRow.children, function (b) {
      var on = b.dataset.k === range;
      b.className = on ? '' : 'mm-rangebtn';
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.style.cssText = 'font:inherit;font-size:12px;font-weight:600;padding:4px 9px;border-radius:var(--r-sm);cursor:pointer;' +
        'border:1px solid ' + (on ? 'var(--accent)' : 'var(--border)') + ';' +
        'background:' + (on ? 'var(--accent-soft)' : 'transparent') + ';' +
        'color:' + (on ? 'var(--accent-strong)' : 'var(--muted)') + ';' +
        (reduce ? '' : 'transition:all var(--dur-fast) var(--ease-out);');
    });
  }

  function tickLabel(ts, k) {
    var d = new Date(ts * 1000);
    if (k === '1G') return d.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' });
    if (k === '1S' || k === '1M') return d.toLocaleDateString(lang, { day: 'numeric', month: 'short' });
    if (k === '3M' || k === '6M' || k === 'YTD' || k === '1A') return d.toLocaleDateString(lang, { month: 'short' });
    return String(d.getFullYear());
  }

  function render() {
    styleButtons();
    var R = DATA[range];
    var series = R.v, times = R.t;
    var lastI = series.length - 1;
    // Il minimo era 320: su un telefono da 375 la card ne lascia 297, e il
    // grafico sforava di 23 punti costringendo la card a scorrere di lato.
    // 240 è il punto sotto il quale una linea con le etichette non si legge più.
    var w = Math.max(240, Math.round(chartWrap.clientWidth || 760));
    var H = 264, padT = 16, padB = 26, padR = 14;
    // Lo spazio a sinistra serve alle etichette «€1.6k». Su uno schermo stretto
    // 50 punti sono un sesto del grafico: si stringe, e le etichette restano.
    var padL = w < 380 ? 38 : 50;
    var plotW = Math.max(10, w - padL - padR), plotH = H - padT - padB;
    var dmin = Math.min.apply(null, series), dmax = Math.max.apply(null, series);
    var room = (dmax - dmin) * 0.14 || dmax * 0.02 || 1;
    var lo = dmin - room, span = (dmax + room) - lo || 1;
    var X = function (i) { return padL + (lastI < 1 ? plotW / 2 : (i * plotW) / lastI); };
    var Y = function (v) { return padT + plotH * (1 - (v - lo) / span); };
    var up = series[lastI] >= series[0];
    var col = up ? 'var(--pos)' : 'var(--neg)';
    var line = series.map(function (v, i) { return X(i) + ',' + Y(v); }).join(' ');
    var area = X(0) + ',' + (padT + plotH) + ' ' + line + ' ' + X(lastI) + ',' + (padT + plotH);
    var fmtK = function (v) { return '€' + (span < 9000 ? (v / 1000).toFixed(1) : Math.round(v / 1000)) + 'k'; };

    var s = '<svg width="' + w + '" height="' + H + '" style="display:block;">';
    s += '<defs><linearGradient id="wg" x1="0" y1="0" x2="0" y2="1">' +
         '<stop offset="0" stop-color="' + col + '" stop-opacity="0.26"/>' +
         '<stop offset="1" stop-color="' + col + '" stop-opacity="0"/></linearGradient></defs>';
    for (var g = 0; g < 5; g++) {
      var gv = lo + (span * g) / 4;
      s += '<line x1="' + padL + '" y1="' + Y(gv) + '" x2="' + (w - padR) + '" y2="' + Y(gv) + '" stroke="var(--border)" stroke-width="1"' +
           (g === 0 ? '' : ' stroke-dasharray="3 5"') + ' opacity="' + (g === 0 ? 0.8 : 0.45) + '"/>';
      s += '<text x="' + (padL - 9) + '" y="' + (Y(gv) + 4) + '" text-anchor="end" font-size="11" fill="var(--muted)">' + fmtK(gv) + '</text>';
    }
    s += '<line x1="' + padL + '" y1="' + Y(series[0]) + '" x2="' + (w - padR) + '" y2="' + Y(series[0]) + '" stroke="' + col + '" stroke-width="1" stroke-dasharray="2 5" opacity="0.55"/>';
    s += '<polygon points="' + area + '" fill="url(#wg)"/>';
    s += '<polyline points="' + line + '" fill="none" stroke="' + col + '" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';
    var nticks = Math.min(times.length, range === '1G' ? 5 : 6);
    for (var ti = 0; ti < nticks; ti++) {
      var idx = Math.round((ti / (nticks - 1)) * lastI);
      var tx = X(idx);
      var anchor = ti === 0 ? 'start' : ti === nticks - 1 ? 'end' : 'middle';
      s += '<text x="' + tx + '" y="' + (H - 7) + '" text-anchor="' + anchor + '" font-size="11" fill="var(--muted)">' + tickLabel(times[idx], range) + '</text>';
    }
    s += '<circle cx="' + X(lastI) + '" cy="' + Y(series[lastI]) + '" r="3.5" fill="' + col + '"/>';
    if (hi != null) {
      s += '<line x1="' + X(hi) + '" y1="' + padT + '" x2="' + X(hi) + '" y2="' + (padT + plotH) + '" stroke="var(--muted)" stroke-width="1" opacity="0.4"/>';
      s += '<circle cx="' + X(hi) + '" cy="' + Y(series[hi]) + '" r="4.5" fill="var(--surface)" stroke="' + col + '" stroke-width="2.5"/>';
    }
    s += '<rect x="' + padL + '" y="' + padT + '" width="' + plotW + '" height="' + plotH + '" fill="transparent" style="cursor:crosshair;"/>';
    s += '</svg>';

    var tip = '';
    if (hi != null) {
      var hx = Math.min(Math.max(X(hi), 54), w - 54);
      var hy = Math.max(Y(series[hi]) - 48, 2);
      tip = '<div style="position:absolute;left:' + hx + 'px;top:' + hy + 'px;transform:translateX(-50%);pointer-events:none;' +
        'background:var(--surface);border:1px solid var(--border);border-radius:var(--r-sm);box-shadow:var(--shadow-md);padding:5px 9px;white-space:nowrap;">' +
        '<div class="num" style="font-weight:700;color:var(--ink);font-size:13px;">' + F.eur(series[hi]) + '</div>' +
        '<div class="faint" style="font-size:11px;margin-top:1px;">' + tickLabel(times[hi], range) + '</div></div>';
    }
    chartWrap.innerHTML = s + tip;

    var rect = chartWrap.querySelector('rect');
    if (rect) {
      rect.addEventListener('mousemove', function (e) {
        var r = chartWrap.getBoundingClientRect();
        var i = Math.round(((e.clientX - r.left - padL) / plotW) * lastI);
        i = Math.max(0, Math.min(lastI, i));
        if (i !== hi) { hi = i; render(); }
      });
      rect.addEventListener('mouseleave', function () { hi = null; render(); });
    }

    var gain = series[lastI] - series[0];
    footer.innerHTML =
      '<span class="muted" style="font-size:13px;">' + (LABELS[range] || '') + '</span>' +
      '<span class="num ' + (gain >= 0 ? 'pos' : 'neg') + '" style="font-weight:700;">' +
      F.signedEur(gain) + (R.pct != null ? ' · ' + F.signedPct(R.pct) : '') + '</span>';
  }

  if (typeof ResizeObserver !== 'undefined') {
    var ro = new ResizeObserver(function () { render(); });
    ro.observe(chartWrap);
  }
  // Due reti di sicurezza sotto al ResizeObserver, perché quello è legato al
  // disegno dei fotogrammi: se il primo disegno capita prima che il contenitore
  // abbia una larghezza, il grafico resta a 760 (il ripiego) dentro una card
  // che ne misura 600, e nessuno lo corregge più. `load` copre il caricamento,
  // `resize` copre la rotazione del telefono e la finestra tirata sul PC.
  window.addEventListener('load', function () { render(); });
  window.addEventListener('resize', function () { render(); });
  render();
})();
