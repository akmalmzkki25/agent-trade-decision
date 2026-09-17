/*
 * V6 desk renderers, shared as window.QlipV6Render; loaded before /v6/static/v6.js.
 *
 * Everything reaches the page through createElement + textContent. Agent notes,
 * order comments and every other recorded string are untrusted and must never be
 * parsed as HTML.
 */
(() => {
  'use strict';

  const HOLD_PREFIX = 'APP-V6-';
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
  const TEXT_TONES = {
    emerald: 'text-emerald-300', rose: 'text-rose-300', amber: 'text-amber-300', sky: 'text-sky-300',
  };
  // Runtime, cycle, intent, execution, outcome and side words share one palette.
  const STATUS_TONES = {
    RUNNING: 'emerald', HALTED: 'rose', BREAKER: 'rose', STALE: 'amber',
    WAITING_EA: 'amber', DISABLED: 'slate',
    ENTER: 'emerald', ENTER_SHADOW: 'sky', HOLD: 'slate', LATE: 'amber', ABORTED: 'amber',
    ERROR: 'rose', OPERATOR_TIMEOUT: 'amber',
    PUBLISHED: 'sky', DELIVERED: 'sky', REPORTED: 'amber', FILLED: 'emerald', CLOSED: 'slate',
    EXPIRED: 'slate', CANCELLED: 'slate', REJECTED: 'rose',
    placed: 'sky', filled: 'emerald', rejected_local: 'rose', failed: 'rose', dry_run: 'amber',
    expired: 'slate', cancelled: 'slate', win: 'emerald', loss: 'rose', flat: 'slate',
    buy: 'emerald', sell: 'rose',
  };
  const PERIODS = [['daily', 'Today'], ['weekly', 'This week'], ['monthly', 'This month']];
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
  const asNode = (part) => (part instanceof Node ? part : el('span', '', part));

  function badge(text, tone) {
    const classes = TONES[tone] || TONES.slate;
    return el('span', 'rounded-md px-1.5 py-0.5 text-[11px] font-medium ' + classes, text);
  }

  const statusBadge = (word) => badge(word || '—', STATUS_TONES[word]);
  const toned = (text, tone, className) =>
    el('span', (className || '') + ' ' + (TEXT_TONES[tone] || ''), text);

  // Parts on one line; empty parts are skipped, text parts become spans.
  function group(...parts) {
    const box = el('span', 'inline-flex flex-wrap items-center gap-1.5');
    parts.filter((part) => part !== null && part !== undefined && part !== '')
      .forEach((part) => box.append(asNode(part)));
    return box;
  }

  function stacked(top, bottom) {
    const box = el('span', 'inline-flex flex-col');
    box.append(asNode(top), el('span', 'text-[10px] text-slate-500', bottom));
    return box;
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

  // columns: [className, (row) => content]
  function renderTable(id, columns, rows, emptyText) {
    if (!Array.isArray(rows) || !rows.length) return fill(id, emptyRow(columns.length, emptyText));
    fill(id, ...rows.map((row) => tableRow(columns.map(([className, cell]) => [className, cell(row)]))));
  }

  // entries: [label, content]
  function pairs(entries) {
    const list = el('dl', 'grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs');
    entries.forEach(([label, content]) => {
      const cell = el('dd', 'min-w-0 break-words text-slate-200');
      cell.append(asNode(content === null || content === undefined ? '—' : content));
      list.append(el('dt', 'text-slate-500', label), cell);
    });
    return list;
  }

  // --- formatting ----------------------------------------------------------
  const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
  const num = (value, digits = 2) => (isNumber(value) ? value.toFixed(digits) : '—');
  const shortReason = (reason) => (typeof reason === 'string' ? reason.replace(HOLD_PREFIX, '') : '—');
  const iso = (epoch) => new Date(epoch * 1000).toISOString();
  const utc = (epoch) => (isNumber(epoch) ? iso(epoch).slice(5, 16).replace('T', ' ') + 'Z' : '—');
  const clock = (epoch) => (isNumber(epoch) ? iso(epoch).slice(11, 19) : '—');
  const money = (value) => (isNumber(value) ? (value < 0 ? '-$' : '$') + Math.abs(value).toFixed(2) : '—');
  const signedR = (value) => (isNumber(value) ? (value > 0 ? '+' : '') + value.toFixed(2) + 'R' : '—');
  const pnlTone = (value) => (!isNumber(value) || value === 0 ? '' : value > 0 ? 'emerald' : 'rose');
  const pnl = (value) => toned(money(value), pnlTone(value), 'tabular');

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

  // --- deliberation panels -------------------------------------------------
  const GATE_COLUMNS = [
    ['px-5 py-2 font-mono text-xs', (gate) => gate.code],
    ['px-3 py-2', (gate) => badge(gate.passed ? 'pass' : 'FAIL', gate.passed ? 'emerald' : 'rose')],
    ['px-3 py-2 tabular text-right text-xs', (gate) => scalar(gate.value)],
    ['px-3 py-2 tabular text-right text-xs text-slate-500', (gate) => scalar(gate.limit)],
    ['px-3 py-2 text-xs text-slate-400', (gate) => gate.detail || ''],
  ];
  const LABEL_COLUMNS = [
    ['px-5 py-2 font-mono text-xs', (s) => s.setup],
    ['px-3 py-2 text-xs', (s) => s.verdict],
    ['px-3 py-2 text-xs text-slate-400', (s) => s.label_status],
    ['px-3 py-2 text-xs', (s) => s.outcome],
    ['px-3 py-2 tabular text-right text-xs', (s) => s.count],
    ['px-3 py-2 tabular text-right text-xs', (s) => num(s.mean_r)],
  ];

  function renderGates(lastCycle) {
    setText('v6-last-cycle', lastCycle ? lastCycle.cycle_id + ' · bar ' + utc(lastCycle.bar_open_epoch) : '—');
    renderTable('v6-gates', GATE_COLUMNS, lastCycle ? lastCycle.gates : [],
      lastCycle ? 'This cycle recorded no gates.' : 'No cycle recorded yet.');
  }

  const renderLabels = (stats) => renderTable('v6-labels', LABEL_COLUMNS, stats, 'No candidates yet.');

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

  function periodCell(label, period) {
    const cell = el('div', 'rounded-md border border-slate-800 bg-slate-950/50 px-2 py-1.5');
    const value = el('p', 'text-sm font-semibold');
    value.append(pnl(period.breaker_realized));
    cell.append(el('p', 'text-[10px] text-slate-500', label + ' · n=' + (period.trades ?? 0)), value);
    if (period.unattributed_trades) {
      const note = money(period.unattributed_loss) + ' unattributed';
      cell.append(el('p', 'text-[10px] text-amber-300', note));
    }
    return cell;
  }

  function renderRealised(realised) {
    if (!realised) {
      return fill('v6-realised', el('p', 'text-[11px] text-slate-500', 'Realised V6 P&L appears once the EA polls.'));
    }
    const grid = el('div', 'mt-2 grid grid-cols-3 gap-2');
    PERIODS.forEach(([scope, label]) => grid.append(periodCell(label, realised.periods[scope] || {})));
    fill('v6-realised', el('p', 'text-[11px] font-medium uppercase tracking-wider text-slate-500',
      'Realised V6 P&L · login ' + realised.login), grid);
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
    const agent = record.source === 'operator' && record.model ? ' · ' + record.model : '';
    head.append(el('h4', 'text-xs font-semibold text-slate-200', ROLE_LABELS[record.role] || record.role),
      badge(record.source + agent, record.source === 'rules' ? 'slate' : 'sky'));
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
      chip.append(badge(c.side || '?', STATUS_TONES[c.side]),
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

  function intentTitle(cycle) {
    if (!cycle.shadow_intent) return 'Refusal';
    return cycle.status === 'ENTER' ? 'Intent (published to the EA)' : 'Shadow intent (not sent)';
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
        panel(intentTitle(cycle), cycle.shadow_intent || cycle.refusal));
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

  window.QlipV6Render = Object.freeze({
    TONES, TEXT_TONES, STATUS_TONES,
    el, byId, setText, fill, badge, statusBadge, toned, group, stacked, pairs, renderTable,
    isNumber, num, utc, clock, ago, scalar, money, signedR, pnl, pnlTone,
    renderGates, renderBreakers, renderRealised, renderHoldReasons, renderCycles, renderLabels,
  });
})();
