/*
 * V6 deliberation desk: polls /v6/api/overview and renders it.
 *
 * Everything reaches the page through createElement + textContent. Agent
 * notes are untrusted model output and must never be parsed as HTML. The HALT
 * nonce is refreshed by every overview: it changes when the adapter restarts.
 */
(() => {
  'use strict';

  const root = document.getElementById('v6-root');
  if (!root) return;

  const POLL_MS = Number(root.dataset.pollMs) || 5000;
  let csrfNonce = root.dataset.csrf || '';
  const [OVERVIEW_URL, HALT_URL] = ['/v6/api/overview', '/v6/control/halt'];
  const HOLD_PREFIX = 'APP-V6-';
  const HALT_FALLBACK = ' (reload the page and retry, or create the V6_HALT file)';
  const ROLE_ORDER = ['price_action', 'news_risk', 'liquidity', 'structure', 'chief'];
  const ROLE_LABELS = {
    price_action: 'Price action', news_risk: 'News risk', liquidity: 'Liquidity',
    structure: 'Structure', chief: 'Chief',
  };
  const UNTRUSTED_KEYS = new Set(['note', 'rationale', 'dissent']);
  const TONES = {
    emerald: 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30',
    rose: 'bg-rose-500/15 text-rose-300 ring-1 ring-rose-500/30',
    amber: 'bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/30',
    sky: 'bg-sky-500/15 text-sky-300 ring-1 ring-sky-500/30',
    slate: 'bg-slate-800 text-slate-300 ring-1 ring-slate-700',
  };
  const TEXT_TONES = { emerald: 'text-emerald-300', rose: 'text-rose-300', amber: 'text-amber-300' };
  const STATUS_TONES = {
    RUNNING: 'emerald', HALTED: 'rose', BREAKER: 'rose', STALE: 'amber',
    WAITING_EA: 'amber', DISABLED: 'slate',
    ENTER_SHADOW: 'emerald', HOLD: 'slate', LATE: 'amber', ABORTED: 'amber', ERROR: 'rose',
  };
  const PILL = 'rounded-lg px-3 py-1.5 font-mono text-sm font-semibold tracking-wide ';
  const ROW = 'text-slate-300 transition hover:bg-slate-800/40';

  // --- DOM helpers ---------------------------------------------------------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  const byId = (id) => document.getElementById(id);
  const setText = (id, text) => { byId(id).textContent = text; };
  const fill = (id, ...children) => byId(id).replaceChildren(...children);

  function badge(text, tone) {
    const classes = TONES[tone] || TONES.slate;
    return el('span', 'rounded-md px-1.5 py-0.5 text-[11px] font-medium ' + classes, text);
  }

  function emptyNote(text) {
    return el('p', 'rounded-md border border-dashed border-slate-800 px-3 py-5 text-center text-xs text-slate-500', text);
  }

  function emptyRow(columns, text) {
    const cell = el('td', 'px-5 py-8 text-center text-xs text-slate-500', text);
    cell.colSpan = columns;
    const row = el('tr');
    row.append(cell);
    return row;
  }

  // cells: [className, content]; content is text or a node.
  function tableRow(cells) {
    const row = el('tr', ROW);
    cells.forEach(([className, content]) => {
      const cell = el('td', className);
      if (content instanceof Node) cell.append(content);
      else cell.textContent = content === null || content === undefined ? '—' : String(content);
      row.append(cell);
    });
    return row;
  }

  // --- formatting ----------------------------------------------------------
  const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
  const num = (value, digits = 2) => (isNumber(value) ? value.toFixed(digits) : '—');
  const shortReason = (reason) => (typeof reason === 'string' ? reason.replace(HOLD_PREFIX, '') : '—');

  function utc(epoch) {
    if (!isNumber(epoch)) return '—';
    return new Date(epoch * 1000).toISOString().slice(5, 16).replace('T', ' ') + 'Z';
  }

  function ago(seconds) {
    if (!isNumber(seconds)) return 'never';
    if (seconds < 90) return Math.round(seconds) + ' s ago';
    if (seconds < 5400) return Math.round(seconds / 60) + ' min ago';
    return (seconds / 3600).toFixed(1) + ' h ago';
  }

  // Up to four decimals, trailing zeros dropped: 4289.8, 0.01, 0.7.
  function scalar(value) {
    if (value === null || value === undefined) return '—';
    return isNumber(value) ? String(Number(value.toFixed(4))) : String(value);
  }

  // --- status tiles --------------------------------------------------------
  function tile(key, value, sub, tone) {
    const node = byId('v6-tile-' + key);
    node.textContent = value;
    node.className = 'mt-2 tabular text-xl font-semibold ' + (TEXT_TONES[tone] || 'text-slate-100');
    setText('v6-tile-' + key + '-sub', sub);
  }

  function renderStatus(runtime) {
    const tone = STATUS_TONES[runtime.status] || 'slate';
    const status = byId('v6-status');
    status.textContent = runtime.status;
    status.className = PILL + TONES[tone];
    setText('v6-mode', runtime.mode);
    const command = runtime.pending_command ? ' · ' + runtime.pending_command.command : '';
    tile('runtime', runtime.status, runtime.mode + ' · ' + runtime.backend + command, tone);
  }

  function renderEa(ea) {
    const spread = isNumber(ea.spread_points) ? ' · spread ' + ea.spread_points + ' pts' : '';
    tile('ea', ago(ea.last_seen_age_s), (ea.trade_mode || 'trade mode unknown') + spread,
      ea.stale ? 'amber' : 'emerald');
    const exposure = ea.exposure || {};
    const open = isNumber(exposure.open_v6_positions) ? exposure.open_v6_positions : '—';
    const pending = isNumber(exposure.pending_v6_orders) ? exposure.pending_v6_orders : '—';
    tile('exposure', open + ' open', pending + ' pending · floating ' + num(exposure.floating_pnl_v6),
      open > 0 ? 'amber' : '');
  }

  function renderSession(session) {
    const active = session.active;
    if (active) {
      tile('session', 'Active', 'since ' + utc(active.started_at) + ' · ' + active.backend, 'emerald');
    } else {
      tile('session', 'None', 'tier 0 only · analysis still recorded', '');
    }
    const day = session.day || {};
    const holds = Object.values(day.hold_reasons || {}).reduce((a, b) => a + b, 0);
    tile('today', (day.cycles ?? 0) + ' cycles',
      (day.shadow_intents ?? 0) + ' shadow intents · ' + holds + ' holds · ' + session.trading_day, '');
  }

  function renderSizing(sizing) {
    if (!sizing.available) return tile('sizing', '—', sizing.reason || 'unavailable', 'amber');
    tile('sizing', '$' + num(sizing.min_tradeable_equity),
      'stop floor ' + num(sizing.stop_floor) + ' · tick value ' + sizing.tick_value +
      ' (' + sizing.tick_value_source + ')', sizing.tradeable_at_basis ? 'emerald' : 'rose');
  }

  // --- panels --------------------------------------------------------------
  function renderGates(lastCycle) {
    setText('v6-last-cycle', lastCycle ? lastCycle.cycle_id + ' · bar ' + utc(lastCycle.bar_open_epoch) : '—');
    const gates = lastCycle ? lastCycle.gates || [] : [];
    if (!gates.length) {
      return fill('v6-gates', emptyRow(5, lastCycle ? 'This cycle recorded no gates.' : 'No cycle recorded yet.'));
    }
    fill('v6-gates', ...gates.map((gate) => tableRow([
      ['px-5 py-2 font-mono text-xs', gate.code],
      ['px-3 py-2', badge(gate.passed ? 'pass' : 'FAIL', gate.passed ? 'emerald' : 'rose')],
      ['px-3 py-2 tabular text-right text-xs', scalar(gate.value)],
      ['px-3 py-2 tabular text-right text-xs text-slate-500', scalar(gate.limit)],
      ['px-3 py-2 text-xs text-slate-400', gate.detail || ''],
    ])));
  }

  function renderBreakers(breakers) {
    if (!breakers.length) return fill('v6-breakers', badge('none tripped', 'emerald'));
    const list = el('ul', 'space-y-2');
    breakers.forEach((b) => {
      const item = el('li', 'rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200');
      item.append(el('p', 'font-mono font-semibold', b.scope + ' · ' + b.period_key),
        el('p', 'text-rose-300/80', b.reason + ' · ' + utc(b.tripped_at)));
      list.append(item);
    });
    fill('v6-breakers', list);
  }

  function holdBar(reason, count, max) {
    const item = el('li', 'text-xs');
    const head = el('div', 'flex items-center justify-between gap-2');
    head.append(el('span', 'font-mono text-slate-300', shortReason(reason)),
      el('span', 'tabular text-slate-400', count));
    const track = el('div', 'mt-1 h-1.5 overflow-hidden rounded-full bg-slate-800');
    const bar = el('div', 'h-full rounded-full bg-emerald-400/70');
    bar.style.width = Math.max(4, Math.round((100 * count) / max)) + '%';
    track.append(bar);
    item.append(head, track);
    return item;
  }

  function renderHoldReasons(counts) {
    const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
    if (!entries.length) return fill('v6-hold-reasons', emptyNote('No holds recorded.'));
    const list = el('ul', 'space-y-2.5');
    entries.forEach(([reason, count]) => list.append(holdBar(reason, count, entries[0][1])));
    fill('v6-hold-reasons', list);
  }

  function renderLabels(stats) {
    if (!stats.length) return fill('v6-labels', emptyRow(6, 'No candidates yet.'));
    fill('v6-labels', ...stats.map((s) => tableRow([
      ['px-5 py-2 font-mono text-xs', s.setup],
      ['px-3 py-2 text-xs', s.verdict],
      ['px-3 py-2 text-xs text-slate-400', s.label_status],
      ['px-3 py-2 text-xs', s.outcome],
      ['px-3 py-2 tabular text-right text-xs', s.count],
      ['px-3 py-2 tabular text-right text-xs', num(s.mean_r)],
    ])));
  }

  // --- transcript ----------------------------------------------------------
  function renderValue(value) {
    if (value === null || value === undefined) return el('span', 'text-slate-600', '—');
    if (Array.isArray(value)) return renderList(value);
    if (typeof value === 'object') return renderObject(value);
    if (typeof value === 'boolean') return el('span', 'font-mono text-slate-300', value ? 'yes' : 'no');
    if (isNumber(value)) return el('span', 'tabular text-slate-200', scalar(value));
    return el('span', 'break-words text-slate-300', value);
  }

  function renderList(values) {
    if (!values.length) return el('span', 'text-slate-600', 'none');
    if (values.every((v) => v === null || typeof v !== 'object')) {
      return el('span', 'break-words font-mono text-[11px] text-slate-300', values.join(', '));
    }
    const list = el('div', 'space-y-1.5');
    values.forEach((v) => {
      const box = el('div', 'rounded-md border border-slate-800 bg-slate-950/60 p-2');
      box.append(renderValue(v));
      list.append(box);
    });
    return list;
  }

  function renderObject(object) {
    const grid = el('dl', 'grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs');
    Object.entries(object).forEach(([key, value]) => {
      const cell = el('dd', 'min-w-0');
      if (UNTRUSTED_KEYS.has(key) && typeof value === 'string') {
        cell.append(el('span', 'break-words italic text-slate-400', value ? '“' + value + '”' : '—'));
      } else {
        cell.append(renderValue(value));
      }
      grid.append(el('dt', 'text-slate-500', key), cell);
    });
    return grid;
  }

  function roleCard(record) {
    // Price action carries the ranked candidates, so it gets a double-width column.
    const span = record.role === 'price_action' ? ' md:col-span-2' : '';
    const card = el('div', 'min-w-0 rounded-lg border border-slate-800 bg-slate-950/50 p-3' + span);
    const head = el('div', 'mb-2 flex items-center justify-between gap-2');
    head.append(el('h4', 'text-xs font-semibold text-slate-200', ROLE_LABELS[record.role] || record.role),
      badge(record.source, record.source === 'rules' ? 'slate' : 'sky'));
    card.append(head, record.error_code ? badge(record.error_code, 'rose') : renderValue(record.view));
    return card;
  }

  function renderRoles(views) {
    if (!views.length) return el('p', 'text-xs text-slate-500', 'No agent was asked (tier 0 held).');
    const ordered = [...views].sort((a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role));
    const grid = el('div', 'grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-6');
    ordered.forEach((record) => grid.append(roleCard(record)));
    return grid;
  }

  function renderCandidates(candidates) {
    if (!candidates.length) return el('p', 'text-xs text-slate-500', 'No candidate detected.');
    const list = el('div', 'flex flex-wrap gap-2');
    candidates.forEach((c) => {
      const chip = el('div', 'flex items-center gap-2 rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]');
      chip.append(badge(c.side || '?', c.side === 'buy' ? 'emerald' : 'rose'),
        el('span', 'font-mono text-slate-300', c.candidate_id),
        el('span', 'text-slate-500', c.verdict + ' · entry ' + num(c.entry) + ' · stop ' + num(c.stop)));
      if (c.refusal && c.refusal.length) chip.append(badge(c.refusal.join(','), 'amber'));
      list.append(chip);
    });
    return list;
  }

  function panel(title, value) {
    const box = el('div', 'rounded-lg border border-slate-800 p-3');
    box.append(el('h4', 'mb-2 text-xs font-semibold text-slate-200', title), renderValue(value));
    return box;
  }

  function cycleHeader(cycle) {
    const head = el('header', 'flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-2.5');
    const left = el('div', 'flex flex-wrap items-center gap-2');
    left.append(el('span', 'tabular text-xs text-slate-400', utc(cycle.bar_open_epoch)),
      badge(cycle.status, STATUS_TONES[cycle.status]));
    if (cycle.hold_reason) left.append(el('span', 'font-mono text-[11px] text-slate-300', shortReason(cycle.hold_reason)));
    if (cycle.hold_detail) left.append(el('span', 'text-[11px] text-slate-500', cycle.hold_detail));
    const right = el('div', 'flex items-center gap-3 text-[11px] text-slate-500');
    right.append(el('span', 'font-mono', cycle.provider + ' · ' + cycle.provider_status),
      el('span', 'tabular', cycle.total_ms + ' ms'));
    head.append(left, right);
    return head;
  }

  function cycleCard(cycle) {
    const card = el('article', 'overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40');
    const body = el('div', 'space-y-3 p-4');
    if (cycle.failed_gates.length) {
      body.append(el('p', 'text-xs text-rose-300', 'Failed gates: ' + cycle.failed_gates.join(', ')));
    }
    body.append(renderCandidates(cycle.candidates), renderRoles(cycle.views));
    if (cycle.protocol || cycle.shadow_intent || cycle.refusal) {
      const verdict = el('div', 'grid grid-cols-1 gap-3 md:grid-cols-2');
      verdict.append(panel('Protocol', cycle.protocol),
        panel('Shadow intent (never sent)', cycle.shadow_intent || cycle.refusal));
      body.append(verdict);
    }
    card.append(cycleHeader(cycle), body);
    return card;
  }

  function renderCycles(cycles) {
    if (!cycles.length) {
      return fill('v6-cycles', emptyNote('No cycle recorded yet. The desk runs at every M15 close.'));
    }
    fill('v6-cycles', ...cycles.map(cycleCard));
  }

  // --- refresh loop --------------------------------------------------------
  function render(data) {
    renderStatus(data.runtime);
    renderEa(data.ea);
    renderSession(data.session);
    renderSizing(data.sizing);
    renderGates(data.last_cycle);
    renderBreakers(data.breakers);
    renderHoldReasons(data.hold_reasons_7d);
    renderCycles(data.recent_cycles);
    renderLabels(data.label_stats);
  }

  function showDisabled(disabled) {
    byId('v6-disabled').hidden = !disabled;
    byId('v6-halt').disabled = disabled || !csrfNonce;
    if (disabled) {
      const status = byId('v6-status');
      status.textContent = 'DISABLED';
      status.className = PILL + TONES.slate;
    }
  }

  async function refresh() {
    if (document.hidden) return;
    try {
      const response = await fetch(OVERVIEW_URL, {
        cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' },
      });
      if (response.status === 404) return showDisabled(true);
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      if (typeof data.csrf_nonce === 'string' && data.csrf_nonce) csrfNonce = data.csrf_nonce;
      showDisabled(false);
      render(data);
      setText('v6-last-update', new Date().toISOString().slice(11, 19) + ' UTC');
      setText('v6-error', '');
    } catch (error) {
      setText('v6-error', 'Refresh failed: ' + error.message);
    }
  }

  async function halt() {
    const button = byId('v6-halt');
    if (!csrfNonce) return;
    if (!window.confirm('HALT V6 now? No new entries until an operator resumes with the token.')) return;
    button.disabled = true;
    try {
      const response = await fetch(HALT_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-V6-CSRF': csrfNonce },
        body: JSON.stringify({ reason: 'dashboard' }),
      });
      const body = await response.json().catch(() => ({}));
      setText('v6-halt-result', response.ok
        ? (body.created ? 'HALT file written.' : 'Already halted.')
        : 'HALT FAILED: ' + JSON.stringify(body.detail || response.status) + HALT_FALLBACK);
    } catch (error) {
      setText('v6-halt-result', 'HALT FAILED: ' + error.message + HALT_FALLBACK);
    } finally {
      button.disabled = false;
      refresh();
    }
  }

  byId('v6-halt').addEventListener('click', halt);
  document.addEventListener('visibilitychange', () => document.hidden || refresh());
  refresh();
  window.setInterval(refresh, POLL_MS);
})();
