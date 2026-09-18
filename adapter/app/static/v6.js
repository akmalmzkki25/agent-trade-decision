/*
 * V6 deliberation desk: polls /v6/api/overview and renders it with the helpers of
 * /v6/static/v6_render.js (window.QlipV6Render, loaded first).
 *
 * Everything reaches the page through createElement + textContent; recorded text is
 * untrusted. The HALT nonce is refreshed by every overview (it changes when the
 * adapter restarts), and HALT keeps working even when the renderer did not load.
 */
(() => {
  'use strict';

  const root = document.getElementById('v6-root');
  if (!root) return;

  const R = window.QlipV6Render || null;
  const POLL_MS = Number(root.dataset.pollMs) || 5000;
  let csrfNonce = root.dataset.csrf || '';
  const [OVERVIEW_URL, HALT_URL] = ['/v6/api/overview', '/v6/control/halt'];
  const HALT_FALLBACK = ' (reload the page and retry, or create the V6_HALT file)';
  const RENDERER_MISSING = 'the page renderer (v6_render.js) did not load';
  const PILL = 'rounded-lg px-3 py-1.5 font-mono text-sm font-semibold tracking-wide ';
  const LIFECYCLE = [['created_at', 'published'], ['delivered_at', 'delivered'],
    ['reported_at', 'reported'], ['closed_at', 'closed']];
  const C = 'px-3 py-2 text-xs';
  const N = C + ' tabular text-right whitespace-nowrap';
  const F = 'px-5 py-2 text-xs tabular whitespace-nowrap';
  const MONO = C + ' font-mono';
  const {
    el, badge, statusBadge, toned, group, stacked, pairs, renderTable, isNumber, num, utc,
    clock, ago, scalar, money, signedR, pnl, pnlTone,
    TONES = {}, TEXT_TONES = {}, STATUS_TONES = {},
  } = R || {};

  const byId = (id) => document.getElementById(id);
  const setText = (id, text) => { byId(id).textContent = text; };
  const count = (value) => 'n = ' + (value ?? 0);
  // An average over a handful of trades is noise: colour it only from this many on.
  const MIN_TONED_SAMPLE = 30;
  const sampleTone = (value, n) => ((n ?? 0) >= MIN_TONED_SAMPLE ? pnlTone(value) : '');

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
    const execute = session.mode === 'execute';
    if (!active) {
      tile('session', 'None', 'tier 0 only · analysis still recorded', '');
    } else if (session.armed) {
      tile('session', 'Armed', 'since ' + utc(active.armed_at) + ' · ' + active.backend, 'emerald');
    } else {
      tile('session', 'Active', (execute ? 'not armed · no intents' : 'shadow · never armed') +
        ' · since ' + utc(active.started_at), execute ? 'amber' : 'sky');
    }
    const day = session.day || {};
    const holds = Object.values(day.hold_reasons || {}).reduce((a, b) => a + b, 0);
    tile('today', (day.cycles ?? 0) + ' cycles', (day.intents ?? 0) + ' intents · ' +
      (day.shadow_intents ?? 0) + ' shadow · ' + holds + ' holds · ' + session.trading_day, '');
  }

  function renderSizing(sizing) {
    if (!sizing.available) return tile('sizing', '—', sizing.reason || 'unavailable', 'amber');
    tile('sizing', '$' + num(sizing.min_tradeable_equity),
      'stop floor ' + num(sizing.stop_floor) + ' · tick value ' + sizing.tick_value +
      ' (' + sizing.tick_value_source + ')', sizing.tradeable_at_basis ? 'emerald' : 'rose');
  }

  // --- session and operator --------------------------------------------------
  function armedState(session) {
    const active = session.active;
    if (!active) return '—';
    if (session.armed) return group(badge('ARMED', 'emerald'), 'since ' + utc(active.armed_at));
    const last = active.disarm_reason
      ? 'disarmed ' + utc(active.disarmed_at) + ' (' + active.disarm_reason + ')' : 'never armed';
    return group(badge('DISARMED', session.mode === 'execute' ? 'amber' : 'slate'), last);
  }

  function pendingDecision(operator) {
    if (!operator.pending_cycle_id) return 'none';
    const left = operator.pending_seconds_left;
    return group(el('span', 'font-mono', operator.pending_cycle_id),
      toned(isNumber(left) ? left + ' s left' : 'deadline unknown', left < 60 ? 'amber' : 'sky'));
  }

  function renderOperator(session, runtime) {
    const operator = session.operator || {};
    const active = session.active;
    const lastAgent = operator.last_agent
      ? group(el('span', 'font-mono', operator.last_agent), ago(operator.last_seen_age_s))
      : 'no decision yet';
    const sessionId = active
      ? group(el('span', 'font-mono', active.session_id), 'since ' + utc(active.started_at)) : 'none';
    R.fill('v6-operator', pairs([
      ['Session', sessionId],
      ['Armed', armedState(session)],
      ['Backend · mode', session.backend + ' · ' + session.mode],
      ['Agents allowed', (operator.agents || []).join(', ') || '—'],
      ['Operator token', runtime.operator_ready ? 'configured' : toned('missing', 'amber')],
      ['EA signing', runtime.ea_signing],
      ['Last decision by', lastAgent],
      ['Awaiting decision', pendingDecision(operator)],
    ]));
  }

  // --- trading tables ----------------------------------------------------------
  const lifecycle = (intent) => LIFECYCLE.filter(([key]) => isNumber(intent[key]))
    .map(([key, label]) => label + ' ' + clock(intent[key])).join(' → ');
  const sideBadge = (side) => badge(side || '?', STATUS_TONES[side]);
  const reason = (code) => (code && code !== 'NONE' ? code : '');

  function labelCell(label) {
    if (!label) return '—';
    return group(el('span', 'font-mono', label.outcome || label.label_status), signedR(label.r));
  }

  const INTENT_COLUMNS = [
    [F, (i) => utc(i.created_at)],
    [MONO, (i) => stacked(i.intent_id, i.agent || i.source)],
    [C, (i) => group(sideBadge(i.side), i.order_type)],
    [N, (i) => num(i.entry)], [N, (i) => num(i.sl)], [N, (i) => num(i.tp)],
    [N, (i) => scalar(i.lots)], [N, (i) => money(i.risk_usd)],
    [C, (i) => group(statusBadge(i.status), reason(i.report_reason))],
    [C + ' text-slate-400', lifecycle],
    [N, (i) => i.ticket],
    [N, (i) => stacked(pnl(i.outcome_pnl), signedR(i.r_multiple))],
  ];
  const EXECUTION_COLUMNS = [
    [F, (x) => utc(x.received_at)],
    [MONO, (x) => x.intent_id],
    [C, (x) => statusBadge(x.status)],
    [MONO, (x) => reason(x.reason_code) || '—'],
    [N, (x) => x.ticket || '—'],
    [N, (x) => (x.fill_price ? num(x.fill_price) : '—')],
    [N, (x) => scalar(x.slippage_points)],
    [N, (x) => scalar(x.spread_points)],
    [N, (x) => (isNumber(x.latency_ms) ? x.latency_ms + ' ms' : '—')],
  ];
  const POSITION_COLUMNS = [
    [F, (p) => p.ticket],
    [MONO, (p) => p.intent_id || p.comment || '—'],
    [C, (p) => sideBadge(p.side)],
    [N, (p) => scalar(p.volume)], [N, (p) => num(p.price_open)],
    [N, (p) => num(p.sl)], [N, (p) => num(p.tp)],
    [N, (p) => pnl(p.profit + p.swap)],
    [N, (p) => scalar(p.mae_points) + ' / ' + scalar(p.mfe_points)],
    [C, (p) => utc(p.open_epoch)],
  ];
  const ORDER_COLUMNS = [
    [F, (o) => o.ticket],
    [MONO, (o) => o.intent_id || o.comment || '—'],
    [C, (o) => o.order_type],
    [N, (o) => num(o.price)], [N, (o) => num(o.sl)], [N, (o) => num(o.tp)],
    [N, (o) => scalar(o.volume)],
    [C, (o) => (o.expiration_epoch ? utc(o.expiration_epoch) : 'no expiry')],
  ];
  const OUTCOME_COLUMNS = [
    [F, (o) => utc(o.closed_epoch)],
    [MONO, (o) => (o.linked ? o.intent_id : group(o.intent_id || o.basket_id, badge('unlinked', 'amber')))],
    [C, (o) => o.agent || o.source],
    [C, (o) => sideBadge(o.side)],
    [MONO, (o) => o.close_reason || '—'],
    [N, (o) => pnl(o.net_pnl)],
    [N, (o) => signedR(o.r_multiple)],
    [C, (o) => labelCell(o.label)],
    [N, (o) => signedR(o.r_gap)],
  ];

  const ACTION_COLUMNS = [
    [F, (a) => utc(a.created_at)],
    [MONO, (a) => stacked(a.action_id, a.agent)],
    [MONO, (a) => a.command],
    [N, (a) => a.ticket],
    [C, (a) => statusBadge(a.status)],
    [MONO, (a) => a.detail || '—'],
    [C, (a) => (a.status === 'PUBLISHED' ? 'waiting' : utc(a.updated_at))],
  ];
  const STEP_LABELS = ['no step yet', 'TP1 reached: stop at SL+ 1', 'TP2 reached: stop at SL+ 2'];
  const level = (value) => (isNumber(value) && value > 0 ? num(value) : '—');

  function renderPlan(plan) {
    const node = byId('v6-plan');
    if (!plan) {
      node.replaceChildren(el('p', 'text-xs text-slate-500', 'No active V6 intent.'));
      return;
    }
    node.replaceChildren(pairs([
      ['Intent', group(plan.intent_id, statusBadge(plan.status))],
      ['Order', group(sideBadge(plan.side), plan.order_type)],
      ['Entry · SL · TP3', level(plan.entry) + ' · ' + level(plan.sl) + ' · ' + level(plan.tp)],
      ['TP1 → SL+ 1', level(plan.tp1) + ' → ' + level(plan.sl_after_tp1)],
      ['TP2 → SL+ 2', level(plan.tp2) + ' → ' + level(plan.sl_after_tp2)],
      ['Step', STEP_LABELS[plan.plan_step] || String(plan.plan_step)],
      ['Time limit', Math.round((plan.time_barrier_s || 0) / 60) + ' min from the open'],
    ]));
  }

  function renderOpenOrders(orders) {
    setText('v6-orders-asof', orders.available ? 'as of ' + utc(orders.as_of_epoch) : 'no snapshot yet');
    renderTable('v6-positions', POSITION_COLUMNS, orders.positions, 'No open V6 position.');
    renderTable('v6-orders', ORDER_COLUMNS, orders.pending_orders, 'No pending V6 order.');
  }

  function statTile(label, value, sub, tone) {
    const box = el('div', 'bg-slate-900/80 px-5 py-4');
    box.append(el('p', 'text-[11px] font-medium uppercase tracking-wider text-slate-500', label),
      el('p', 'mt-1 tabular text-2xl font-semibold ' + (TEXT_TONES[tone] || 'text-slate-100'), value),
      el('p', 'mt-1 text-[11px] text-slate-500', sub));
    return box;
  }

  function outcomeNote(stats, invalid) {
    const notes = [];
    if (stats.unlinked) notes.push(stats.unlinked + ' result(s) name no known intent and carry no R.');
    if (invalid.length) {
      notes.push(invalid.length + ' unreadable result(s) skipped: ' + invalid.join(', ') + '.');
    }
    notes.push('Averages stay uncoloured below n = ' + MIN_TONED_SAMPLE + '; knowledge/15 asks for ' +
      't > 3 over a large n before any edge is trusted.');
    return notes.join(' ');
  }

  function renderOutcomes(outcomes) {
    const s = outcomes.stats || {};
    const t = isNumber(s.t_r) ? ' · t = ' + s.t_r.toFixed(2) : '';
    const rate = isNumber(s.win_rate_pct) ? s.win_rate_pct.toFixed(1) + '%' : '—';
    setText('v6-outcome-window', outcomes.window_days + ' days');
    const split = (s.wins ?? 0) + ' won · ' + (s.losses ?? 0) + ' lost · ' + (s.breakeven ?? 0) + ' flat';
    R.fill('v6-outcome-stats',
      statTile('Closed', String(s.n ?? 0), split),
      statTile('Win rate', rate, count((s.wins ?? 0) + (s.losses ?? 0)) + ' decided'),
      statTile('Average R', signedR(s.avg_r), count(s.n_r) + t, sampleTone(s.avg_r, s.n_r)),
      statTile('Net P&L', money(s.total_pnl), count(s.n), pnlTone(s.total_pnl)),
      statTile('Label R', signedR(s.avg_label_r), count(s.n_label) + ' chosen candidates'),
      statTile('Execution gap', signedR(s.avg_r_gap), count(s.n_gap) + ' · realised minus label',
        sampleTone(s.avg_r_gap, s.n_gap)));
    setText('v6-outcome-note', outcomeNote(s, outcomes.invalid || []));
    renderTable('v6-outcomes', OUTCOME_COLUMNS, outcomes.recent, 'No V6 position has closed yet.');
  }

  // --- refresh loop --------------------------------------------------------
  function render(data) {
    if (!R) throw new Error(RENDERER_MISSING);
    renderStatus(data.runtime);
    renderEa(data.ea);
    renderSession(data.session);
    renderSizing(data.sizing);
    renderOperator(data.session, data.runtime);
    renderOutcomes(data.outcomes);
    renderTable('v6-intents', INTENT_COLUMNS, data.intents,
      'No intent published yet. Shadow mode never publishes one.');
    renderTable('v6-executions', EXECUTION_COLUMNS, data.executions, 'No execution report yet.');
    renderOpenOrders(data.open_orders);
    renderPlan(data.plan);
    renderTable('v6-actions', ACTION_COLUMNS, data.actions, 'No management action yet.');
    R.renderGates(data.last_cycle);
    R.renderBreakers(data.breakers);
    R.renderRealised(data.outcomes.realised);
    R.renderHoldReasons(data.hold_reasons_7d);
    R.renderCycles(data.recent_cycles);
    R.renderLabels(data.label_stats);
  }

  function showDisabled(disabled) {
    byId('v6-disabled').hidden = !disabled;
    byId('v6-halt').disabled = disabled || !csrfNonce;
    if (disabled) {
      const status = byId('v6-status');
      status.textContent = 'DISABLED';
      status.className = PILL + (TONES.slate || '');
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
