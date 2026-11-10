// Redline Referee: static front end. Everything shown comes from one review.json per review.
// No framework, no build step. All URLs are relative so the app works from any sub-path.

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false || k === '') continue;
    el.append(k.nodeType ? k : String(k));
  }
  return el;
}

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style') el.style.cssText = v;
      else if (k === 'dataset') for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = String(dv);
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  return append(el, kids);
}

const SVG = (d) => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const ICONS = {
  check: SVG('<path d="M3.5 8.5l3 3 6-7"/>'),
  chevron: SVG('<path d="M4 6l4 4 4-4"/>'),
  up: SVG('<path d="M4 10l4-4 4 4"/>'),
  down: SVG('<path d="M4 6l4 4 4-4"/>'),
  download: SVG('<path d="M8 2.5v8m0 0l-3-3m3 3l3-3M3 13.5h10"/>'),
  upload: SVG('<path d="M8 10.5v-8m0 0l-3 3m3-3l3 3M3 13.5h10"/>'),
  alert: SVG('<path d="M8 2.5l6 10.5H2L8 2.5z"/><path d="M8 6.8v2.7"/><path d="M8 11.3v.1"/>'),
  stop: SVG('<circle cx="8" cy="8" r="5.5"/><path d="M4.2 11.8l7.6-7.6"/>'),
  dots: SVG('<path d="M4.5 8h.1M8 8h.1M11.5 8h.1"/>'),
  open: SVG('<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.2M8 10.8v.1"/>'),
  copy: SVG('<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/>'),
  x: SVG('<path d="M4 4l8 8M12 4l-8 8"/>'),
  minus: SVG('<path d="M3.5 8h9"/>'),
  plus: SVG('<path d="M3.5 8h9M8 3.5v9"/>'),
  arrow: SVG('<path d="M3.5 8h9m0 0L9 4.5M12.5 8L9 11.5"/>'),
  play: SVG('<path d="M5.2 3.4v9.2L12.6 8 5.2 3.4z" fill="currentColor" stroke-linejoin="round"/>'),
};
function ico(name) {
  const s = h('span', { class: 'ico' });
  s.innerHTML = ICONS[name] ?? '';            // constant strings defined above, never data
  return s;
}

// One rule for every probability on the page: a whole percent, one decimal from 99.5% up,
// and 100% only when the value is exactly 1 (0.9965 reads 99.7%, 0.99999 reads 99.9%).
function pct(x) {
  if (typeof x !== 'number' || !Number.isFinite(x)) return null;
  if (x >= 1) return '100%';
  if (x >= 0.995) return `${Math.min(99.9, Math.round(x * 1000) / 10).toFixed(1)}%`;
  return `${Math.round(x * 100)}%`;
}
const words = (x) => String(x ?? '').replace(/_/g, ' ');
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many ?? one + 's')}`;
// Sentences written by the engine may still say "3 statement(s)": show the right number form.
const tidy = (t) => String(t ?? '')
  .replace(/(\d+)((?:\s+[A-Za-z-]+){0,2}?)\s+([A-Za-z]+)\(s\)/g, (m, n, mid, noun) => `${n}${mid} ${noun}${Number(n) === 1 ? '' : 's'}`)
  .replace(/\(s\)/g, 's');
const joinAnd = (xs) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs.at(-1)}`);
// "Clocked Compare, return_pdf=highlight" is an API detail. Say who marked the copy, in words.
function byWords(by) {
  const s = String(by ?? '').trim();
  const m = /^(Clocked \w+)\s*,\s*return_pdf=(\w+)/.exec(s);
  if (m) return `${m[2] === 'box' ? 'boxed' : 'marked'} by ${m[1]}`;
  return s.replace(/,?\s*\b\w+=[\w.-]+/g, '').trim();
}
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const arr = (x) => (Array.isArray(x) ? x : []);
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const narrow = () => !!window.matchMedia?.('(max-width: 899px)').matches;   // phone and small tablet: one column
const store = {
  get(k) { try { return window.localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { window.localStorage.setItem(k, v); } catch { /* private mode: not remembered */ } },
};
const fmtClock = (d) => { try { return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch { return ''; } };

async function getJSON(url) {
  const res = await fetch(url, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${url} answered HTTP ${res.status}`);
  return res.json();
}

// ------------------------------------------------------------------- labels
const STATUSES = ['ready', 'changes', 'blocked', 'incomplete'];
const HEADLINES = { ready: 'Ready to sign', changes: 'Changes to resolve before signing', blocked: 'Do not sign', incomplete: 'Review incomplete' };
const STATUS_ICON = { ready: 'check', changes: 'open', blocked: 'stop', incomplete: 'dots' };
const LEVEL_TONE = { block: 't-red', open: 't-amber', incomplete: 't-grey', ok: 't-green' };
const CLASSES = {
  substantive: 'Substantive', reworded: 'Reworded, same meaning', administrative: 'Administrative',
  spelling: 'Spelling', formatting: 'Formatting',
};
const CLASS_TONE = { substantive: 't-amber', reworded: 't-accent', administrative: 't-grey', spelling: 't-grey', formatting: 't-grey' };
const ROUTES = { accept: 'Accept', counter: 'Counter', escalate: 'Escalate' };
const ROUTE_DONE = { accept: 'Accepted', counter: 'Countered', escalate: 'Escalated' };
const ROUTE_TONE = { accept: 't-green', counter: 't-amber', escalate: 't-ink' };
const ROUTE_KEY = { accept: 'A', counter: 'C', escalate: 'E' };
const VERDICTS = { supported: 'Supported', contradicted: 'Contradicted', not_stated: 'Not stated', not_a_claim: 'Not a factual claim', not_checked: 'Not checked' };
const VERDICT_TONE = { supported: 't-green', contradicted: 't-red', not_stated: 't-grey', not_a_claim: 't-grey', not_checked: 't-grey' };
const VERDICT_ORDER = ['contradicted', 'supported', 'not_stated', 'not_a_claim', 'not_checked'];
const RELATIONS = { conflict: 'Conflict', same: 'Same', restated: 'Same', unpaired: 'Unpaired', silent: 'Not flagged', not_run: 'Not run' };
const RELATION_TEXT = {
  conflict: 'The two versions conflict', same: 'Same statement', restated: 'Same statement',
  unpaired: 'Compare could not pair this passage', silent: 'Compare did not flag this', not_run: 'Not run',
};
const RELATION_TONE = { conflict: 't-amber', same: 't-green', restated: 't-green', unpaired: 't-grey', silent: 't-grey', not_run: 't-grey' };
const VERIFY_READS = {
  changed: ['Reads as a different meaning', 't-amber'], same: ['Reads as the same meaning', 't-green'], unclear: ['Unclear', 't-grey'],
};
const MODES = { replay: 'Replay of recorded Clocked responses', live: 'Live', 'rules-only': 'Rules only, Clocked not run' };
const MODES_SHORT = { replay: 'Recorded replay', live: 'Live', 'rules-only': 'Rules only' };
const GROUPS = [
  { key: 'resolve', title: 'To resolve', test: (c) => !!c.blocking },
  { key: 'reworded', title: 'Reworded, same meaning', test: (c) => c.cls === 'reworded' },
  { key: 'admin', title: 'Administrative', test: (c) => c.cls === 'administrative' },
  { key: 'minor', title: 'Spelling and formatting', test: (c) => c.cls === 'spelling' || c.cls === 'formatting', collapsed: true },
  { key: 'other', title: 'Other differences', test: () => true },
];
const ZOOMS = [1, 1.25, 1.5, 2, 2.5, 3];

// -------------------------------------------------------------------- state
const S = {
  index: [],            // demo cases from cases/index.json
  runs: [],             // reviews uploaded in this session
  live: false,
  base: '', caseId: null, run: false, review: null,
  sel: null,            // change id, 'guard' or null
  claim: null,          // index of the active cover-note statement
  tab: 'changes',
  open: {},             // group key -> open?
  decisions: new Map(), // base -> { changeId: action }
  outcomes: new Map(),  // base -> { kind: 'signed' | 'sent', at: Date }
  noIntro: false,       // the URL asked for no "start here" strip (intro=0)
  warnOff: new Set(),   // bases whose warnings strip was dismissed
  replies: new Map(),   // base -> { status, data }: the recorded reply.json, fetched when the summary opens
  drafts: new Map(),    // base|decisions -> { status, data, error }: replies drafted live
  zoom: 1, zoomAuto: true, view: 'both', wasBoth: false, layer: 'changes',
  drawerOpen: false, drawerTab: 'endpoints',
  panes: {},
  loadToken: 0,
};
const sync = { progUntil: 0, offset: 0, last: 'v1', timer: 0 };

const changesOf = (r = S.review) => arr(r?.changes);
const blockingOf = (r = S.review) => changesOf(r).filter((c) => c.blocking);
const changeById = (id) => changesOf().find((c) => c.id === id) ?? null;
const numOf = (c) => changesOf().indexOf(c) + 1;
const clsOf = (c) => (CLASSES[c.cls] ? c.cls : 'other');
const clauseOf = (c) => ((c.clause_label ?? c.clause) ? `§ ${c.clause_label ?? c.clause}` : '');     // the label may read "14 → 15"
// The document-level Compare result. Newer reviews nest it under compare.document.
const cmpDoc = (r = S.review) => r?.compare?.document ?? r?.compare ?? null;
function layersOf(r = S.review) {
  const out = [];
  for (const side of ['v1', 'v2']) {
    for (const l of arr(r?.documents?.[side]?.layers)) {
      if (l?.id && arr(l.pages).length && !out.some((x) => x.id === l.id)) out.push({ id: String(l.id), label: l.label ?? String(l.id), by: l.by ?? '' });
    }
  }
  return out;
}
function decisions() {
  if (!S.decisions.has(S.base)) S.decisions.set(S.base, {});
  return S.decisions.get(S.base);
}
function statusOf(r) {
  if (!r) return 'incomplete';
  if (arr(r.errors).length && r.status !== 'blocked') return 'incomplete';   // never green when a step failed
  return STATUSES.includes(r.status) ? r.status : 'incomplete';
}
function guardInfo(r = S.review) {
  const flagged = arr(r?.guard).filter((g) => g.flagged);
  const hidden = arr(r?.hidden_text);
  return flagged.length || hidden.length ? { flagged, hidden } : null;
}
// Why the rule layer calls a passage hidden. Newer reviews say it in words (`reason_text`).
const hiddenReason = (x) => {
  const reason = x && typeof x === 'object' ? x.reason : x;
  if (x && typeof x === 'object' && x.reason_text) return String(x.reason_text);
  return reason === 'white' ? 'white text' : reason === 'tiny' ? 'under 4 pt' : words(reason ?? 'hidden');
};

// --------------------------------------------------------------------- hash
function readHash() {
  const p = new URLSearchParams(location.hash.replace(/^#/, ''));
  return { case: p.get('case'), run: p.get('run'), change: p.get('change'), view: p.get('view'), tab: p.get('tab'), intro: p.get('intro') };
}
function writeHash() {
  const p = new URLSearchParams();
  if (S.run) p.set('run', S.base); else if (S.caseId) p.set('case', S.caseId);
  if (S.sel === 'guard') p.set('view', 'guard'); else if (S.sel) p.set('change', S.sel);
  if (S.tab && S.tab !== 'changes') p.set('tab', S.tab);       // optional, so a link can open on the cover note
  if (S.noIntro) p.set('intro', '0');
  const next = `#${p.toString()}`;
  if (next !== location.hash) history.replaceState(null, '', next);
}
function safeBase(b) {
  if (typeof b !== 'string' || !/^[A-Za-z0-9._~\-/]+$/.test(b) || b.startsWith('/') || b.includes('..') || b.includes('//')) return null;
  return b.endsWith('/') ? b : `${b}/`;
}

// ------------------------------------------------------------------ top bar
// A review recorded from live calls and shown on a static site is not "live" for the person looking at it.
function modeLabels(r) {
  if (r.mode === 'live' && !(S.live && S.run)) return ['Recorded from a live run on the Clocked API', 'Recorded live run'];
  const long = MODES[r.mode] ?? words(r.mode);
  const secs = typeof r.audit?.elapsed_ms === 'number' ? `${(r.audit.elapsed_ms / 1000).toFixed(1)} s` : null;
  if (r.mode === 'live' && secs) return [`Reviewed live in ${secs}`, `Reviewed live in ${secs}`];
  return [long, MODES_SHORT[r.mode] ?? long];
}
const START_CASES = ['quiet-changes', 'two-readers', 'instruction-in-note'];
function renderTop() {
  const r = S.review;
  const current = S.run ? S.runs.find((x) => x.base === S.base) : S.index.find((x) => x.id === S.caseId);
  $('#switcher-label').textContent = r?.case?.title ?? current?.title ?? 'Select a review';
  $('#switcher-dot').dataset.status = r ? statusOf(r) : (current?.status ?? '');
  const badge = $('#mode-badge');
  if (r?.mode) {
    const [long, short] = modeLabels(r);
    badge.hidden = false;
    badge.dataset.mode = r.mode === 'live' && !(S.live && S.run) ? 'replay' : r.mode;
    badge.title = long;
    badge.setAttribute('aria-label', long);
    badge.replaceChildren(h('span', { class: 'long' }, long), h('span', { class: 'short', 'aria-hidden': 'true' }, short));
  } else badge.hidden = true;

  // Clocked on the first screen: how many endpoints ran and how many calls. Opens the endpoint drawer.
  const pill = $('#clocked-pill');
  const used = arr(r?.endpoints).filter((e) => Number(e.calls) > 0).length;
  const calls = r?.audit?.calls ?? arr(r?.audit?.requests).length;
  pill.hidden = !(r && used && calls);
  if (!pill.hidden) {
    pill.title = 'What each endpoint decided in this review';
    pill.replaceChildren(h('span', null, h('b', null, used), ` Clocked ${used === 1 ? 'endpoint' : 'endpoints'}`), h('span', { class: 'pill-more' }, h('i', { class: 'sep' }, '·'), h('b', null, calls), ` ${calls === 1 ? 'call' : 'calls'}`));
  }

  const menu = $('#switcher-menu');
  const item = (entry, selected, onpick) => h('button', { type: 'button', class: 'switcher-item', role: 'option', 'aria-selected': selected ? 'true' : 'false', onclick: onpick },
    h('span', { class: 'dot', dataset: { status: selected && r ? statusOf(r) : (entry.status ?? '') } }),
    h('b', null, entry.title ?? entry.id),
    entry.description ? h('small', null, entry.description) : null);
  const demo = (c) => item(c, !S.run && c.id === S.caseId, () => { closeSwitcher(); openReview({ id: c.id, base: `cases/${c.id}/` }); });
  menu.replaceChildren();
  // Eight cases are easier to scan in two short groups: the three to start with, then the rest.
  const first = START_CASES.map((id) => S.index.find((c) => c.id === id)).filter(Boolean);
  const rest = S.index.filter((c) => !first.includes(c));
  if (first.length >= 2 && rest.length) {
    menu.append(h('div', { class: 'switcher-group' }, 'Start with these'), ...first.map(demo), h('div', { class: 'switcher-group' }, 'More cases'), ...rest.map(demo));
  } else if (S.index.length) {
    menu.append(h('div', { class: 'switcher-group' }, 'Demo reviews'), ...S.index.map(demo));
  }
  if (S.runs.length) {
    menu.append(h('div', { class: 'switcher-group' }, 'Your uploads'));
    for (const c of S.runs) menu.append(item(c, S.run && c.base === S.base, () => { closeSwitcher(); openReview({ base: c.base, run: true }); }));
  }
  if (!S.index.length && !S.runs.length) menu.append(h('div', { class: 'switcher-group' }, 'No reviews available'));
}
function closeSwitcher() {
  $('#switcher-menu').hidden = true;
  $('#switcher-btn').setAttribute('aria-expanded', 'false');
}
// live === true: the server can review uploads. live === false: a static replay, so offer the video
// and the instructions instead of a dead button. null: not known yet, show neither.
function setLive(live) {
  S.live = live === true;
  $('#upload-wrap').hidden = live !== true;
  $('#cta-static').hidden = live !== false;
  if (S.review) renderTop();
}

// ---------------------------------------------------------------- start here
const MOMENTS = [
  { key: 'number', label: 'A changed number', case: 'quiet-changes' },
  { key: 'rewording', label: 'A faithful rewording', case: 'quiet-changes' },
  { key: 'readers', label: 'Two readers, one disagreement', case: 'two-readers' },
  { key: 'note', label: 'A false cover note', case: 'quiet-changes' },
  { key: 'guard', label: 'A note that talks to your AI', case: 'instruction-in-note' },
  { key: 'wrong', label: 'The wrong file came back', case: 'wrong-file' },
];
function renderIntro() {
  const root = $('#intro');
  const have = MOMENTS.filter((m) => S.index.some((c) => c.id === m.case));
  root.hidden = S.noIntro || store.get('rr-intro') === 'off' || !have.length;
  if (root.hidden) { root.replaceChildren(); return; }
  const r = S.review;
  const claims = arr(r?.note?.claims);
  const paymentClaim = claims.findIndex((c) => c.verdict === 'contradicted' && /payment/i.test(c.text ?? ''));
  const ci = paymentClaim >= 0 ? paymentClaim : claims.findIndex((c) => c.verdict === 'contradicted');
  const claim = claims[ci];
  const held = changesOf(r ?? {}).filter((c) => c.blocking);
  const linked = arr(claim?.change_ids);
  const proof = held.find((c) => linked.includes(c.id)) ?? held[0];
  const evidence = claim && proof ? h('div', { class: 'intro-evidence' },
    h('div', { class: 'intro-said' }, h('span', { class: 'intro-label' }, 'The cover note'), h('q', null, claim.text)),
    h('span', { class: 'intro-vs', 'aria-hidden': 'true' }, '→'),
    h('div', { class: 'intro-found' }, h('span', { class: 'intro-label' }, `The returned copy · ${clauseOf(proof)}`), h('strong', null, proof.summary)),
    h('button', { type: 'button', class: 'btn btn--primary intro-action', onclick: () => { S.tab = 'changes'; select(proof.id); if (narrow()) reveal($('#detail')); } }, 'Inspect this change', ico('arrow'))) :
    h('div', { class: 'intro-orientation' }, h('b', null, 'Know what you are being asked to sign.'), h('span', null, 'Compare both copies. Check the cover note. Decide each change.'));
  root.replaceChildren(
    h('b', { class: 'intro-kicker' }, 'A second look before you sign'),
    evidence,
    h('details', { class: 'intro-explore' }, h('summary', null, 'Explore the recorded cases'),
      h('div', { class: 'intro-links' }, have.map((m) => h('button', { type: 'button', class: 'intro-btn', dataset: { moment: m.key }, onclick: () => goMoment(m.key) }, m.label, ico('arrow'))))),
    h('button', { type: 'button', class: 'icon-btn intro-close', 'aria-label': 'Dismiss this strip', title: 'Dismiss', onclick: () => { store.set('rr-intro', 'off'); root.hidden = true; root.replaceChildren(); } }, ico('x')));

}
function goMoment(key) {
  const m = MOMENTS.find((x) => x.key === key);
  return m ? openReview({ id: m.case, base: `cases/${m.case}/`, moment: key }) : null;
}

// ------------------------------------------------------------- verdict band
const outcomeOf = () => S.outcomes.get(S.base) ?? null;
// The whole-document Compare is a gate. When it says the two files are not versions of one agreement,
// the review stops there: no changes, no cover note check, nothing to approve.
const stopped = (r = S.review) => r?.compare?.gate?.stopped === true || (r?.headline === 'Not the same agreement' && !changesOf(r).length);
const OUTCOME_TEXT = { sent: 'Sent to approvers', signed: 'Signed off', asked: 'Asked for the right file' };
function gate(r) {
  const st = statusOf(r);
  const blocking = blockingOf(r);
  const d = decisions();
  const decided = blocking.filter((c) => d[c.id]).length;
  const left = blocking.length - decided;
  const base = { st, blocking, decided, left };
  if (stopped(r)) {
    return { ...base, ok: true, kind: 'asked', label: 'Ask for the right file', why: 'There is nothing to approve: the file that came back is not a version of the agreement we sent.' };
  }
  if (st === 'blocked') {
    return { ...base, ok: true, kind: 'sent', label: 'Send to approvers', why: 'Cannot be signed as returned. The approvers get the findings and your decisions.' };
  }
  if (st === 'incomplete') {
    const n = arr(r.errors).length;
    return { ...base, ok: true, kind: 'sent', label: 'Send to approvers', why: `${n ? plural(n, 'step') : 'A step'} did not complete, so this cannot be signed off. The approvers get what was found.` };
  }
  if (st === 'changes' && left > 0) {
    return { ...base, ok: false, kind: 'signed', label: 'Sign off', why: `Sign-off opens when every change to resolve has a decision. ${plural(left, 'change')} left.` };
  }
  if (st === 'changes') {
    const up = blocking.filter((c) => d[c.id] === 'escalate').length;
    return up
      ? { ...base, ok: true, kind: 'sent', label: 'Send to approvers', why: `Every change has a decision. ${plural(up, 'change')} ${up === 1 ? 'goes' : 'go'} to another approver before anyone signs.` }
      : { ...base, ok: true, kind: 'signed', label: 'Sign off', why: 'Every change to resolve has a decision.' };
  }
  return { ...base, ok: true, kind: 'signed', label: 'Sign off', why: 'No blocking finding. Nothing to resolve.' };
}

const SOURCE_NAMES = { Rules: 'the rule layer (plain code, no model)', Verify: 'Clocked Verify', Compare: 'Clocked Compare', Guard: 'Clocked Guard', Rerank: 'Clocked Rerank', Decide: 'Clocked Decide', Extract: 'Clocked Extract', Fill: 'Clocked Fill' };
const SOURCE_TIPS = {
  Verify: 'Clocked Verify checked each statement of the cover note against the documents.',
  Guard: 'Clocked Guard looked for text addressed to an AI reviewer.',
  Rules: 'The rule layer is plain code that compares the text of the two PDFs. No model is involved.',
  Compare: 'Clocked Compare read the two PDFs.',
  'All checks': 'The rule layer, Verify, Compare and Guard all ran and found nothing that blocks signing.',
};
function sourceTip(src) {
  const s = String(src ?? '');
  if (SOURCE_TIPS[s]) return SOURCE_TIPS[s];
  const parts = s.split('+').map((x) => x.trim()).filter(Boolean);
  if (parts.length > 1) return `Read by ${joinAnd(parts.map((x) => SOURCE_NAMES[x] ?? x))}.`;
  return SOURCE_NAMES[s] ? `From ${SOURCE_NAMES[s]}.` : null;
}
// The small print under the reasons says who read what, from the review itself: it must not claim more than happened.
const asked = (x) => x != null && x !== 'not_asked' && x !== 'not_read' && x !== 'not_run';
const clockedRead = (c) => (c.readers ? asked(c.readers.verify) || asked(c.readers.compare) : !!(c.verify || c.compare));
function reasonsLegend(r) {
  if (stopped(r)) return 'Compare read the two whole files first. Nothing else was sent to Clocked.';
  const out = [];
  if (r.note?.ran) out.push('Verify checked the cover note.');
  if (guardInfo(r) && arr(r.guard).some((g) => g.ran)) out.push('Guard scanned both texts for instructions to an AI.');
  const open = blockingOf(r), rest = changesOf(r).filter((c) => !c.blocking);
  if (open.length && open.every(clockedRead)) out.push('Every change to resolve was read by rules and by Clocked.');
  const alone = [['formatting', 'formatting'], ['administrative', 'addresses'], ['spelling', 'spelling']]
    .filter(([cls]) => rest.some((c) => c.cls === cls) && rest.filter((c) => c.cls === cls).every((c) => !clockedRead(c))).map(([, word]) => word);
  if (alone.length) {
    const s = joinAnd(alone);
    out.push(`${s.charAt(0).toUpperCase()}${s.slice(1)} ${alone.length === 1 && alone[0] !== 'addresses' ? 'is' : 'are'} settled by rules alone.`);
  }
  return out.join(' ');
}
function firstContradicted(r, linked = false) {
  const claims = arr(r?.note?.claims);
  const has = (c) => arr(c.change_ids).some((id) => changesOf(r).some((x) => x.id === id));
  let i = linked ? claims.findIndex((c) => c.verdict === 'contradicted' && has(c)) : -1;
  if (i < 0) i = claims.findIndex((c) => c.verdict === 'contradicted');
  return i;
}
function reasonText(x, r) {
  let t = tidy(x.text);
  if (/guard/i.test(String(x.source ?? ''))) {
    // The engine rounds this to "100%"; every other place shows the measured value. Keep them the same.
    const g = arr(r.guard).find((y) => y.flagged && y.target && t.startsWith(y.target)) ?? arr(r.guard).find((y) => y.flagged);
    if (g && pct(g.injection)) t = t.replace(/probability \d+(?:\.\d+)?%/, `probability ${pct(g.injection)}`);
  }
  return t;
}
// Where a reason leads: the evidence behind it.
function reasonAction(x, r) {
  const src = String(x.source ?? '');
  if (stopped(r)) return null;
  if (x.level === 'open') {
    const c = blockingOf(r)[0];
    return c ? { title: 'Show the first change to resolve', run: () => { S.tab = 'changes'; select(c.id); } } : null;
  }
  if (x.level === 'block' && /guard|rules/i.test(src) && guardInfo(r)) return { title: 'Show the text and where it sits', run: () => { S.tab = 'changes'; select('guard'); } };
  if (/verify/i.test(src) && arr(r.note?.claims).length) return { title: 'Open the cover note at the first contradicted statement', run: () => openNote(firstContradicted(r)) };
  if (x.level === 'ok') return { title: 'See what each check did', run: () => openDrawer('endpoints') };
  return null;
}
function openNote(i) {
  S.tab = 'note';
  if (i != null && i >= 0) pickClaim(i, { reveal: false }); else renderLeft();
  if (narrow()) reveal($('.left'));
}

function renderVerdict() {
  const r = S.review;
  const root = $('#verdict');
  if (!r) return;
  const g = gate(r);
  $('#app').dataset.status = g.st;
  const headline = g.st === r.status && r.headline ? r.headline : HEADLINES[g.st];

  const main = h('div', { class: 'verdict-main' },
    h('span', { class: 'verdict-glyph' }, ico(STATUS_ICON[g.st])),
    h('h1', { class: 'verdict-headline' }, headline),
    h('p', { class: 'verdict-case' }, h('b', null, r.case?.title ?? 'Review'), r.case?.description ? ` · ${r.case.description}` : ''));

  const reasons = h('div', { class: 'reasons' });
  let list = arr(r.reasons);
  const errs = arr(r.errors);
  if (errs.length && list.filter((x) => x.level === 'incomplete').length > 1) {
    // Each failed step is listed in the banner below; one row here is enough.
    const names = [...new Set(errs.map((e) => e.endpoint).filter(Boolean))];
    list = list.filter((x) => x.level !== 'incomplete').concat([{ level: 'incomplete', source: 'Incomplete', text: `${plural(errs.length, 'step')} did not complete${names.length ? ` (${names.join(', ')})` : ''}. Each one is listed below.` }]);
  }
  if (!list.length) list = [{ level: 'none', source: 'Review', text: 'The review returned no reasons.' }];
  for (const x of list) {
    const tone = g.st !== r.status && x.level === 'ok' ? 't-grey' : (LEVEL_TONE[x.level] ?? 't-grey');
    const act = reasonAction(x, r);
    // The arrow stays with the last word, so it never wraps onto a line of its own.
    const text = reasonText(x, r), cutAt = text.lastIndexOf(' ');
    const body = act ? [text.slice(0, cutAt + 1), h('span', { class: 'nowrap' }, text.slice(cutAt + 1), ico('arrow'))] : text;
    const kids = [h('span', { class: `tag ${tone}`, title: sourceTip(x.source) }, x.source ?? 'Review'), h('span', { class: 'reason-text' }, body)];
    reasons.append(act ? h('button', { type: 'button', class: 'reason is-link', title: act.title, onclick: act.run }, kids) : h('div', { class: 'reason' }, kids));
  }
  const legend = reasonsLegend(r);

  const total = g.blocking.length;
  const progress = h('div', { class: 'progress' },
    h('span', { class: 'progress-text num' }, stopped(r) ? 'Nothing was reviewed' : total ? `${g.decided} of ${plural(total, 'change')} resolved` : 'No changes to resolve'),
    total ? h('span', { class: 'progress-bar', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': total, 'aria-valuenow': g.decided }, h('i', { style: `width:${(g.decided / total) * 100}%` })) : null);

  // After sign-off or send the band keeps saying so, with the time, until it is undone.
  const out = outcomeOf();
  const btn = out
    ? h('button', { type: 'button', class: 'btn', id: 'signoff-btn', onclick: () => openSummary() }, out.kind === 'asked' ? 'Open message' : 'Open summary')
    : h('button', { type: 'button', class: 'btn btn--primary', id: 'signoff-btn', disabled: !g.ok, onclick: () => openSummary() }, g.ok && g.kind === 'signed' ? ico('check') : null, g.label);
  const line = out
    ? h('p', { class: 'signoff-done' }, ico('check'), h('b', null, OUTCOME_TEXT[out.kind] ?? 'Done'), ` at ${fmtClock(out.at)}`, h('button', { type: 'button', class: 'link-btn', onclick: undoOutcome }, 'Undo'))
    : h('p', { class: 'signoff-why' }, g.why);

  root.replaceChildren(main,
    h('div', { class: 'verdict-mid' }, reasons, legend ? h('p', { class: 'reasons-legend' }, legend) : null),
    h('div', { class: 'signoff' }, progress, h('div', { class: 'signoff-actions' }, btn), line));
}

function renderErrors() {
  const root = $('#errors');
  const errs = arr(S.review?.errors);
  root.hidden = !errs.length;
  if (!errs.length) root.replaceChildren();
  else root.replaceChildren(ico('alert'), h('div', null,
    h('h3', null, `Review incomplete: ${plural(errs.length, 'step')} failed. Findings below may be missing.`),
    h('ul', null, errs.map((e) => h('li', null, h('b', null, `${e.endpoint ?? 'Step'} · ${e.step ?? 'unknown step'}`), ` ${e.message ?? 'failed'} `, e.kind ? h('code', null, e.kind) : null)))));
  renderWarnings();
}
// Things the review wants the reader to know that do not stop it. A note that only says an optional layer of
// marked pages is absent is not about the findings: it goes with the endpoint table, not in the banner.
function warningsOf(r) {
  const banner = [], quiet = [];
  for (const w of arr(r?.warnings)) {
    const text = tidy(typeof w === 'string' ? w : w?.text).trim();
    if (!text) continue;
    const src = w && typeof w === 'object' && w.source ? String(w.source) : 'Note';
    (/\bmarked cop(y|ies)\b|\blayer is not shown\b/i.test(text) ? quiet : banner).push([src, text]);
  }
  return { banner, quiet };
}
function renderWarnings() {
  const root = $('#warnings');
  const r = S.review;
  const lines = [];
  if (r && !S.warnOff.has(S.base)) {
    for (const [src, text] of warningsOf(r).banner) lines.push([text.startsWith(src) ? '' : src, text]);
    const sides = [['v1', 'the version we sent'], ['v2', 'the returned copy']];
    const pages = (obj, verb) => {
      const bits = sides.map(([k, name]) => (arr(obj?.[k]).length ? `${arr(obj[k]).length === 1 ? 'page' : 'pages'} ${arr(obj[k]).join(', ')} of ${name}` : null)).filter(Boolean);
      if (bits.length) lines.push(['Pages', `${verb}: ${joinAnd(bits)}.`]);
    };
    pages(r.unread_pages, 'Not read, so changes there would be missed');
    pages(r.ocr_pages, 'Read by OCR, so quotes there may be less exact');
  }
  root.hidden = !lines.length;
  if (!lines.length) { root.replaceChildren(); return; }
  root.replaceChildren(ico('alert'),
    h('ul', null, lines.map(([src, text]) => h('li', null, src ? h('b', null, `${src} `) : null, text))),
    h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Dismiss', title: 'Dismiss', onclick: () => { S.warnOff.add(S.base); renderWarnings(); } }, ico('x')));
}

// --------------------------------------------------------------- left panel
function renderLeft() {
  const r = S.review;
  const body = $('#left-body');
  if (!r) { body.replaceChildren(); return; }
  writeHash();
  const hadFocus = body.contains(document.activeElement);
  for (const t of $$('#left-tabs .tab')) t.setAttribute('aria-selected', String(t.dataset.tab === S.tab));

  const claims = arr(r.note?.claims);
  const bad = claims.filter((c) => c.verdict === 'contradicted').length;
  const toAi = arr(r.note?.instructions).length;
  const changedTerms = arr(r.terms).filter((t) => t.changed === true).length;
  const setCount = (id, text, tone) => { const el = $(id); el.textContent = text; el.className = `count ${tone ?? ''}`; };
  setCount('#count-changes', changesOf(r).length ? String(changesOf(r).length) : '');
  setCount('#count-note', bad + toAi ? String(bad + toAi) : (claims.length ? String(claims.length) : ''), bad + toAi ? 't-red' : '');
  setCount('#count-terms', changedTerms ? String(changedTerms) : '', changedTerms ? 't-amber' : '');
  $('#count-note').title = [bad ? plural(bad, 'contradicted statement') : null, toAi ? `${plural(toAi, 'sentence')} addressed to an AI` : null].filter(Boolean).join(', ');
  $('#count-terms').title = changedTerms ? `${plural(changedTerms, 'term')} changed` : '';

  const keep = body.dataset.tab === S.tab ? body.scrollTop : 0;
  body.dataset.tab = S.tab;
  if (stopped(r)) body.replaceChildren(stoppedLeft(r));
  else if (S.tab === 'note') body.replaceChildren(renderNote(r));
  else if (S.tab === 'terms') body.replaceChildren(renderTerms(r));
  else body.replaceChildren(...renderChanges(r));
  body.scrollTop = keep;
  const cur = $('.row[aria-current="true"]', body);
  if (cur) {
    scrollIntoViewIfNeeded(cur, body);
    if (hadFocus) cur.focus({ preventScroll: true });
  }
}
// The wrong file came back: say so in each tab instead of showing empty lists.
function stoppedLeft(r) {
  const pane = h('div', { class: 'pane' });
  if (S.tab === 'note') {
    const text = typeof r.note?.text === 'string' ? r.note.text.trim() : '';
    pane.append(h('div', { class: 'stop-note' }, h('b', null, 'The cover note was not checked'), 'The review stopped before it. A note about a different document has nothing to be checked against.'));
    if (text) pane.append(h('div', { class: 'letter' }, r.note.text));
  } else if (S.tab === 'terms') {
    pane.append(h('div', { class: 'stop-note' }, h('b', null, 'No key terms were read'), 'Comparing the terms of two different agreements would mean nothing, so Extract was not called.'));
  } else {
    pane.append(h('div', { class: 'stop-note' }, h('b', null, 'There is no list of changes'),
      'The two files are not versions of one agreement. Listing differences between them would mark every clause as removed or added, which would tell you nothing.'),
    h('div', { class: 'stop-steps' }, h('h3', { class: 'section-title' }, 'What happened'),
      h('ol', null,
        h('li', null, h('b', null, 'Compare read both files.'), ' One call, on the two whole PDFs.'),
        h('li', null, h('b', null, 'It did not find one agreement in two versions.'), pct(cmpDoc(r)?.related) ? ` Related: ${pct(cmpDoc(r).related)}.` : ''),
        h('li', null, h('b', null, 'The review stopped.'), ' No other endpoint was called.'))));
  }
  return pane;
}
function scrollIntoViewIfNeeded(el, box) {
  const a = el.getBoundingClientRect(), b = box.getBoundingClientRect();
  if (a.top < b.top + 34) box.scrollTop -= b.top + 34 - a.top;
  else if (a.bottom > b.bottom - 6) box.scrollTop += a.bottom - b.bottom + 6;
}

function groupOf(c) { return GROUPS.find((g) => g.test(c)); }

function renderChanges(r) {
  const out = [];
  const ch = changesOf(r);
  const section = (key, title, rows, collapsed, tone) => {
    const open = S.open[key] ?? !collapsed;
    return h('section', { class: 'group', dataset: { open } },
      h('button', { type: 'button', class: 'group-head', 'aria-expanded': String(open), onclick: () => { S.open[key] = !open; renderLeft(); } },
        ico('chevron'), h('span', { class: tone ?? '' , style: tone ? 'color:var(--t)' : null }, title), h('span', { class: 'n' }, rows.length)),
      h('div', { class: 'group-rows' }, rows));
  };

  const gi = guardInfo(r);
  if (gi) {
    const g0 = gi.flagged[0];
    const page = g0?.evidence?.find((e) => e.page)?.page ?? gi.hidden[0]?.page;
    const where = [g0?.target ?? 'Returned copy', page ? `page ${page}` : null].filter(Boolean).join(' · ');
    out.push(section('findings', 'Blocking findings', [
      h('button', { type: 'button', class: 'row', 'aria-current': S.sel === 'guard' ? 'true' : null, onclick: () => select('guard') },
        h('span', { class: 'pin-n c-guard' }, '!'),
        h('span', { class: 'row-main' },
          h('span', { class: 'row-top' }, h('span', { class: 'row-title' }, g0 ? 'Text addressed to an AI reviewer' : 'Hidden text in the returned copy')),
          h('span', { class: 'row-summary' }, where),
          h('span', { class: 'row-meta' },
            g0 ? h('span', { class: 'badge' }, `Guard ${pct(g0.injection) ?? ''}`.trim()) : null,
            gi.hidden.length ? h('span', { class: 'badge' }, `Rules: ${hiddenReason(gi.hidden[0])}`) : null))),
    ], false, 't-red'));
  }

  const buckets = new Map(GROUPS.map((g) => [g.key, []]));
  for (const c of ch) buckets.get(groupOf(c).key).push(c);
  for (const g of GROUPS) {
    const list = buckets.get(g.key);
    if (!list.length) continue;
    out.push(section(g.key, g.title, list.map(changeRow), g.collapsed));
  }

  const unplaced = arr(r.compare?.unplaced ?? cmpDoc(r)?.unplaced);
  if (unplaced.length) {
    out.push(h('section', { class: 'group', dataset: { open: true } },
      h('div', { class: 'group-head' }, h('span', null, 'Flagged by Compare, not matched to a clause'), h('span', { class: 'n' }, unplaced.length)),
      unplaced.map((u, i) => h('details', { class: 'unplaced' },
        h('summary', null, `Conflict ${i + 1}`, u.confidence != null ? ` · ${pct(u.confidence)}` : ''),
        h('div', null, comparePassages(u), u.aligned === false ? alignWarning() : null)))));
  }

  if (!out.length) {
    out.push(arr(r.errors).length
      ? h('div', { class: 'empty' }, h('b', null, 'No changes listed'), 'The review did not complete, so this list may be missing changes.')
      : h('div', { class: 'empty' }, h('b', null, 'No differences found'), 'The rule layer found no difference between the version we sent and the copy they returned.'));
  }
  return out;
}

function changeRow(c) {
  const d = decisions()[c.id];
  const route = c.route && ROUTES[c.route] ? h('span', { class: `chip ${ROUTE_TONE[c.route]}`, title: `Suggested route: ${ROUTES[c.route]}${c.route_reason ? `. ${c.route_reason}` : ''}` }, ROUTES[c.route]) : null;
  let state = null;
  if (d) state = h('span', { class: `state is-done ${ROUTE_TONE[d]}` }, ROUTE_DONE[d]);
  else if (c.blocking) state = h('span', { class: 'state' }, 'Undecided');
  return h('button', { type: 'button', class: 'row', 'aria-current': S.sel === c.id ? 'true' : null, dataset: { cid: c.id }, onclick: () => select(c.id) },
    h('span', { class: `pin-n c-${clsOf(c)}` }, numOf(c)),
    h('span', { class: 'row-main' },
      h('span', { class: 'row-top' }, clauseOf(c) ? h('span', { class: 'row-clause' }, clauseOf(c)) : null, h('span', { class: 'row-title' }, c.title ?? 'Untitled clause'), route),
      h('span', { class: 'row-summary' }, c.summary ?? ''),
      h('span', { class: 'row-meta' }, readerChips(c), disagreeMark(c), promotedMark(c), state)));
}
const FOUND = { both: 'Rules + Clocked', clocked: 'Clocked', rules: 'Rules' };
function foundBadge(c) {
  const raw = String(c.found_by ?? 'rules');
  const label = c.found_by_label || FOUND[raw] || `${raw.charAt(0).toUpperCase()}${words(raw.slice(1))}`;
  const strong = c.promoted === true || raw === 'clocked';
  return h('span', { class: `badge is-${strong ? 'clocked' : raw === 'both' ? 'both' : 'rules'}`, title: `Read by: ${label}` }, label);
}
// Who read this change, one small chip each. The Clocked endpoints stand out from the rule layer.
function readerChips(c) {
  const names = String(c.found_by_label ?? '').split('+').map((x) => x.trim()).filter(Boolean);
  if (!names.length || !names.every((n) => /^[A-Za-z]+$/.test(n))) return foundBadge(c);
  const rd = readersOf(c), bw = borrowed(c);
  const tip = (n) => {
    if (n === 'Verify' && !bw && VERIFY_SHORT[rd.verify]) return `Clocked Verify: ${VERIFY_SHORT[rd.verify][0]}.`;
    if (n === 'Compare' && !bw && COMPARE_SHORT[rd.compare]) return `Clocked Compare: ${COMPARE_SHORT[rd.compare][0]}${pct(c.compare?.confidence) ? `, ${pct(c.compare.confidence)}` : ''}.`;
    if (n === 'Rules') return 'The rule layer, plain code with no model, found this difference in the text.';
    return SOURCE_NAMES[n] ? `Read by ${SOURCE_NAMES[n]}.` : null;
  };
  return h('span', { class: 'readers' }, names.map((n) => h('span', { class: `reader${n === 'Rules' ? '' : ' is-clocked'}`, title: tip(n) }, n)));
}
function promotedMark(c) {
  return c.promoted === true ? h('span', { class: 'badge is-promoted', title: c.why ?? '' }, 'Promoted by Clocked') : null;
}
// The three readings of a change: the rule layer's class, and what Verify and Compare read. Older reviews carry
// no `readers`; there the readings come from the verify and compare blocks.
function readersOf(c) {
  const r = c.readers && typeof c.readers === 'object' ? c.readers : {};
  const cmp = c.compare?.relation === 'restated' ? 'same' : c.compare?.relation;
  return {
    rules: r.rules ?? c.cls ?? null,
    verify: r.verify ?? c.verify?.relation ?? 'not_asked',
    compare: r.compare ?? cmp ?? 'not_asked',
    agree: typeof r.agree === 'boolean' ? r.agree : null,
  };
}
const disagrees = (c) => !!c.blocking && c.readers?.agree === false;
const uncertainMeaning = (c) => !!c.blocking && c.readers?.compare === 'same' && c.readers?.verify !== 'same' && arr(c.parts).every((p) => p.kind === 'wording');
function disagreeMark(c) {
  return disagrees(c) ? h('span', { class: 'badge is-clash', title: 'The rule layer, Verify and Compare do not read this change the same way. The stricter reading wins.' }, 'readers disagree') : null;
}

// --------------------------------------------------------------- cover note
function codePointSlices(text, claims) {
  // Offsets come from Python (code points). Slice on code points, then check against the claim text.
  const cps = Array.from(text);
  const spans = [];
  claims.forEach((c, i) => {
    let a = c.start, b = c.end;
    let ok = Number.isInteger(a) && Number.isInteger(b) && a >= 0 && b > a && b <= cps.length;
    if (ok && c.text && cps.slice(a, b).join('') !== c.text) ok = false;
    if (!ok && c.text) {
      const at = text.indexOf(c.text);
      if (at >= 0) { a = Array.from(text.slice(0, at)).length; b = a + Array.from(c.text).length; ok = true; }
    }
    if (ok) spans.push({ a, b, i });
  });
  spans.sort((x, y) => x.a - y.a);
  const out = [];
  let pos = 0;
  for (const s of spans) {
    if (s.a < pos) continue;                   // overlapping span: keep the first
    if (s.a > pos) out.push({ text: cps.slice(pos, s.a).join('') });
    out.push({ text: cps.slice(s.a, s.b).join(''), i: s.i });
    pos = s.b;
  }
  if (pos < cps.length) out.push({ text: cps.slice(pos).join('') });
  return out;
}

function renderNote(r) {
  const note = r.note ?? {};
  const claims = arr(note.claims);
  const text = typeof note.text === 'string' ? note.text : '';
  // Sentences addressed to an AI are not statements about the agreement: Verify leaves them to Guard.
  const toAi = arr(note.instructions).filter((x) => x && typeof x.text === 'string' && x.text.trim());
  const pane = h('div', { class: 'pane' });
  if (!text.trim()) {
    pane.append(h('div', { class: 'empty' }, h('b', null, 'No cover note'), 'No cover note came with this copy, so there are no statements to check.'));
    return pane;
  }

  const counts = {};
  for (const c of claims) counts[c.verdict] = (counts[c.verdict] ?? 0) + 1;
  const stats = h('div', { class: 'pane-stats' });
  if (toAi.length) stats.append(h('span', { class: 'chip t-red' }, `${toAi.length} addressed to an AI`));
  if (note.ran) {
    for (const v of VERDICT_ORDER) {
      if (counts[v]) stats.append(h('span', { class: `chip ${VERDICT_TONE[v]}` }, `${counts[v]} ${VERDICTS[v].toLowerCase()}`));
    }
    if (!claims.length) stats.append(h('span', { class: 'pane-line' }, 'Verify found no statements to check.'));
  }
  const lines = [];
  if (note.ran) {
    const skipped = arr(note.skipped).length;
    lines.push(h('p', { class: 'pane-line' }, h('b', null, 'Verify'), ` checked ${plural(note.checked ?? claims.length, 'statement')} against the returned copy and the list of changes.`,
      skipped ? ` ${plural(skipped, 'other line')} (greeting, sign-off, question) ${skipped === 1 ? 'is' : 'are'} not statements.` : '',
      pct(note.grounded) ? h('span', { title: 'Verify\'s overall score for how much of the note the documents support.' }, ` Overall support for the note: ${pct(note.grounded)}.`) : null));
  } else {
    lines.push(h('div', { class: 'not-run' }, 'Verify did not run. The statements in this note were not checked.'));
  }
  const gNote = arr(r.guard).find((g) => g.target === 'Cover note');
  if (gNote) {
    lines.push(!gNote.ran
      ? h('p', { class: 'pane-line' }, h('b', null, 'Guard'), ' did not run on the cover note.')
      : gNote.flagged
        ? h('button', { type: 'button', class: 'callout t-red callout--link', onclick: () => select('guard', { jump: true }) }, ico('alert'), h('span', null, h('b', null, 'Guard: '), `text addressed to an AI reviewer${gNote.category ? ` (${words(gNote.category)})` : ''}, probability ${pct(gNote.injection) ?? 'unknown'}. Signing is blocked.`))
        : h('p', { class: 'pane-line' }, h('b', null, 'Guard'), ` found nothing addressed to an AI in the note${pct(gNote.injection) ? ` (probability ${pct(gNote.injection)})` : ''}.`));
  }
  pane.append(h('div', { style: 'display:grid;row-gap:8px' }, stats.childNodes.length ? stats : null, lines));

  // Mark the checked statements and the sentences addressed to an AI in the text of the note.
  const marks = (note.ran ? claims : []).map((c, i) => ({ text: c.text, start: c.start, end: c.end, claim: i }));
  toAi.forEach((x, i) => marks.push({ text: x.text, start: x.start, end: x.end, ai: i }));
  const segs = codePointSlices(text, marks);
  const letter = h('div', { class: 'letter' });
  for (const seg of segs) {
    if (seg.i == null) { letter.append(seg.text); continue; }
    const m = marks[seg.i];
    if (m.ai != null) {
      letter.append(h('span', { class: 'claim-span t-red v-ai', role: 'button', tabindex: '0', title: 'Addressed to an AI reviewer: handled by Guard',
        onclick: () => select('guard'), onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select('guard'); } } }, seg.text));
      continue;
    }
    const c = claims[m.claim];
    letter.append(h('span', {
      class: `claim-span ${VERDICT_TONE[c.verdict] ?? 't-grey'} v-${c.verdict}${S.claim === m.claim ? ' is-active' : ''}`,
      role: 'button', tabindex: '0', title: `Statement ${m.claim + 1}: ${VERDICTS[c.verdict] ?? words(c.verdict)}`, dataset: { claim: m.claim },
      onclick: () => pickClaim(m.claim), onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickClaim(m.claim); } },
    }, seg.text));
  }
  pane.append(letter);

  if (toAi.length) {
    pane.append(h('h3', { class: 'section-title' }, 'Addressed to an AI reviewer'));
    pane.append(h('div', { class: 'claims' }, toAi.map((x) => h('button', { type: 'button', class: 'claim t-red is-ai', title: 'Show the Guard finding', onclick: () => select('guard') },
      h('span', { class: 'claim-head' }, h('span', { class: 'chip t-red' }, 'Addressed to an AI reviewer'), h('span', { class: 'link' }, 'Handled by Guard')),
      h('span', { class: 'claim-text' }, x.text),
      h('span', { class: 'evidence' }, 'An instruction, not a statement about the agreement, so Verify does not check it. Guard does.')))));
  }
  if (claims.length) {
    pane.append(h('h3', { class: 'section-title' }, 'Statements'));
    pane.append(h('div', { class: 'claims' }, claims.map((c, i) => claimCard(c, i))));
  }
  return pane;
}

function claimCard(c, i) {
  const tone = VERDICT_TONE[c.verdict] ?? 't-grey';
  const p = pct(c.p?.[c.verdict]);
  const ids = arr(c.change_ids).filter((id) => changeById(id));
  const ev = c.evidence;
  const weak = c.verdict === 'not_stated' || c.verdict === 'not_a_claim';
  let evNode = null;
  if (ev?.quote) {
    const src = ev.document === 'v2' ? `the returned copy${ev.page ? `, page ${ev.page}` : ''}` : ev.document === 'changes' ? 'the list of changes' : (ev.document ?? 'the documents');
    // For a statement the documents do not speak to, the quote is only the nearest passage, not proof.
    evNode = h('div', { class: 'evidence' }, weak ? `Nearest passage, from ${src}. It does not settle the statement.` : `Evidence from ${src}`, h('q', null, ev.quote),
      ev.aligned === false ? h('div', { class: 'warn-line', style: 'margin-top:5px' }, ico('alert'), 'The quote does not match the text at its offsets.') : null);
  } else if (c.verdict !== 'not_checked') {
    evNode = h('div', { class: 'evidence' }, c.verdict === 'not_stated' ? 'Neither document says anything about this.' : c.verdict === 'not_a_claim' ? 'A greeting or a request, not something the documents can confirm or deny.' : 'No evidence quote was returned.');
  }
  return h('button', { type: 'button', class: `claim ${tone}${S.claim === i ? ' is-active' : ''}`, dataset: { claim: i }, onclick: () => pickClaim(i) },
    h('span', { class: 'claim-head' },
      h('span', { class: 'claim-n', title: `Statement ${i + 1}` }, i + 1),
      h('span', { class: `chip ${tone}` }, VERDICTS[c.verdict] ?? (c.verdict ? words(c.verdict) : 'Not checked')),
      p ? h('span', { class: 'p' }, p) : null,
      ids.length ? h('span', { class: 'link' }, `Shows ${ids.map((id) => clauseOf(changeById(id)) || id).join(', ')}`) : null),
    h('span', { class: 'claim-text' }, c.text ?? ''),
    evNode);
}

function pickClaim(i, { reveal: show = true } = {}) {
  const c = arr(S.review?.note?.claims)[i];
  if (!c) return;
  S.claim = i;
  S.tab = 'note';
  const ids = arr(c.change_ids).filter((id) => changeById(id));
  if (ids.length) {
    select(ids[0], { keepClaim: true, jump: false });
  } else {
    clearSpot();
    renderLeft();
    const ev = c.evidence;
    if (ev?.document === 'v2' && ev.page && arr(ev.rects).length) showSpot({ v2: { page: ev.page, rects: ev.rects } });
  }
  const body = $('#left-body');
  const card = $(`.claim[data-claim="${i}"]`, body);
  if (card) scrollIntoViewIfNeeded(card, body);
  if (show && narrow() && card) card.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'center' });
}

// ---------------------------------------------------------------- key terms
function renderTerms(r) {
  const terms = arr(r.terms);
  if (!terms.length) {
    return h('div', { class: 'empty' }, h('b', null, 'Key terms not read'), 'Extract did not run for this review, so there is no side-by-side table.');
  }
  const cell = (side) => {
    if (!side || side.read === false) return h('td', null, h('span', { class: 'term-none' }, 'Not read'));
    if (side.value == null || side.value === '') return h('td', null, h('span', { class: 'term-none' }, 'Not found'));
    const conf = pct(side.confidence);
    const low = typeof side.confidence === 'number' && side.confidence < 0.7;
    const sub = [side.page ? `p. ${side.page}` : null, conf ? (low ? `low confidence ${conf}` : conf) : null].filter(Boolean).join(' · ');
    return h('td', null, h('span', { class: 'term-val' }, String(side.value)), sub ? h('span', { class: `term-sub${low ? ' is-low' : ''}` }, sub) : null);
  };
  const rows = terms.map((t) => {
    const can = (t.v1?.page && arr(t.v1?.rects).length) || (t.v2?.page && arr(t.v2?.rects).length);
    const go = () => showSpot({
      v1: t.v1?.page && arr(t.v1.rects).length ? { page: t.v1.page, rects: t.v1.rects } : null,
      v2: t.v2?.page && arr(t.v2.rects).length ? { page: t.v2.page, rects: t.v2.rects } : null,
    });
    return h('tr', { class: `${t.changed === true ? 'is-changed' : ''}${can ? ' can-locate' : ''}`, tabindex: can ? '0' : null, title: can ? 'Show in the documents' : null, onclick: can ? go : null, onkeydown: can ? (e) => { if (e.key === 'Enter') go(); } : null },
      h('td', null, h('span', { class: 'term-label' }, t.label ?? t.field ?? ''),
        t.changed === true ? h('span', { class: 'term-flag' }, 'Changed') : t.changed == null ? h('span', { class: 'term-sub' }, 'Not compared') : null),
      cell(t.v1), cell(t.v2));
  });
  return h('table', { class: 'terms' },
    h('thead', null, h('tr', null, h('th', null, 'Term'), h('th', null, 'We sent'), h('th', null, 'They returned'))),
    h('tbody', null, rows));
}

// ---------------------------------------------------------------- documents
function renderDocs() {
  const r = S.review;
  const root = $('#docs');
  root.replaceChildren();
  S.panes = {};
  renderGate(r);
  if (!r) return;
  if (S.layer !== 'changes' && !layersOf(r).some((l) => l.id === S.layer)) S.layer = 'changes';
  if (narrow() && S.view === 'both') { S.view = 'v2'; S.wasBoth = true; syncViewButtons(); }   // one document at a time on a phone
  root.dataset.view = S.view;
  root.dataset.layer = S.layer;
  root.style.setProperty('--zoom', String(S.zoom));

  for (const side of ['v1', 'v2']) {
    const d = r.documents?.[side] ?? {};
    const name = d.name ?? (side === 'v1' ? 'The version we sent' : 'The copy they returned');
    // A layer is Clocked's own marked copy of the document, rendered as page images.
    const found = S.layer === 'changes' ? null : arr(d.layers).find((l) => l?.id === S.layer) ?? null;
    const lay = found && arr(found.pages).length ? found : null;
    const allPages = lay ? arr(lay.pages) : arr(d.pages);
    const pagesData = stopped(r) ? allPages.slice(0, 1) : allPages;     // the wrong file: the two first pages say enough
    const pages = pagesData.map((p, i) => {
      const layer = h('div', { class: 'page-layer' });
      const w = Number(p.width) > 0 ? Number(p.width) : 1020, ht = Number(p.height) > 0 ? Number(p.height) : 1320;
      const img = p.image ? h('img', { alt: `${name}, page ${i + 1}`, width: w, height: ht, decoding: 'async', draggable: 'false', loading: i === 0 ? 'eager' : 'lazy', fetchpriority: i === 0 ? 'high' : null }) : null;
      const el = h('div', { class: 'page', style: `aspect-ratio:${w} / ${ht}`, dataset: { page: i + 1 } }, img, layer);
      // The box is reserved from the start; the overlays only appear once the page under them has arrived.
      if (img) {
        img.addEventListener('load', () => el.classList.add('is-loaded'), { once: true });
        img.addEventListener('error', () => el.classList.add('is-loaded', 'is-missing'), { once: true });
        img.src = S.base + p.image;
        if (img.complete && img.naturalWidth) el.classList.add('is-loaded');
      } else el.classList.add('is-loaded', 'is-missing');
      return { el, layer, wrap: h('div', { class: 'page-wrap' }, h('div', { class: 'page-label' }, `Page ${i + 1} of ${allPages.length}`), el) };
    });
    const scroll = h('div', { class: 'doc-scroll', tabindex: '0', role: 'region', 'aria-label': name, dataset: { side } },
      pages.length
        ? h('div', { class: 'doc-pages' }, pages.map((p) => p.wrap))
        : h('div', { class: 'doc-empty' }, 'No page images for this document.', d.source_pdf ? [' ', h('a', { href: S.base + d.source_pdf, target: '_blank', rel: 'noopener' }, 'Open the PDF')] : null));
    scroll.addEventListener('scroll', onDocScroll, { passive: true });
    S.panes[side] = { scroll, pages, ratio: 0, width: 0, expect: null };
    root.append(h('div', { class: 'doc', dataset: { side } }, docHead(side, d, name, r, lay), scroll));
  }
  buildOverlays(r);   // kept in layer views too (hidden by CSS) so a selected change can still be scrolled to
}

// The wrong file came back: say plainly what happened, above the two first pages.
function renderGate(r) {
  const root = $('#gate');
  root.hidden = !r || !stopped(r);
  if (root.hidden) { root.replaceChildren(); return; }
  const doc = cmpDoc(r), floor = r.compare?.gate?.floor;
  const calls = r.audit?.calls ?? arr(r.audit?.requests).length, credits = r.audit?.credits ?? 0;
  const reason = arr(r.reasons).find((x) => /compare/i.test(String(x.source ?? ''))) ?? arr(r.reasons)[0];
  root.replaceChildren(ico('stop'), h('div', null,
    h('h3', null, 'These are not two versions of one agreement'),
    h('p', null, reason ? reasonText(reason, r) : 'Compare read the two files as unrelated, so the review stopped before it started.'),
    h('ul', { class: 'gate-facts' },
      h('li', null, h('b', null, `${plural(calls, 'Clocked call')}, ${plural(credits, 'credit')}`)),
      h('li', null, 'Nothing else was sent'),
      pct(doc?.related) ? h('li', null, `Related ${pct(doc.related)}${pct(floor) ? `; the gate stops below ${pct(floor)}` : ''}`) : null)));
}

function docHead(side, d, name, r, lay) {
  const meta = h('div', { class: 'doc-meta' });
  const link = (file, label, title) => h('a', { href: S.base + file, download: file, title: title || null }, ico('download'), label);
  if (S.layer !== 'changes') {
    if (lay) {
      const caption = [lay.label ?? words(lay.id), byWords(lay.by), lay.spans != null ? plural(lay.spans, 'mark') : null].filter(Boolean).join(' · ');
      meta.append(h('span', { class: 'doc-caption' }, caption));
      if (lay.pdf) meta.append(link(lay.pdf, 'Download PDF', byWords(lay.by)));
    } else {
      meta.append(h('span', { class: 'doc-caption' }, 'Clocked marked only the other document in this layer. These are this one\'s clean pages.'));
    }
  } else {
    if (d.source_pdf) meta.append(link(d.source_pdf, 'Original'));
    if (!arr(d.layers).length) {
      // Older reviews carry no layers: offer whatever marked copies exist as plain downloads.
      const seen = new Set([d.source_pdf]);
      const add = (file, label, title) => { if (file && !seen.has(file)) { seen.add(file); meta.append(link(file, label, title)); } };
      add(d.marked_pdf, 'Marked by Compare', byWords(d.marked_by));
      for (const x of arr(d.extra_pdfs)) add(x?.file, x?.label ?? x?.file);
      for (const x of arr(d.downloads)) add(x?.file, x?.label ?? x?.file, byWords(x?.by));
    }
    if (side === 'v2') {
      const g = arr(r.guard).find((x) => x.target === 'Returned copy');
      if (g) {
        meta.append(!g.ran ? h('span', { class: 'tag t-grey' }, 'Guard not run')
          : g.flagged ? h('button', { type: 'button', class: 'tag t-red', style: 'cursor:pointer', title: 'Clocked Guard flagged text addressed to an AI reviewer', onclick: () => select('guard') }, `Guard: flagged ${pct(g.injection) ?? ''}`.trim())
            : h('span', { class: 'tag t-grey', title: `Clocked Guard found nothing addressed to an AI reviewer. Injection probability ${pct(g.injection) ?? 'unknown'}.` }, 'Guard: clean'));
      }
    }
  }
  const n = arr(d.pages).length;
  return h('div', { class: 'doc-head' },
    h('div', { class: 'doc-name' }, name, n ? h('small', null, plural(n, 'page')) : null),
    meta);
}

function renderLayerSwitch() {
  const root = $('#layer-switch');
  const layers = layersOf();
  root.hidden = !layers.length;
  if (!layers.length) { root.replaceChildren(); return; }
  const all = [{ id: 'changes', label: 'Changes', by: 'Clean pages with every change located on them' }, ...layers.map((l) => ({ ...l, by: byWords(l.by) }))];
  const cur = all.find((l) => l.id === S.layer) ?? all[0];
  const menu = h('div', { class: 'switcher-menu', role: 'listbox', 'aria-label': 'Document layer', hidden: true },
    all.map((l) => h('button', { type: 'button', class: 'switcher-item no-dot', role: 'option', 'aria-selected': String(l.id === cur.id), onclick: () => setLayer(l.id) },
      h('b', null, l.label), l.by ? h('small', null, l.by.charAt(0).toUpperCase() + l.by.slice(1)) : null)));
  menu.addEventListener('keydown', menuKeys);
  const btn = h('button', { type: 'button', class: 'switcher-btn', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', title: `What the pages show: ${cur.label}`, onclick: () => toggleMenu(root) },
    h('span', { class: 'layer-prefix' }, 'Layer'), h('span', { class: 'switcher-label' }, cur.label), ico('chevron'));
  root.replaceChildren(btn, menu);
}
function setLayer(id) {
  const keep = Object.entries(S.panes).map(([side, p]) => [side, p.scroll.scrollHeight ? p.scroll.scrollTop / p.scroll.scrollHeight : 0, p.scroll.scrollLeft]);
  S.layer = id;
  renderDocs();
  sync.progUntil = performance.now() + 300;
  for (const [side, ry, left] of keep) {
    const p = S.panes[side];
    if (!p) continue;
    p.scroll.scrollTop = ry * p.scroll.scrollHeight; p.scroll.scrollLeft = left;
    p.ratio = ry;
  }
  renderLayerSwitch();
  renderLegend();
  markSelection({ scroll: false });
}
function toggleMenu(root) {
  const menu = $('.switcher-menu', root), btn = $('.switcher-btn', root);
  if (!menu || !btn) return;
  menu.hidden = !menu.hidden;
  btn.setAttribute('aria-expanded', String(!menu.hidden));
  if (!menu.hidden) ($('.switcher-item[aria-selected="true"]', menu) ?? $('.switcher-item', menu))?.focus();
}
function closeMenu(root) {
  const menu = $('.switcher-menu', root), btn = $('.switcher-btn', root);
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute('aria-expanded', 'false');
}
function menuKeys(e) {
  const items = $$('.switcher-item', e.currentTarget);
  const i = items.indexOf(document.activeElement);
  if (e.key === 'ArrowDown') { e.preventDefault(); items[Math.min(items.length - 1, i + 1)]?.focus(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); items[Math.max(0, i - 1)]?.focus(); }
  else if (e.key === 'Escape') { const root = e.currentTarget.closest('.switcher'); closeMenu(root); $('.switcher-btn', root)?.focus(); }
}

function rectBox(rect) {
  const v = arr(rect).map(Number);
  if (v.length !== 4 || !v.every(Number.isFinite)) return null;
  const x0 = clamp01(Math.min(v[0], v[2])), x1 = clamp01(Math.max(v[0], v[2]));
  const y0 = clamp01(Math.min(v[1], v[3])), y1 = clamp01(Math.max(v[1], v[3]));
  return x1 > x0 && y1 >= y0 ? { x0, y0, x1, y1 } : null;
}
function rectStyle(rect) {
  const b = rectBox(rect);
  if (!b) return null;
  return { x0: b.x0, y0: b.y0, css: `left:${(b.x0 * 100).toFixed(3)}%;top:${(b.y0 * 100).toFixed(3)}%;width:${((b.x1 - b.x0) * 100).toFixed(3)}%;height:${((b.y1 - b.y0) * 100).toFixed(3)}%` };
}

function addMarks(side, page, rects, cls, cid, label, pinText, first) {
  const pg = S.panes[side]?.pages[page - 1];
  if (!pg) return;
  let pinned = !pinText || !!$(`.pin[data-cid="${cid}"]`, pg.layer);
  arr(rects).forEach((rect, i) => {
    const st = rectStyle(rect);
    if (!st) return;
    const m = h('div', { class: `mark c-${cls}`, style: st.css, title: label, dataset: { cid } });
    if (first && i === 0) m.dataset.first = '1';
    if (cid) m.addEventListener('click', () => select(cid, { from: side }));
    pg.layer.append(m);
    if (!pinned) {
      pinned = true;
      pg.layer.append(h('button', { type: 'button', class: `pin c-${cls}`, style: `left:${(st.x0 * 100).toFixed(3)}%;top:${(st.y0 * 100).toFixed(3)}%`, 'aria-label': label, title: label, dataset: { cid }, onclick: () => select(cid, { from: side }) }, pinText));
    }
  });
}

function buildOverlays(r) {
  // Sentence-level passages from Compare, as a very light tint underneath the change overlays.
  for (const c of changesOf(r)) {
    for (const side of ['v1', 'v2']) {
      const s = c.compare?.[side];
      const pg = s && typeof s === 'object' && s.page ? S.panes[side]?.pages[s.page - 1] : null;
      if (!pg) continue;
      for (const rect of arr(s.rects)) {
        const st = rectStyle(rect);
        if (st) pg.layer.append(h('div', { class: `mark m-sentence c-${clsOf(c)}`, style: st.css }));
      }
    }
  }
  for (const c of changesOf(r)) {
    const label = `${numOf(c)}. ${[clauseOf(c), c.title].filter(Boolean).join(' ')}: ${c.summary ?? ''}`;
    ownParts(c).forEach((part, pi) => {      // an edit that belongs to another change in the same sentence is drawn by that change
      for (const side of ['v1', 'v2']) {
        const s = part?.[side];
        if (s?.page && arr(s.rects).length) addMarks(side, s.page, s.rects, clsOf(c), c.id, label, String(numOf(c)), pi === 0);
      }
    });
  }
  const gi = guardInfo(r);
  if (!gi) return;
  for (const g of gi.flagged) {
    if (g.target !== 'Returned copy') continue;
    arr(g.evidence).forEach((e, i) => {
      if (e.page && arr(e.rects).length) addMarks('v2', e.page, e.rects, 'guard', 'guard', 'Guard: text addressed to an AI reviewer', '!', i === 0);
    });
  }
  const flagged = new Set();
  for (const hdn of gi.hidden) {
    const pg = S.panes.v2?.pages[hdn.page - 1];
    if (!pg || flagged.has(hdn.page)) continue;
    flagged.add(hdn.page);
    pg.layer.append(h('button', { type: 'button', class: 'page-flag', dataset: { cid: 'guard', flag: '1' }, onclick: () => select('guard', { from: 'v2' }) }, ico('alert'), `Hidden text on this page (${hiddenReason(hdn)})`));
  }
}

// The two documents scroll as one. `sync.offset` is how far the returned copy sits below the sent
// version when the selected change is level in both; following is by position, not by movement,
// so the panes cannot drift apart.
const otherSide = (side) => (side === 'v1' ? 'v2' : 'v1');
function onDocScroll(e) {
  const side = e.currentTarget.dataset.side;
  const me = S.panes[side];
  if (!me) return;
  const sc = me.scroll;
  me.ratio = sc.scrollHeight ? sc.scrollTop / sc.scrollHeight : 0;
  if (me.expect && Math.abs(sc.scrollTop - me.expect[0]) < 1.5 && Math.abs(sc.scrollLeft - me.expect[1]) < 1.5) { me.expect = null; return; }   // the echo of follow()
  me.expect = null;
  sync.last = side;
  const now = performance.now();
  if (now < sync.progUntil) { sync.progUntil = Math.max(sync.progUntil, now + 140); return; }   // our own scroll-to-change is running
  follow(side);
}
function follow(side) {
  const me = S.panes[side], other = S.panes[otherSide(side)];
  if (!me || !other || S.view !== 'both' || !me.scroll.offsetParent || !other.scroll.offsetParent) return;
  const o = other.scroll;
  const top = me.scroll.scrollTop + (side === 'v1' ? sync.offset : -sync.offset);
  const left = me.scroll.scrollLeft;
  if (Math.abs(o.scrollTop - top) < 0.75 && Math.abs(o.scrollLeft - left) < 0.75) return;
  const before = [o.scrollTop, o.scrollLeft];
  o.scrollTop = top; o.scrollLeft = left;
  if (o.scrollTop !== before[0] || o.scrollLeft !== before[1]) other.expect = [o.scrollTop, o.scrollLeft];
  other.ratio = o.scrollHeight ? o.scrollTop / o.scrollHeight : 0;
}
// If the reader scrolls while a scroll-to-change is still running, bring the panes level again when it ends.
function settleSoon() {
  clearTimeout(sync.timer);
  const tick = () => {
    const wait = sync.progUntil - performance.now();
    if (wait > 0) { sync.timer = setTimeout(tick, wait + 40); return; }
    follow(sync.last);
  };
  sync.timer = setTimeout(tick, 80);
}

// When the panes change size the pages rescale: keep each pane at the same place in its document.
let resizeRaf = 0;
function onDocsResize() {
  cancelAnimationFrame(resizeRaf);
  resizeRaf = requestAnimationFrame(() => {
    for (const p of Object.values(S.panes)) {
      const sc = p.scroll;
      if (!sc.offsetParent) continue;
      if (p.width && p.width !== sc.clientWidth && p.ratio != null) {
        sync.progUntil = performance.now() + 200;
        sync.offset *= sc.clientWidth / p.width;
        sc.scrollTop = p.ratio * sc.scrollHeight;
      }
      p.width = sc.clientWidth;
    }
    applyAutoZoom();
    settleSoon();
  });
}

function targetPos(side, el, anchor = null) {
  const pane = S.panes[side];
  if (!pane || !el || !pane.scroll.offsetParent) return null;
  const sc = pane.scroll;
  const a = el.getBoundingClientRect(), b = sc.getBoundingClientRect();
  const maxTop = Math.max(0, sc.scrollHeight - sc.clientHeight), maxLeft = Math.max(0, sc.scrollWidth - sc.clientWidth);
  // Sideways (only when zoomed in): keep the page flush left where the target fits, so lines read from their start;
  // otherwise move just far enough to show it.
  const x0 = a.left - b.left + sc.scrollLeft, x1 = x0 + a.width;
  const left = x1 <= sc.clientWidth - 10 ? 0 : Math.max(0, Math.min(x0 - 14, x1 - sc.clientWidth + 14));
  return {
    top: Math.min(maxTop, Math.max(0, a.top - b.top + sc.scrollTop - (anchor ?? sc.clientHeight * 0.3))),
    left: Math.min(maxLeft, left),
  };
}
function scrollPane(side, pos, instant) {
  const pane = S.panes[side];
  if (!pane || !pos || !pane.scroll.offsetParent || narrow()) return;
  sync.progUntil = performance.now() + 450;
  pane.scroll.scrollTo({ top: pos.top, left: pos.left, behavior: instant || reducedMotion() ? 'auto' : 'smooth' });
}
// Scroll both panes to their own targets and remember the distance between them.
function alignTo(pos, { instant = false, skip = null } = {}) {
  for (const side of ['v1', 'v2']) if (!pos[side] && pos[otherSide(side)]) pos[side] = { ...pos[otherSide(side)] };   // nothing to show: stay level with the other
  if (pos.v1 && pos.v2 && S.view === 'both') sync.offset = pos.v2.top - pos.v1.top;
  for (const side of ['v1', 'v2']) if (side !== skip) scrollPane(side, pos[side], instant);
  settleSoon();
}
function scrollPaneTo(side, el, instant) { alignTo({ [side]: targetPos(side, el) }, { instant }); }

function selectionTarget(side) {
  const pane = S.panes[side];
  if (!pane || !S.sel) return null;
  const q = `[data-cid="${CSS.escape(S.sel)}"]`;
  const hit = $(`.mark${q}[data-first]`, pane.scroll) ?? $(`.mark${q}`, pane.scroll) ?? $(`.page-flag${q}`, pane.scroll);
  if (hit) return hit;
  const c = changeById(S.sel);
  const page = arr(c?.parts).map((p) => p?.[side]?.page).find(Boolean);
  return page ? pane.pages[page - 1]?.el ?? null : null;
}

function markSelection({ scroll = true, instant = false, from = null } = {}) {
  const root = $('#docs');
  for (const el of $$('.is-selected', root)) el.classList.remove('is-selected');
  if (!S.sel) return;
  const hits = $$(`[data-cid="${CSS.escape(S.sel)}"]`, root);
  for (const el of hits) {
    el.classList.add('is-selected');
    if (el.classList.contains('mark')) pulse(el);
  }
  if (!scroll) return;
  // Clicked inside a document: leave that pane alone and bring the other one level with it.
  let anchor = null;
  const src = from ? S.panes[from]?.scroll : null;
  const srcEl = from ? selectionTarget(from) : null;
  if (src && srcEl) anchor = srcEl.getBoundingClientRect().top - src.getBoundingClientRect().top;
  const pos = { v1: targetPos('v1', selectionTarget('v1'), anchor), v2: targetPos('v2', selectionTarget('v2'), anchor) };
  if (src && src.offsetParent) pos[from] = { top: src.scrollTop, left: src.scrollLeft };
  alignTo(pos, { instant, skip: src ? from : null });
}
function pulse(el) {
  el.classList.remove('is-pulse');
  void el.offsetWidth;
  el.classList.add('is-pulse');
}

function clearSpot() { for (const el of $$('.m-spot', $('#docs'))) el.remove(); }
function showSpot(spots) {
  clearSpot();
  const pos = {};
  let first = null;
  for (const side of ['v1', 'v2']) {
    const s = spots[side];
    const pg = s ? S.panes[side]?.pages[s.page - 1] : null;
    if (!pg) continue;
    let firstEl = null;
    for (const rect of arr(s.rects)) {
      const st = rectStyle(rect);
      if (!st) continue;
      const m = h('div', { class: 'mark m-spot', style: st.css });
      pg.layer.append(m);
      pulse(m);
      firstEl ??= m;
    }
    pos[side] = targetPos(side, firstEl ?? pg.el);
    first ??= [side, firstEl ?? pg.el];
  }
  if (narrow()) {     // one document at a time: switch to the side that has it and bring it into view
    if (!first) return;
    const [side, el] = S.view !== 'both' && spots[S.view] ? [S.view, $('.m-spot', S.panes[S.view].scroll) ?? first[1]] : first;
    if (S.view !== side) setView(side);
    requestAnimationFrame(() => el.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'center' }));
    return;
  }
  alignTo(pos);
}
// From a crop in the detail panel back to the place in the documents.
function showInDocs(side) {
  if (S.layer !== 'changes') setLayer('changes');
  if (narrow()) {
    if (S.view !== side) setView(side);
    requestAnimationFrame(() => { markSelection({ scroll: false }); selectionTarget(side)?.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'center' }); });
    return;
  }
  markSelection({ scroll: true });
}

// On a laptop two pages side by side leave body text near 7px. Unless the reader has picked a zoom,
// use the smallest one at which a page is about 400px wide (text near 9px); the panes then scroll sideways together.
function fitZoom() {
  if (narrow() || S.view !== 'both') return 1;
  const pane = S.panes.v1 ?? S.panes.v2;
  const w = pane && pane.scroll.offsetParent ? pane.scroll.clientWidth - 16 : 0;
  if (w <= 0) return S.zoom;
  return Math.min(1.5, Math.max(1, Math.ceil((400 / w) * 20 - 0.01) / 20));
}
function applyAutoZoom() {
  if (!S.zoomAuto || !S.review) return;
  const z = fitZoom();
  if (Math.abs(z - S.zoom) > 0.001) setZoom(z, { auto: true });
}
function setZoom(z, { auto = false } = {}) {
  const next = Math.min(ZOOMS.at(-1), Math.max(1, Math.round(z * 100) / 100));
  if (!auto) S.zoomAuto = false;
  const prev = S.zoom || 1;
  const ratios = Object.values(S.panes).map((p) => [p, p.scroll.scrollHeight ? p.scroll.scrollTop / p.scroll.scrollHeight : 0, p.scroll.scrollWidth ? (p.scroll.scrollLeft + p.scroll.clientWidth / 2) / p.scroll.scrollWidth : 0.5]);
  S.zoom = next;
  $('#docs').style.setProperty('--zoom', String(next));
  sync.progUntil = performance.now() + 300;
  sync.offset *= next / prev;
  for (const [p, ry, rx] of ratios) {
    p.scroll.scrollTop = ry * p.scroll.scrollHeight;
    p.scroll.scrollLeft = !auto && next > 1 ? rx * p.scroll.scrollWidth - p.scroll.clientWidth / 2 : 0;
  }
  settleSoon();
  renderZoom();
}
function renderZoom() {
  $('#zoom-val').textContent = `${Math.round(S.zoom * 100)}%`;
  $('#zoom-out').disabled = S.zoom <= 1;
  $('#zoom-in').disabled = S.zoom >= ZOOMS.at(-1);
  $('#zoom-fit').disabled = S.zoom === 1;
}
function syncViewButtons() {
  for (const b of $$('#view-seg button')) b.setAttribute('aria-pressed', String(b.dataset.view === S.view));
}
function setView(v) {
  S.view = narrow() && v === 'both' ? 'v2' : v;
  S.wasBoth = false;
  $('#docs').dataset.view = S.view;
  syncViewButtons();
  requestAnimationFrame(() => {
    applyAutoZoom();
    if (S.sel) markSelection({ scroll: true, instant: true });
    else { sync.offset = 0; follow(sync.last); }
  });
}

const LAYER_NOTES = {
  compare: 'Yellow-green: every passage Clocked Compare paired with the other version, headings and footers included. It shows how much was read, not what changed.',
  terms: 'Boxes: the key terms Clocked Extract read from each version, the ones listed under Key terms.',
  guard: 'Box: the text Clocked Guard flagged as addressed to an AI reviewer. Guard reads the returned copy only.',
};
function renderLegend() {
  const r = S.review;
  const root = $('#legend');
  root.replaceChildren();
  if (!r) return;
  if (S.layer !== 'changes') {
    root.append(h('span', { class: 'legend-note' }, LAYER_NOTES[S.layer] ?? 'Marks drawn by Clocked on its own copy of the PDF.', ' The change overlays are hidden here.'),
      h('button', { type: 'button', class: 'link-btn', onclick: () => setLayer('changes') }, 'Back to Changes'));
    return;
  }
  const present = new Set(changesOf(r).map(clsOf));
  const item = (cls, text) => h('span', null, h('i', { class: `c-${cls}` }), text);
  if (present.has('substantive')) root.append(item('substantive', 'To resolve'));
  if (present.has('reworded')) root.append(item('reworded', 'Reworded'));
  if (present.has('administrative')) root.append(item('administrative', 'Administrative'));
  if (present.has('spelling') || present.has('formatting') || present.has('other')) root.append(item('spelling', 'Spelling, format'));
  const gi = guardInfo(r);      // only when something is marked on a page: a finding in the cover note has no place there
  if (gi && (gi.hidden.length || gi.flagged.some((g) => g.target === 'Returned copy'))) root.append(item('guard', 'Guard, hidden text'));
}

// ------------------------------------------------------------------- detail
function emphasise(quote, changed) {
  if (!quote) return h('span', { class: 'muted' }, 'No text on this side.');
  const at = changed ? quote.indexOf(changed) : -1;
  if (at < 0) return quote;
  return [quote.slice(0, at), h('mark', null, changed), quote.slice(at + changed.length)];
}

function wordDiff(a, b) {
  // Which words of each passage are not shared, by longest common subsequence. Presentation only.
  const A = a.split(/(\s+)/), B = b.split(/(\s+)/);
  const n = A.length, m = B.length;
  if (n * m > 400000) return [quoteNode(a), quoteNode(b)];
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const ka = new Array(n).fill(false), kb = new Array(m).fill(false);
  for (let i = 0, j = 0; i < n && j < m;) {
    if (A[i] === B[j]) { ka[i++] = true; kb[j++] = true; } else if (L[i + 1][j] >= L[i][j + 1]) i++; else j++;
  }
  const build = (T, keep) => T.map((t, i) => (keep[i] || !t.trim() ? t : h('mark', null, t)));
  return [build(A, ka), build(B, kb)];
}
const quoteNode = (t) => t;

function comparePassages(cmp) {
  const q1 = String(cmp.v1_quote ?? cmp.v1?.quote ?? '').trim(), q2 = String(cmp.v2_quote ?? cmp.v2?.quote ?? '').trim();
  if (!q1 && !q2) return null;
  // Mark the differing words only when the two passages are of similar length; otherwise it is noise.
  const alike = q1 && q2 && Math.max(q1.length, q2.length) <= 2.2 * Math.min(q1.length, q2.length);
  const [a, b] = alike ? wordDiff(q1, q2) : [q1, q2];
  const row = (side, label, body) => h('div', { class: 'pair-side', dataset: { side } }, h('div', { class: 'pair-label' }, label), h('div', { class: 'quote quote--clamp' }, body));
  return h('div', { class: 'pair' }, q1 ? row('v1', 'Passage we sent', a) : null, q2 ? row('v2', 'Passage they returned', b) : null);
}
const alignWarning = () => h('div', { class: 'callout t-amber' }, ico('alert'), h('span', null, h('b', null, 'Quote not aligned. '), 'A quoted passage does not match the document text at the offsets Clocked returned. Read the passage in the document before relying on it.'));

function block(title, via, right, ...kids) {
  return h('section', { class: 'block' }, h('div', { class: 'block-head' }, h('h3', null, title), via ? h('span', { class: 'via' }, via) : null, right ?? null), kids);
}
function keyed(key, el) { if (el) el.dataset.block = key; return el; }
function focusBlock(key, instant = false) {
  const sc = $('#detail .detail-scroll');
  const b = sc ? $(`[data-block="${key}"]`, sc) : null;
  if (!b) return;
  const behavior = instant || reducedMotion() ? 'auto' : 'smooth';
  if (narrow()) b.scrollIntoView({ behavior, block: 'start' });
  else sc.scrollTo({ top: Math.max(0, b.offsetTop - 4), behavior });
}

// A blocking change in the same clause as this one.
const clauseSibling = (c) => changesOf().find((x) => x.blocking && x.id !== c.id && x.clause && x.clause === c.clause) ?? null;
const linkTo = (x) => h('button', { type: 'button', class: 'link-btn', onclick: () => select(x.id) }, `${clauseOf(x)} ${x.title ?? ''}`.trim() || `change ${numOf(x)}`);
// Verify and Compare read whole sentences. A spelling, formatting or address edit can sit in a sentence that
// also holds a real change; its readings then describe that other change, not this edit.
function borrowed(c) {
  if (!c || c.blocking || !['spelling', 'formatting', 'administrative'].includes(c.cls)) return null;
  const rd = readersOf(c);
  if (rd.verify !== 'changed' && rd.compare !== 'conflict') return null;
  const named = c.same_sentence_as ? changeById(c.same_sentence_as) : null;
  return { sib: named && named.id !== c.id ? named : clauseSibling(c) };
}
// The engine names other changes by id ("That change is c1"). Readers know them by number.
const idsToNumbers = (text) => String(text ?? '').replace(/\bc(\d+)\b/g, (m) => { const x = changeById(m); return x ? `change ${numOf(x)}` : m; });

const KIND_LABEL = { added: 'Clause added', removed: 'Clause removed', moved: 'Clause moved', renumbered: 'Renumbered', symbol: 'Symbol', header: 'Header or footer' };
const VERIFY_SHORT = { changed: ['a different meaning', 't-amber'], same: ['same meaning', 't-green'], unclear: ['unclear', 't-grey'], not_asked: ['not asked', 't-grey'] };
const COMPARE_SHORT = {
  conflict: ['conflict', 't-amber'], same: ['same', 't-green'], silent: ['nothing flagged', 't-grey'], unpaired: ['no counterpart', 't-grey'],
  not_asked: ['not asked', 't-grey'], not_read: ['not read', 't-grey'], not_run: ['not run', 't-grey'],
};
function rulesReading(c, cls) {
  if (c.kind === 'added') return ['a clause added', 't-amber'];
  if (c.kind === 'removed') return ['a clause removed', 't-amber'];
  if (c.kind === 'moved') return ['a clause moved', 't-grey'];
  if (c.kind === 'renumbered') return ['renumbered', 't-grey'];
  return { substantive: ['a change', 't-amber'], reworded: ['a change of wording', 't-amber'], spelling: ['a spelling fix', 't-grey'], formatting: ['formatting only', 't-grey'], administrative: ['an address', 't-grey'] }[cls]
    ?? [cls ? words(cls) : 'not read', 't-grey'];
}
const DECIDED = {
  'compare:vetoed': 'Compare decided this one: it read a conflict, so the change is held.',
  'verify:cleared': 'Verify decided this one: it read the same meaning both ways, so the rewording is cleared.',
  'verify:promoted': 'Verify decided this one: it read a change of meaning, so a spelling fix became a change.',
  'compare:promoted': 'Compare decided this one: it read a conflict, so a spelling fix became a change.',
};
function decidedLine(c) {
  const d = c.decided_by && typeof c.decided_by === 'object' ? Object.entries(c.decided_by).filter(([, v]) => v) : [];
  return d.map(([k, v]) => DECIDED[`${k}:${v}`] ?? `${SOURCE_NAMES[k.charAt(0).toUpperCase() + k.slice(1)] ? k.charAt(0).toUpperCase() + k.slice(1) : words(k)} decided this one (${words(v)}).`).join(' ');
}
// One block at the top of the detail: what the rule layer, Verify and Compare each read, whether they agree, and why it is listed.
function threeReadings(c) {
  const rd = readersOf(c), bw = borrowed(c);
  const cell = (key, who, clocked, [text, tone], small, tip) => h(key ? 'button' : 'div', { type: key ? 'button' : null, class: `reading ${tone}${key ? ' is-link' : ''}`, title: tip, onclick: key ? () => focusBlock(key) : null },
    h('span', { class: `reading-who${clocked ? ' is-clocked' : ''}` }, who), h('b', null, text), small ? h('small', { class: 'num' }, small) : null);
  // Verify's own verdict on the returned sentence, with its probability. For an added clause that verdict is
  // "not stated": the other version does not have the obligation at all.
  const ras = c.verify?.returned_against_sent ?? c.verify?.sent_against_returned;
  const vp = ras && typeof ras === 'object' ? ras[ras.verdict] : null;
  const vread = rd.verify === 'changed' && ras?.verdict === 'not_stated' ? ['not in the other version', 't-amber'] : (VERIFY_SHORT[rd.verify] ?? [words(rd.verify), 't-grey']);
  const sentence = ['whole sentence', 't-grey'];
  const cells = h('div', { class: 'readings3' },
    cell(null, 'Rules', false, rulesReading(c, rd.rules), null, 'The rule layer: plain code that compares the text of the two PDFs.'),
    cell(c.verify || bw ? 'verify' : null, 'Verify', true, bw ? sentence : vread, bw || !asked(rd.verify) ? null : pct(vp), 'Clocked Verify: is each version\'s sentence supported by the other version?'),
    cell(c.compare && !bw ? 'compare' : bw ? 'verify' : null, 'Compare', true, bw ? sentence : (COMPARE_SHORT[rd.compare] ?? [words(rd.compare), 't-grey']), bw || !asked(rd.compare) ? null : pct(c.compare?.confidence), 'Clocked Compare: do the two passages conflict?'));
  const lead = decidedLine(c);
  const why = c.why ? idsToNumbers(c.why) : '';
  // What Rerank and Decide then did with it, as jumps to their blocks.
  const then = [];
  const chip = (key, name, text, tone) => h('button', { type: 'button', class: `rchip ${tone}`, title: `Clocked ${name}: show it`, onclick: () => focusBlock(key) }, h('b', null, name), text);
  const pb = c.playbook, d = c.decide;
  if (pb) then.push(chip('playbook', 'Rerank', pb.status === 'guidance' && arr(pb.passages).length ? plural(arr(pb.passages).length, 'passage') : pb.status === 'error' ? 'failed' : 'no guidance', pb.status === 'guidance' ? 't-accent' : 't-ink'));
  if (d) then.push(chip('route', 'Decide', d.route ? `${(ROUTES[d.route] ?? words(d.route)).toLowerCase()}${pct(d.route_confidence) ? ` ${pct(d.route_confidence)}` : ''}` : 'no clear answer', d.route ? (ROUTE_TONE[d.route] ?? 't-grey') : 't-grey'));
  return keyed('readings', block('Three readings', null, rd.agree === true && asked(rd.verify) && asked(rd.compare) && !bw ? h('span', { class: 'right-note' }, 'all agree') : null,
    cells,
    disagrees(c) ? h('p', { class: 'readers-clash' }, ico('alert'), uncertainMeaning(c) ? 'Compare reads the same meaning; Verify does not confirm it in both directions. Kept for human review. A substantive change is not established by agreement of the readers.' : 'The readers disagree. The stricter reading wins.') : null,
    lead || why ? h('p', { class: 'plain' }, lead ? h('b', { style: 'font-weight:600;color:var(--ink)' }, `${lead} `) : h('b', { style: 'font-weight:600;color:var(--ink)' }, 'Why it is listed. '), why) : null,
    then.length ? h('div', { class: 'readings' }, then) : null));
}

// A piece of the page image around the given rects, at a size where its text reads like body text (about 14px).
// Drawn with a CSS background, so it needs no new asset.
function cropNode(side, page, rects, cls, label) {
  const p = arr(S.review?.documents?.[side]?.pages)[page - 1];
  const boxes = arr(rects).map(rectBox).filter(Boolean);
  if (!p?.image || !boxes.length) return null;
  const pw = Number(p.width) > 0 ? Number(p.width) : 1020, ph = Number(p.height) > 0 ? Number(p.height) : 1320;
  const Pw = Math.round(Math.min(760, Math.max(520, pw * 0.66)));     // the whole page at this width has body text near 14px
  const Ph = (Pw * ph) / pw;
  const bx0 = Math.min(...boxes.map((b) => b.x0)), bx1 = Math.max(...boxes.map((b) => b.x1));
  const by0 = Math.min(...boxes.map((b) => b.y0)), by1 = Math.max(...boxes.map((b) => b.y1));
  const y0 = Math.max(0, by0 - 0.03), y1 = Math.min(1, by1 + 0.03);   // about a line and a half above and below
  const W = Math.max(240, ($('#detail')?.clientWidth || 360) - 34);
  const regionW = Math.max(W, Math.ceil((bx1 - bx0) * Pw) + 56);
  const left = Math.max(0, Math.min(((bx0 + bx1) / 2) * Pw - regionW / 2, Math.max(0, Pw - regionW)));
  const img = h('div', { class: 'crop-img', style: `width:${regionW}px;height:${Math.round((y1 - y0) * Ph)}px;background-size:${Pw}px ${Ph.toFixed(1)}px;background-position:${(-left).toFixed(1)}px ${(-y0 * Ph).toFixed(1)}px` },
    boxes.map((b) => h('i', { class: `crop-mark c-${cls}`, style: `left:${(b.x0 * Pw - left).toFixed(1)}px;top:${((b.y0 - y0) * Ph).toFixed(1)}px;width:${((b.x1 - b.x0) * Pw).toFixed(1)}px;height:${Math.max(3, (b.y1 - b.y0) * Ph).toFixed(1)}px` })));
  img.style.backgroundImage = `url("${encodeURI(S.base + p.image)}")`;
  const view = h('div', { class: 'crop-view', role: 'button', tabindex: '0', title: 'Show this place in the documents',
    onclick: () => showInDocs(side), onkeydown: (e) => { if (e.key === 'Enter') showInDocs(side); } }, img);
  const first = boxes[0].x0 * Pw - left;
  if (first > W - 60) requestAnimationFrame(() => { view.scrollLeft = first - 28; });   // a wide passage: start where the change starts
  return h('figure', { class: 'crop', dataset: { side } }, h('figcaption', null, h('b', null, label), ` · page ${page}`), view);
}
// The parts of a change that are its own. A part marked `from` belongs to another change that shares the sentence.
const ownParts = (c) => { const own = arr(c.parts).filter((p) => !(p?.from && changeById(p.from))); return own.length ? own : arr(c.parts); };
const sideAbsent = (p, side) => p?.[side]?.absent === true || p?.absent === side;
function cropsBlock(c) {
  const figs = [];
  const height = (rs) => { const bs = rs.map(rectBox).filter(Boolean); return bs.length ? Math.max(...bs.map((b) => b.y1)) - Math.min(...bs.map((b) => b.y0)) : 0; };
  for (const [side, label] of [['v1', 'We sent'], ['v2', 'They returned']]) {
    // An added or removed clause exists on one side only: there is nothing to show for the other.
    const located = ownParts(c).filter((p) => !sideAbsent(p, side)).map((p) => p?.[side]).filter((s) => s?.page && arr(s.rects).length);
    if (!located.length) continue;
    const page = located[0].page;
    let rects = located.filter((s) => s.page === page).flatMap((s) => s.rects);
    if (height(rects) > 0.16) rects = located[0].rects;     // edits far apart on the page: show the first
    const fig = cropNode(side, page, rects, clsOf(c), label);
    if (fig) figs.push(fig);
  }
  return figs.length ? keyed('page', block('On the page', null, h('span', { class: 'right-note' }, 'Click to find it in the documents'), h('div', { class: 'crops' }, figs))) : null;
}

function renderDetail({ keepScroll = false } = {}) {
  const root = $('#detail');
  const r = S.review;
  const prev = keepScroll ? $('.detail-scroll', root)?.scrollTop ?? 0 : 0;
  if (!r) { root.replaceChildren(); return; }
  if (stopped(r)) { root.replaceChildren(stoppedDetail(r)); return; }
  if (S.sel === 'guard' && guardInfo(r)) { root.replaceChildren(...guardDetail(r)); return; }
  const c = changeById(S.sel);
  if (!c) {
    root.replaceChildren(h('div', { class: 'detail-scroll' }, h('div', { class: 'empty', style: 'padding-top:64px' },
      h('b', null, changesOf(r).length ? 'No change selected' : 'Nothing to review'),
      changesOf(r).length ? 'Select a change to see the three readings of it, the playbook and the route.' : arr(r.errors).length ? 'The review did not complete. See the failed steps above.' : 'The two versions do not differ.')));
    return;
  }

  const ch = changesOf(r), i = ch.indexOf(c);
  const head = h('div', { class: 'detail-head' },
    h('div', { class: 'detail-kicker' }, h('span', { class: `pin-n c-${clsOf(c)}` }, i + 1), `Change ${i + 1} of ${ch.length}`,
      h('span', { class: 'nav' },
        h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Previous change', title: 'Previous change (K)', disabled: i === 0, onclick: () => move(-1) }, ico('up'), h('kbd', null, 'K')),
        h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Next change', title: 'Next change (J)', disabled: i === ch.length - 1, onclick: () => move(1) }, ico('down'), h('kbd', null, 'J')))),
    h('h2', { class: 'detail-title' }, [clauseOf(c), c.title].filter(Boolean).join(' · ') || 'Change'),
    c.summary && c.summary !== KIND_LABEL[c.kind] ? h('p', { class: 'detail-summary' }, c.summary) : null,
    h('div', { class: 'detail-badges' }, h('span', { class: `chip ${CLASS_TONE[c.cls] ?? 't-grey'}` }, uncertainMeaning(c) ? 'Meaning needs review' : (KIND_LABEL[c.kind] ?? CLASSES[c.cls] ?? (c.cls ? words(c.cls) : 'Change'))), disagreeMark(c), promotedMark(c)));

  // What changed
  const parts = arr(c.parts);
  const what = block('What changed', null, parts.length > 1 ? h('span', { class: 'right-note' }, plural(parts.length, 'edit')) : null,
    parts.map((p, pi) => {
      const other = p?.from ? changeById(p.from) : null;      // an edit of another change that sits in the same sentence
      const gone = { v1: sideAbsent(p, 'v1'), v2: sideAbsent(p, 'v2') };
      const whole = (s) => !!s?.quote && (!s.changed || s.changed.length > 0.6 * s.quote.length);
      const diff = !gone.v1 && !gone.v2 && p?.v1?.quote && p?.v2?.quote && (whole(p.v1) || whole(p.v2)) ? wordDiff(p.v1.quote, p.v2.quote) : null;
      const sideRow = (side, label) => {
        const s = p?.[side] ?? {};
        if (gone[side]) {
          return h('div', { class: 'pair-side is-absent', dataset: { side } }, h('div', { class: 'pair-label' }, label),
            h('div', { class: 'quote-absent' }, side === 'v1' ? 'Not in the version we sent' : 'Not in the returned copy'));
        }
        const where = s.page ? h('button', { type: 'button', title: 'Show in the document', onclick: () => { const t = arr(s.rects).length ? { [side]: { page: s.page, rects: s.rects } } : null; if (t) showSpot(t); else scrollPaneTo(side, S.panes[side]?.pages[s.page - 1]?.el, false); } }, `Page ${s.page}`) : null;
        const one = gone[side === 'v1' ? 'v2' : 'v1'];          // the other side has nothing: this is the whole clause, shown plain
        return h('div', { class: 'pair-side', dataset: { side } },
          h('div', { class: 'pair-label' }, label, where, s.located === false ? h('small', null, 'not located on the page') : null),
          h('div', { class: 'quote' }, one ? (s.quote || '') : diff ? diff[side === 'v1' ? 0 : 1] : emphasise(s.quote, s.changed)));
      };
      const title = parts.length > 1 && p.summary
        ? h('div', { class: 'part-title' }, `${pi + 1}. ${p.summary}`, other ? [' · same sentence, listed as ', linkTo(other)] : null)
        : null;
      const renum = arr(p.renumbered);
      return [title,
        h('div', { class: 'pair' }, sideRow('v1', 'We sent'), sideRow('v2', 'They returned')),
        renum.length ? h('ul', { class: 'renum' }, renum.map((x) => h('li', null, h('b', null, `${x.v1 ?? '?'} → ${x.v2 ?? '?'}`), x.title ? ` ${x.title}` : ''))) : null,
        p.table_row ? h('p', { class: 'part-note' }, 'This edit is in a table row.') : null,
        p.capped ? h('p', { class: 'part-note' }, 'A long passage: only the start is quoted here.') : null];
    }),
    !parts.length ? h('div', { class: 'not-run' }, 'No quoted text for this change.') : null);

  const bw = borrowed(c);
  root.replaceChildren(
    h('div', { class: 'detail-scroll', tabindex: '0', role: 'region', 'aria-label': 'Detail of the selected change' }, head, threeReadings(c), what, cropsBlock(c),
      bw ? wholeSentenceBlock(c, bw) : [verifyBlock(c), compareBlock(c, r)],
      playbookBlock(c, r), routeBlock(c, r), claimsBlock(c, r)),
    actionsBar(c));
  if (prev) $('.detail-scroll', root).scrollTop = prev;
}
// The wrong file came back: what that means and what to do, instead of an empty panel.
function stoppedDetail(r) {
  const doc = cmpDoc(r), gateInfo = r.compare?.gate ?? {};
  const calls = r.audit?.calls ?? arr(r.audit?.requests).length, credits = r.audit?.credits ?? 0;
  const kv = h('dl', { class: 'kv' });
  kv.append(h('dt', null, 'Compare read'), h('dd', null, doc?.relation ? words(doc.relation) : 'not one agreement', pct(doc?.related) ? h('small', null, ` · related ${pct(doc.related)}`) : null));
  if (pct(gateInfo.floor)) kv.append(h('dt', null, 'It stops below'), h('dd', { class: 'num' }, pct(gateInfo.floor)));
  kv.append(h('dt', null, 'Sent to Clocked'), h('dd', null, `${plural(calls, 'call')}, ${plural(credits, 'credit')}`, h('small', null, ' · nothing else')));
  const idle = arr(r.endpoints).filter((e) => !Number(e.calls)).map((e) => e.name).filter(Boolean);
  return h('div', { class: 'detail-scroll', tabindex: '0', role: 'region', 'aria-label': 'What happened' },
    h('div', { class: 'detail-head' },
      h('div', { class: 'detail-kicker' }, h('span', { class: 'pin-n c-guard' }, '!'), 'The review stopped'),
      h('h2', { class: 'detail-title' }, 'The file that came back is a different document'),
      h('p', { class: 'detail-summary' }, 'It is not a version of the agreement we sent, so there is nothing to compare, check or approve.')),
    block('The gate', 'Clocked Compare', h('span', { class: 'chip t-red' }, 'Stopped'), kv,
      idle.length ? h('p', { class: 'plain', style: 'font-size:12.5px' }, `Not called: ${joinAnd(idle)}.`) : null),
    block('What to do', null, null,
      h('p', { class: 'plain' }, 'Ask the customer for the returned copy of the agreement you sent. When it arrives, run the review again.'),
      h('p', { class: 'plain', style: 'font-size:12.5px' }, 'If this is the file they meant to send, it is a new agreement, not a returned copy, and needs a full review of its own.')));
}

function wholeSentenceBlock(c, bw) {
  const v = c.verify, cmp = c.compare;
  const kv = h('dl', { class: 'kv' });
  const one = (x) => (x && typeof x === 'object' ? `${(VERDICTS[x.verdict] ?? words(x.verdict ?? 'no verdict')).toLowerCase()}${pct(x[x.verdict]) ? ` ${pct(x[x.verdict])}` : ''}` : 'not checked');
  if (v && typeof v === 'object') kv.append(h('dt', null, 'Verify'), h('dd', null, h('small', null, `Returned against sent: ${one(v.returned_against_sent)}. Sent against returned: ${one(v.sent_against_returned)}.`)));
  if (cmp && typeof cmp === 'object' && cmp.relation) kv.append(h('dt', null, 'Compare'), h('dd', null, h('small', null, [RELATION_TEXT[cmp.relation] ?? words(cmp.relation), cmp.kind ? words(cmp.kind) : null, pct(cmp.confidence)].filter(Boolean).join(' · '))));
  return keyed('verify', block('Reading of the whole sentence', 'Clocked Verify · Compare', null,
    bw.sib ? h('p', { class: 'note-neutral' }, 'This sentence also holds another edit (see ', linkTo(bw.sib), '). The readings below are of the whole sentence, so they reflect that edit.') : null,
    kv));
}

function verifyBlock(c) {
  const v = c.verify;
  if (!v || typeof v !== 'object') return null;
  const [text, tone] = VERIFY_READS[v.relation] ?? [v.relation ? words(v.relation) : 'Unclear', 't-grey'];
  const row = (label, x) => h('div', { class: 'vrow' },
    h('div', { class: 'vrow-label' }, label),
    x && typeof x === 'object'
      ? h('div', { class: 'vrow-body' },
        h('span', { class: `chip ${VERDICT_TONE[x.verdict] ?? 't-grey'}` }, VERDICTS[x.verdict] ?? (x.verdict ? words(x.verdict) : 'No verdict')),
        h('span', { class: 'vrow-p num' }, ['supported', 'contradicted', 'not_stated'].filter((k) => pct(x[k])).map((k) => `${VERDICTS[k].toLowerCase()} ${pct(x[k])}`).join(' · ')))
      : h('div', { class: 'vrow-body' }, h('span', { class: 'vrow-p' }, 'Not checked')));
  return keyed('verify', block('Verify across versions', 'Clocked Verify', null,
    h('div', { class: `callout ${tone}` }, h('span', null, h('b', null, text))),
    h('div', { class: 'vrows' },
      row('Returned sentence checked against the sent version', v.returned_against_sent),
      row('Sent sentence checked against the returned version', v.sent_against_returned))));
}

function compareBlock(c, r) {
  const cmp = c.compare;
  if (!cmp || typeof cmp !== 'object') {
    const state = c.readers?.compare;
    return keyed('compare', block('Compare', 'Clocked Compare', null, h('div', { class: 'not-run' },
      state === 'not_asked' ? (c.blocking ? 'Not asked about this change.' : 'Not asked. The rule layer settled this on its own.')
        : state === 'not_read' ? 'Compare did not read this clause.'
          : r.compare ? 'Compare raised no finding on this passage. The rule layer\'s reading stands.' : 'Not run. This change comes from the rule layer alone.')));
  }
  const rel = cmp.relation ?? 'silent';
  const chip = h('span', { class: `chip ${RELATION_TONE[rel] ?? 't-grey'}` }, RELATIONS[rel] ?? words(rel));
  const kv = h('dl', { class: 'kv' });
  kv.append(h('dt', null, 'Reading'), h('dd', null, RELATION_TEXT[rel] ?? words(rel)));
  if (cmp.kind) kv.append(h('dt', null, 'Kind'), h('dd', null, words(cmp.kind)));
  if (pct(cmp.confidence)) kv.append(h('dt', null, 'Confidence'), h('dd', { class: 'num' }, pct(cmp.confidence)));
  const cp = cmp.clause_probabilities;
  const probs = cp && typeof cp === 'object' && !Array.isArray(cp)
    ? Object.entries(cp).filter(([, x]) => pct(x)).map(([k, x]) => `${words(k)} ${pct(x)}`).join(' · ') : (pct(cmp.clause_probability) ?? '');
  if (cmp.clause_relation || probs) {
    // A faithful rewording can sit in a clause that conflicts for another reason. Say so instead of showing both verdicts bare.
    const sib = !c.blocking && /conflict/i.test(String(cmp.clause_relation ?? '')) && (rel === 'same' || rel === 'restated') ? clauseSibling(c) : null;
    kv.append(h('dt', null, 'Whole clause'), sib
      ? h('dd', null, h('small', null, 'Reads as conflicting because it also holds another edit, ', linkTo(sib), '.'))
      : h('dd', null, cmp.clause_relation ? words(cmp.clause_relation) : '', probs ? h('small', { class: 'num' }, `${cmp.clause_relation ? ' · ' : ''}${probs}`) : null));
  }
  return keyed('compare', block('Compare', 'Clocked Compare', chip,
    kv,
    comparePassages(cmp),
    cmp.aligned === false ? alignWarning() : null,
    cmp.note ? h('p', { class: 'plain' }, cmp.note) : null));
}

function passageCard(p, skipped) {
  return h('div', { class: `passage${skipped ? ' is-skipped' : ''}` },
    h('div', { class: 'passage-head' }, h('b', null, p.title ?? p.id ?? 'Passage'), pct(p.relevance) ? h('span', null, `relevance ${pct(p.relevance)}`) : null),
    h('p', null, p.text ?? ''));
}

function playbookBlock(c, r) {
  const pb = c.playbook;
  const title = 'Playbook';
  if (!pb) {
    return keyed('playbook', block(title, 'Clocked Rerank', null, h('div', { class: 'not-run' },
      c.blocking ? 'Not run. No playbook guidance is attached to this change.' : 'Not looked up. Only changes to resolve are checked against the playbook.')));
  }
  const used = arr(pb.passages), skipped = arr(pb.skipped);
  const right = pb.read != null ? h('span', { class: 'right-note' }, `${plural(pb.read, 'passage')} read`) : null;
  const kids = [];
  if (pb.status === 'error') kids.push(h('div', { class: 'callout t-grey' }, ico('alert'), h('span', null, h('b', null, 'Rerank failed. '), 'No guidance could be attached, so a person decides.')));
  else if (pb.status === 'none' || !used.length) kids.push(h('div', { class: 'callout t-ink' }, ico('alert'), h('span', null, h('b', null, 'No guidance. '), 'No playbook passage answers this change, so it goes to Legal.')));
  if (pb.expanded) kids.push(h('p', { class: 'plain', style: 'font-size:12.5px' }, 'Expanded to neighbouring passages: the first pass found the right topic but not the answer, so Rerank read the rest of that topic.'));
  kids.push(used.map((p) => passageCard(p, false)));
  if (skipped.length) {
    kids.push(h('details', { class: 'fold' }, h('summary', null, `${skipped.length} skipped: written for a different case`), h('div', null, skipped.map((p) => passageCard(p, true)))));
  }
  return keyed('playbook', block(title, 'Clocked Rerank', right, kids));
}

function routeBlock(c, r) {
  const d = c.decide;
  const kids = [];
  if (d) {
    const kv = h('dl', { class: 'kv' });
    if (typeof d.desk_may_accept === 'number') kv.append(h('dt', null, 'Desk may accept as written'), h('dd', null, pct(d.desk_may_accept) ?? ''));
    if (typeof d.desk_may_counter === 'number') kv.append(h('dt', null, 'Playbook gives the desk a counter'), h('dd', null, pct(d.desk_may_counter) ?? ''));
    kv.append(h('dt', null, 'Decide says'), h('dd', null, ROUTES[d.route] ?? d.route ?? 'No clear answer', d.route && pct(d.route_confidence) ? h('small', null, ` · confidence ${pct(d.route_confidence)}`) : null));
    if (d.favors) kv.append(h('dt', null, 'Favours'), h('dd', null, d.favors.charAt(0).toUpperCase() + d.favors.slice(1), pct(d.favors_confidence) ? h('small', null, ` · ${pct(d.favors_confidence)}`) : null));
    if (d.materiality_label || typeof d.materiality === 'number') kv.append(h('dt', null, 'Materiality'), h('dd', null, d.materiality_label ?? '', typeof d.materiality === 'number' ? h('small', null, `${d.materiality_label ? ' · ' : ''}${d.materiality.toFixed(1)} of 4`) : null));
    kids.push(kv);
  } else {
    kids.push(h('div', { class: 'not-run' }, c.blocking ? 'Decide did not run for this change.' : 'Not asked. Decide only routes changes to resolve.'));
  }
  if (c.route) {
    const tightened = d?.route && d.route !== c.route;
    kids.push(h('dl', { class: 'final-route' }, h('dt', null, 'Final route'),
      h('dd', null, h('span', { class: `chip ${ROUTE_TONE[c.route] ?? 't-grey'}` }, ROUTES[c.route] ?? c.route),
        c.route_reason ? h('p', null, c.route_reason) : null,
        tightened ? h('p', null, `Code tightened the model's answer (${ROUTES[d.route] ?? d.route}). It can tighten a route, never loosen it.`) : null)));
  }
  return keyed('route', block('Route', 'Clocked Decide', null, kids));
}

function claimsBlock(c, r) {
  const all = arr(r.note?.claims);
  const linked = arr(c.claims).map((i) => [i, all[i]]).filter(([, x]) => x);
  if (!linked.length) return null;
  return block('Cover note statements about this change', 'Clocked Verify', null,
    h('div', { class: 'linked' }, linked.map(([i, x]) => h('button', { type: 'button', title: 'Open in the cover note', onclick: () => { S.claim = i; S.tab = 'note'; renderLeft(); const card = $(`.claim[data-claim="${i}"]`); if (card) scrollIntoViewIfNeeded(card, $('#left-body')); } },
      h('span', { class: `chip ${VERDICT_TONE[x.verdict] ?? 't-grey'}` }, VERDICTS[x.verdict] ?? words(x.verdict)), h('span', null, x.text ?? '')))));
}

function actionsBar(c) {
  if (!c.blocking) {
    const why = c.cls === 'reworded' ? 'Reworded with the same meaning.' : c.cls === 'administrative' ? (c.route_reason ?? 'Administrative change.')
      : c.kind === 'renumbered' ? 'The same clause under a new number.' : c.kind === 'moved' ? 'The same clause in a new place.' : 'No change of meaning.';
    return h('div', { class: 'detail-actions' }, h('div', { class: 'actions-plain' }, h('b', null, 'No decision needed'), h('span', null, why)));
  }
  const d = decisions()[c.id];
  const sug = ROUTES[c.route] ? c.route : null;
  return h('div', { class: 'detail-actions' },
    h('div', { class: 'actions-head' }, h('b', null, 'Reviewer decision'), h('span', null, d ? `Recorded: ${ROUTE_DONE[d]}` : 'Not decided')),
    h('div', { class: 'actions' }, Object.keys(ROUTES).map((a) => h('button', {
      type: 'button', class: `action${sug === a ? ' is-suggested' : ''}${d === a ? ' is-applied' : ''}`, 'aria-pressed': String(d === a), onclick: () => decide(c.id, a),
    }, d === a ? ico('check') : null, ROUTES[a], h('kbd', null, ROUTE_KEY[a])))),
    h('p', { class: 'actions-note' }, d ? 'Press the same action again to clear it.' : sug ? `${ROUTES[sug]} is suggested. Nothing is recorded until you choose.` : 'No route was suggested. Nothing is recorded until you choose.'));
}

function guardDetail(r) {
  const gi = guardInfo(r);
  const inNote = gi.flagged.some((g) => g.target === 'Cover note');
  const head = h('div', { class: 'detail-head' },
    h('div', { class: 'detail-kicker' }, h('span', { class: 'pin-n c-guard' }, '!'), 'Blocking finding'),
    h('h2', { class: 'detail-title' }, gi.flagged.length ? 'Text addressed to an AI reviewer' : 'Hidden text in the returned copy'),
    h('p', { class: 'detail-summary' }, 'Signing is blocked while this is in the documents. Nothing in it was followed.'),
    inNote ? h('div', { class: 'detail-badges' }, h('button', { type: 'button', class: 'link-btn', onclick: () => { S.tab = 'note'; renderLeft(); if (narrow()) reveal($('.left')); } }, 'Show it in the cover note')) : null);
  const blocks = [];
  // Where it sits on the page: the passage is too small to read there, so the crop shows only its place.
  const evid = gi.flagged.filter((g) => g.target === 'Returned copy').flatMap((g) => arr(g.evidence)).filter((e) => e.page && arr(e.rects).length);
  if (evid.length) {
    const page = evid[0].page;
    const fig = cropNode('v2', page, evid.filter((e) => e.page === page).flatMap((e) => e.rects), 'guard', 'They returned');
    if (fig) blocks.push(keyed('page', block('On the page', null, h('span', { class: 'right-note' }, 'Click to find it in the documents'), h('div', { class: 'crops' }, fig),
      h('p', { class: 'plain', style: 'font-size:12.5px' }, 'The outlined strip is the hidden passage. It is too small to read on the page; its text is quoted below.'))));
  }
  const guardBlock = (g) => {
    if (!g.ran) return block(g.target ?? 'Text', 'Clocked Guard', null, h('div', { class: 'not-run' }, 'Guard did not run on this text.'));
    const kv = h('dl', { class: 'kv' });
    kv.append(h('dt', null, 'Injection'), h('dd', { class: 'num' }, pct(g.injection) ?? 'Unknown'));
    if (g.category) kv.append(h('dt', null, 'Category'), h('dd', null, words(g.category)));
    if (g.severity) kv.append(h('dt', null, 'Severity'), h('dd', null, words(g.severity)));
    return block(g.target ?? 'Text', 'Clocked Guard', h('span', { class: `chip ${g.flagged ? 't-red' : 't-green'}` }, g.flagged ? 'Flagged' : 'Clean'), kv,
      arr(g.evidence).map((e) => h('div', { class: 'pair' }, h('div', { class: 'pair-side', dataset: { side: 'v2' } },
        h('div', { class: 'pair-label' }, 'Evidence', e.page ? h('button', { type: 'button', onclick: () => (arr(e.rects).length ? showSpot({ v2: { page: e.page, rects: e.rects } }) : null) }, `Page ${e.page}`) : null, pct(e.confidence) ? h('small', null, pct(e.confidence)) : null),
        h('div', { class: 'quote' }, e.quote ?? '')))));
  };
  for (const g of arr(r.guard).filter((x) => x.flagged)) blocks.push(guardBlock(g));
  if (gi.hidden.length) {
    blocks.push(block('Hidden text', 'Rule layer', h('span', { class: 'chip t-red' }, plural(gi.hidden.length, 'passage')),
      gi.hidden.map((x) => h('div', { class: 'pair' }, h('div', { class: 'pair-side', dataset: { side: 'v2' } },
        h('div', { class: 'pair-label' }, x.page ? `Page ${x.page}` : 'Returned copy', h('small', null, hiddenReason(x))),
        h('div', { class: 'quote' }, x.text ?? ''))))));
  }
  for (const g of arr(r.guard).filter((x) => !x.flagged)) blocks.push(guardBlock(g));
  return [h('div', { class: 'detail-scroll', tabindex: '0', role: 'region', 'aria-label': 'Detail of the finding' }, head, blocks),
    h('div', { class: 'detail-actions' }, h('div', { class: 'actions-plain' }, h('b', null, 'Cannot be resolved here'), h('span', null, 'A reviewer decision does not clear this finding. It needs a corrected copy.')))];
}

// ------------------------------------------------------------------- drawer
// Older reviews carry no `decided` count per endpoint: there, say what the endpoint returned.
function endpointResult(name, r) {
  const ch = changesOf(r), blocking = blockingOf(r);
  if (name === 'Compare') {
    const c = cmpDoc(r);
    if (!c) return null;
    const bits = [];
    const read = blocking.filter((x) => asked(readersOf(x).compare));
    if (read.length) bits.push(`${read.filter((x) => readersOf(x).compare === 'conflict').length} of ${plural(read.length, 'change')} to resolve read as a conflict`);
    const marked = r.compare?.marked ?? c.marked;
    const m1 = marked?.v1?.marked, m2 = marked?.v2?.marked;
    if (m1 != null || m2 != null) bits.push(`${m1 ?? 0} + ${m2 ?? 0} passages marked on the two PDFs`);
    if (c.unaligned) bits.push(`${plural(c.unaligned, 'quote')} not aligned`);
    const unplaced = arr(r.compare?.unplaced ?? c.unplaced).length;
    if (unplaced) bits.push(`${unplaced} not matched to a clause`);
    return bits.length ? bits.join(' · ') : null;
  }
  if (name === 'Verify') {
    const n = r.note;
    const across = ch.filter((x) => x.verify && typeof x.verify === 'object').length;
    const out = [];
    if (n?.ran) {
      const by = {};
      for (const c of arr(n.claims)) by[c.verdict] = (by[c.verdict] ?? 0) + 1;
      const bits = VERDICT_ORDER.filter((v) => by[v]).map((v) => `${by[v]} ${VERDICTS[v].toLowerCase()}`);
      out.push(`${plural(arr(n.claims).length, 'statement')}${bits.length ? `: ${bits.join(', ')}` : ''}`);
    }
    if (across) out.push(`${plural(across, 'change')} read across versions`);
    return out.length ? out.join(' · ') : null;
  }
  if (name === 'Rerank') {
    const looked = blocking.filter((c) => c.playbook);
    if (!looked.length) return null;
    const withG = looked.filter((c) => c.playbook.status === 'guidance').length;
    const none = looked.filter((c) => c.playbook.status === 'none').length;
    const skipped = looked.reduce((s, c) => s + arr(c.playbook.skipped).length, 0);
    const bits = [`${withG} of ${plural(looked.length, 'change')} with guidance`];
    if (none) bits.push(`${none} sent to Legal for lack of it`);
    if (skipped) bits.push(`${plural(skipped, 'passage')} skipped`);
    return bits.join(' · ');
  }
  if (name === 'Decide') {
    const askedOf = blocking.filter((c) => c.decide);
    if (!askedOf.length) return null;
    const by = {};
    for (const c of askedOf) { const k = c.decide.route ?? ''; by[k] = (by[k] ?? 0) + 1; }
    return Object.keys(by).map((k) => `${by[k]} ${k ? (ROUTES[k] ?? words(k)).toLowerCase() : 'no clear answer'}`).join(' · ');
  }
  if (name === 'Extract') {
    const t = arr(r.terms);
    if (!t.length) return null;
    const found = t.filter((x) => x.v1?.value != null || x.v2?.value != null).length;
    return `${found} of ${plural(t.length, 'term')} found · ${t.filter((x) => x.changed === true).length} changed`;
  }
  if (name === 'Guard') {
    const ran = arr(r.guard).filter((g) => g.ran);
    if (!ran.length) return null;
    return `${plural(ran.length, 'text')} scanned · ${ran.filter((g) => g.flagged).length} flagged`;
  }
  return null;
}
// `decided_detail` says which outcomes depended on the endpoint: lists of change ids, statement numbers or names.
function decidedDetail(detail, r, go) {
  if (!detail || typeof detail !== 'object') return null;
  const out = [];
  for (const [k, v] of Object.entries(detail)) {
    if (v === true) { out.push(h('span', null, words(k))); continue; }
    if (!Array.isArray(v) || !v.length) continue;
    const items = v.map((x) => {
      const c = typeof x === 'string' ? changeById(x) : null;
      if (c) return h('button', { type: 'button', class: 'link-btn', title: `${clauseOf(c)} ${c.title ?? ''}`.trim(), onclick: () => go(() => { S.tab = 'changes'; select(c.id); }) }, `change ${numOf(c)}`);
      if (typeof x === 'number' && /statement|claim/.test(k) && arr(r.note?.claims)[x]) return h('button', { type: 'button', class: 'link-btn', title: r.note.claims[x].text, onclick: () => go(() => openNote(x)) }, `statement ${x + 1}`);
      return words(String(x));
    });
    out.push(h('span', null, `${words(k)}: `, items.flatMap((n, i) => (i ? [', ', n] : [n]))));
  }
  return out.length ? h('span', { class: 'ep-detail' }, out.flatMap((n, i) => (i ? [' · ', n] : [n]))) : null;
}

const fmtMs = (ms) => (typeof ms !== 'number' ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
// Compare has two jobs. As a gate it answers one question about the two whole files; say what it answered.
function compareSummary(r) {
  const c = cmpDoc(r), g = r.compare?.gate;
  if (!c || typeof c !== 'object') return null;
  const bits = [];
  if (g && typeof g === 'object') {
    bits.push(g.stopped ? 'the two files are not versions of one agreement, so the review stopped' : g.passed ? 'the two files are versions of one agreement' : g.asked === false ? 'not asked' : 'no answer');
    if (pct(c.related)) bits.push(`related ${pct(c.related)}${pct(g.floor) ? `, it stops below ${pct(g.floor)}` : ''}`);
    const n = Number(r.compare?.clauses_asked) || 0;
    if (n) bits.push(`then ${plural(n, 'clause')} read one by one`);
  } else {
    if (c.conflicts || c.same_passages) bits.push(`${plural(c.conflicts ?? 0, 'passage')} read as conflicting, ${c.same_passages ?? 0} as the same`);
    if (c.unpaired) bits.push(`${c.unpaired} with no counterpart`);
    if (pct(c.related)) bits.push(`the two files read as versions of one agreement (${pct(c.related)})`);
  }
  if (arr(r.compare?.kinds ?? c.kinds).length) bits.push(`kinds of difference: ${arr(r.compare?.kinds ?? c.kinds).map(words).join(', ')}`);
  return bits.length ? h('p', { class: 'drawer-note' }, h('b', null, g ? 'Compare as a gate, on the two whole PDFs' : 'Compare, whole-document pass'), bits.join(' · ')) : null;
}
function fmtTime(iso) {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return iso ?? '';
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 19)} UTC`;
}
function openDrawer(tab) {
  S.drawerOpen = true;
  if (tab) S.drawerTab = tab;
  renderDrawer();
  if (narrow()) reveal($('#drawer'));
}
function closeDrawer() { S.drawerOpen = false; renderDrawer(); }

function renderDrawer() {
  const r = S.review;
  const root = $('#drawer');
  root.dataset.open = String(S.drawerOpen);
  if (!r) { root.replaceChildren(); return; }
  const audit = r.audit ?? {};
  const reqs = arr(audit.requests);
  const credits = audit.credits ?? reqs.reduce((s, a) => s + (Number(a.credits) || 0), 0);
  const calls = audit.calls ?? reqs.length;
  const replayed = reqs.filter((a) => a.replayed).length;
  const here = S.live && S.run;      // reviewed just now on this server, as opposed to a recording
  const origin = !reqs.length ? null : replayed === reqs.length ? 'all replayed from recordings' : replayed === 0 ? (here ? 'all live' : 'recorded from live calls') : `${replayed} replayed, ${reqs.length - replayed} ${here ? 'live' : 'recorded live'}`;

  const tab = (key, label) => h('button', { type: 'button', class: 'tab', role: 'tab', 'aria-selected': String(S.drawerTab === key), onclick: () => { S.drawerTab = key; renderDrawer(); } }, label);
  const bar = h('div', { class: 'drawer-bar' },
    h('button', { type: 'button', class: 'drawer-toggle', 'aria-expanded': String(S.drawerOpen), onclick: () => (S.drawerOpen ? closeDrawer() : openDrawer()) }, ico('chevron'), 'What each endpoint changed'),
    h('div', { class: 'drawer-tabs', role: 'tablist' }, tab('endpoints', 'Endpoints'), tab('audit', h('span', null, 'Audit ', h('span', { class: 'count' }, reqs.length || '')))),
    h('div', { class: 'drawer-sum' },
      calls ? h('span', null, h('b', null, calls), ` Clocked ${calls === 1 ? 'call' : 'calls'}`) : h('span', null, 'Clocked was not called'),
      calls ? h('span', null, h('b', null, credits), ` ${credits === 1 ? 'credit' : 'credits'}`) : null,
      typeof audit.elapsed_ms === 'number' ? h('span', null, `${(audit.elapsed_ms / 1000).toFixed(1)} s end to end`) : null,
      origin ? h('span', null, origin) : null),
    S.drawerOpen ? h('button', { type: 'button', class: 'icon-btn drawer-close', 'aria-label': 'Close', title: 'Close (Esc)', onclick: closeDrawer }, ico('x')) : null);

  let body;
  if (S.drawerTab === 'audit') {
    body = !reqs.length
      ? h('div', { class: 'empty' }, h('b', null, 'No requests'), 'No Clocked request was made for this review.')
      : h('table', { class: 'grid grid--audit' },
        h('thead', null, h('tr', null, h('th', { class: 'r' }, '#'), h('th', null, 'Endpoint'), h('th', null, 'Step'), h('th', { class: 'r' }, 'Credits'), h('th', { class: 'r' }, 'Model ms'), h('th', null, 'Trace'), h('th', null, 'Idempotency key'), h('th', null, 'Source'), h('th', null, 'Recorded'))),
        h('tbody', null, reqs.map((a, i) => h('tr', null,
          h('td', { class: 'r muted' }, i + 1),
          h('td', null, h('b', { style: 'font-weight:600' }, a.endpoint ?? ''), a.path ? h('span', { class: 'ep-path' }, `  ${a.path}`) : null),
          h('td', null, a.step ?? ''),
          h('td', { class: 'r' }, a.credits ?? ''),
          h('td', { class: 'r' }, typeof a.model_ms === 'number' ? Math.round(a.model_ms) : (a.model_ms ?? '')),
          h('td', { class: 'mono' }, a.request_id ?? a.trace ?? ''),
          h('td', { class: 'mono idem' }, a.idempotency_key ?? ''),
          h('td', null, h('span', { class: `src${a.replayed ? '' : ' is-live'}` }, a.replayed ? 'Replayed' : here ? 'Live' : 'Recorded live')),
          h('td', { class: 'num muted' }, fmtTime(a.recorded_at))))),
        h('tfoot', null, h('tr', null, h('td'), h('td', { colspan: 2 }, `${plural(reqs.length, 'request')}`), h('td', { class: 'r' }, credits), h('td', { class: 'r' }, Math.round(reqs.reduce((s, a) => s + (Number(a.model_ms) || 0), 0))), h('td', { colspan: 4 }, 'Total credits and model time'))));
  } else {
    // Both lists are shown in the order the review gives them.
    const eps = arr(r.endpoints);
    const abl = new Map(arr(r.ablation).map((a) => [a.endpoint, a]));
    const orphans = arr(r.ablation).filter((a) => !eps.some((e) => e.name === a.endpoint));
    const go = (fn) => { closeDrawer(); fn(); };
    const ablLinks = (a) => {
      const links = [];
      // Two changes can share a clause and a title: the number is what tells them apart.
      for (const id of arr(a?.changes)) { const c = changeById(id); if (c) links.push(h('button', { type: 'button', onclick: () => go(() => { S.tab = 'changes'; select(id); }) }, `Change ${numOf(c)} · ${clauseOf(c)} ${c.title ?? ''}`.trim())); }
      for (const i of arr(a?.claims)) { const c = arr(r.note?.claims)[i]; if (c) links.push(h('button', { type: 'button', title: c.text, onclick: () => go(() => openNote(i)) }, `Statement ${i + 1}`)); }
      return links.length ? h('div', { class: 'ep-links' }, links) : null;
    };
    const share = (e) => pct(e.credits_share ?? audit.by_endpoint_share?.[e.name]);
    const did = (e) => {
      const n = e.decided;
      if (typeof n !== 'number') return endpointResult(e.name, r) ?? (e.calls ? 'Called, no result recorded' : 'Not run');
      if (!e.calls) return 'Not run';
      return n > 0 ? `Decided ${plural(n, 'outcome')} here` : 'Changed no outcome here';
    };
    const table = !eps.length && !orphans.length
      ? h('div', { class: 'empty' }, h('b', null, 'No endpoint summary'), 'This review has no endpoint summary.')
      : h('table', { class: 'grid grid--eps' },
        h('thead', null, h('tr', null, h('th', { style: 'width:150px' }, 'Endpoint'), h('th', { class: 'r' }, 'Calls'), h('th', { class: 'r' }, 'Credits'), h('th', { class: 'r' }, 'Model time'), h('th', { style: 'width:38%' }, 'In this review'), h('th', null, 'Without it'))),
        h('tbody', null, eps.map((e) => {
          const a = abl.get(e.name);
          return h('tr', { class: e.calls ? '' : 'is-off' },
            h('td', null, h('span', { class: 'ep-name' }, e.name ?? ''), e.path ? h('span', { class: 'ep-path' }, e.path) : null),
            h('td', { class: 'r', 'data-label': 'Calls' }, e.calls ?? 0),
            h('td', { class: 'r', 'data-label': 'Credits' }, e.credits ?? 0, e.calls && share(e) ? h('small', { class: 'share', title: 'Share of the credits this review used' }, ` ${share(e)}`) : null),
            h('td', { class: 'r', 'data-label': 'Model time' }, e.calls ? fmtMs(e.model_ms) : ''),
            h('td', { class: 'wide', 'data-label': 'In this review' }, h('span', { class: 'ep-result' }, did(e)), e.calls ? decidedDetail(e.decided_detail, r, go) : null,
              // With a count of outcomes to show, the general description of the endpoint folds away.
              !e.effect ? null : typeof e.decided === 'number'
                ? h('details', { class: 'ep-more' }, h('summary', null, 'What this endpoint does in a review'), h('span', { class: 'ep-effect' }, tidy(e.effect)))
                : h('span', { class: 'ep-effect' }, tidy(e.effect))),
            h('td', { class: 'wide', 'data-label': 'Without it' }, a ? h('div', { class: 'ep-without' }, tidy(a.text), ablLinks(a)) : h('span', { class: 'muted' }, 'Supporting step')));
        }), orphans.map((a) => h('tr', null, h('td', null, h('span', { class: 'ep-name' }, a.endpoint ?? '')), h('td', { class: 'r' }), h('td', { class: 'r' }), h('td', { class: 'r' }), h('td', { class: 'r' }), h('td', { class: 'wide', 'data-label': 'Without it' }, h('div', { class: 'ep-without' }, tidy(a.text), ablLinks(a)))))),
        h('tfoot', null, h('tr', null, h('td', null, 'Total'), h('td', { class: 'r', 'data-label': 'Calls' }, calls), h('td', { class: 'r', 'data-label': 'Credits' }, credits), h('td', { class: 'r', 'data-label': 'Model time' }, fmtMs(eps.reduce((s, e) => s + (Number(e.model_ms) || 0), 0))), h('td', { class: 'r', colspan: 2 }))));
    const method = arr(r.method).filter((x) => typeof x === 'string' && x.trim());
    const quiet = warningsOf(r).quiet;
    body = [compareSummary(r), table,
      method.length ? h('div', { class: 'drawer-method' }, h('b', null, 'How the verdict is set'), h('ul', null, method.map((x) => h('li', null, x)))) : null,
      quiet.length ? h('div', { class: 'drawer-method' }, h('b', null, 'Notes from the review'), h('ul', null, quiet.map(([, text]) => h('li', null, text)))) : null];
  }
  // The open drawer is a panel over the workspace, not a strip that squeezes it.
  root.replaceChildren(h('div', { class: 'drawer-panel' }, bar, h('div', { class: 'drawer-body', tabindex: S.drawerOpen ? '0' : null }, body)));
}

// ---------------------------------------------------------------- selection
function reveal(el) { el?.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' }); }
function select(id, { keepClaim = false, from = null, scroll = true, jump = true } = {}) {
  S.sel = id;
  if (!keepClaim) S.claim = null;
  const c = changeById(id);
  if (c) S.open[groupOf(c).key] = true;
  clearSpot();
  renderLeft();
  renderDetail();
  markSelection({ scroll, from });
  if (jump && narrow()) reveal($('#detail'));     // one column: the detail and its page crops sit below the list
}
function move(step) {
  const ch = changesOf();
  if (!ch.length) return;
  const i = ch.findIndex((c) => c.id === S.sel);
  const next = i < 0 ? (step > 0 ? 0 : ch.length - 1) : Math.min(ch.length - 1, Math.max(0, i + step));
  if (ch[next].id !== S.sel) select(ch[next].id, { jump: false });
}
function decide(cid, action) {
  const c = changeById(cid);
  if (!c?.blocking) return;
  const d = decisions();
  if (d[cid] === action) delete d[cid]; else d[cid] = action;
  S.outcomes.delete(S.base);      // a decision changed: whatever was signed off or sent no longer stands
  renderVerdict();
  renderLeft();
  renderDetail({ keepScroll: true });
}

// ------------------------------------------------------------------ summary
// Sign-off and send both end here: one part that can go to the customer, one that stays inside.
function openSummary() {
  const r = S.review;
  if (!r) return;
  if (!outcomeOf()) {
    const g = gate(r);
    if (!g.ok) return;
    S.outcomes.set(S.base, { kind: g.kind, at: new Date() });
    renderVerdict();
  }
  if (!stopped(r)) loadReply();
  renderSummary();
  const dlg = $('#reply-dialog');
  if (!dlg.open) dlg.showModal();
}
function undoOutcome() {
  S.outcomes.delete(S.base);
  renderVerdict();
  const dlg = $('#reply-dialog');
  if (dlg.open) dlg.close();
}
// reply.json sits next to review.json: a reply drafted by Clocked Fill for the suggested decisions and checked
// sentence by sentence by Clocked Verify. Fetched only when the summary is opened; a review without one is fine.
function loadReply() {
  const base = S.base;
  if (S.replies.has(base)) return;
  S.replies.set(base, { status: 'loading' });
  fetch(`${base}reply.json`, { cache: 'no-cache' }).then((res) => (res.ok ? res.json() : null)).catch(() => null).then((data) => {
    S.replies.set(base, data && typeof data === 'object' ? { status: 'ok', data } : { status: 'missing' });
    if (S.base === base && $('#reply-dialog').open) renderSummary();
  });
}
const sameDecisions = (mine, theirs, ids) => ids.every((id) => mine[id] && mine[id] === theirs?.[id]);
// Live mode: ask the server to draft the reply for the decisions actually taken.
function draftLive(ids) {
  const base = S.base, d = decisions();
  const taken = Object.fromEntries(ids.map((id) => [id, d[id]]));
  const key = `${base}|${JSON.stringify(taken)}`;
  if (S.drafts.has(key)) return S.drafts.get(key);
  const entry = { status: 'loading' };
  S.drafts.set(key, entry);
  fetch('api/reply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base, decisions: taken }) })
    .then(async (res) => {
      let body = null;
      try { body = await res.json(); } catch { body = null; }
      if (!res.ok) throw new Error(detailMessage(body, res.status, 'The reply could not be drafted'));
      if (!body || typeof body !== 'object') throw new Error('The server answered without a reply.');
      entry.status = 'ok'; entry.data = body;
    })
    .catch((ex) => { entry.status = 'error'; entry.error = ex?.message || 'The reply could not be drafted.'; })
    .finally(() => { if (S.base === base && $('#reply-dialog').open) renderSummary(); });
  return entry;
}
function useSuggested(suggested) {
  const d = decisions();
  for (const c of blockingOf()) if (ROUTES[suggested?.[c.id]]) d[c.id] = suggested[c.id];
  const out = outcomeOf();
  if (out) out.kind = gate(S.review).kind;      // the decisions changed under an open summary: keep it, with the right name
  renderVerdict();
  renderLeft();
  renderDetail({ keepScroll: true });
  renderSummary();
}
function readingsLine(c) {
  const bits = [];
  const rd = readersOf(c), d = c.decide;
  if (rd.rules) bits.push(`Rules: ${rulesReading(c, rd.rules)[0]}`);
  if (asked(rd.verify) && VERIFY_SHORT[rd.verify]) {
    const p = rd.verify === 'changed' ? c.verify?.returned_against_sent?.contradicted : rd.verify === 'same' ? c.verify?.returned_against_sent?.supported : null;
    bits.push(`Verify: ${VERIFY_SHORT[rd.verify][0]}${pct(p) ? ` (${pct(p)})` : ''}`);
  }
  if (asked(rd.compare)) bits.push(`Compare: ${(COMPARE_SHORT[rd.compare] ?? [words(rd.compare)])[0]}${pct(c.compare?.confidence) ? ` (${pct(c.compare.confidence)})` : ''}`);
  if (d) bits.push(`Decide: ${d.route ? (ROUTES[d.route] ?? words(d.route)).toLowerCase() : 'no clear answer'}${d.route && pct(d.route_confidence) ? ` (${pct(d.route_confidence)})` : ''}`);
  return bits.join('; ') + (disagrees(c) ? '. The readers disagree; the stricter reading wins' : '');
}
// The drafted reply as it would be sent: opening, one line per clause with Verify's check, closing.
function draftedReply(reply) {
  const paras = arr(reply.paragraphs).filter((p) => p && p.text);
  const text = String(reply.text ?? '').trim();
  const opening = paras.length ? text.split(/\n\s*\n/)[0] : text;
  const nodes = [];
  if (opening) nodes.push(h('p', { class: 'plain' }, opening));
  if (paras.length) {
    nodes.push(h('ul', { class: 'reply-lines' }, paras.map((p) => h('li', null,
      h('b', null, `${p.clause ? `Clause ${p.clause}` : 'Clause'}${p.title ? ` (${p.title})` : ''}:`), ` ${p.text} `,
      h('span', { class: `vmark ${p.verdict === 'supported' ? 't-green' : 't-amber'}`, title: 'Clocked Verify checked this sentence against the decision taken' },
        p.verdict === 'supported' || !p.verdict ? `checked by Verify · ${pct(p.supported) ?? 'ok'}` : `Verify: ${words(p.verdict)}${pct(p.supported) ? ` · ${pct(p.supported)}` : ''}`)))));
    if (reply.closing) nodes.push(h('p', { class: 'plain' }, reply.closing));
  }
  const removed = arr(reply.removed).length;
  if (reply.ran) nodes.push(h('p', { class: 'reply-foot' }, `Drafted by Clocked Fill. Each sentence was checked against the decisions by Clocked Verify; ${removed} removed.`));
  return { nodes, text };
}
function renderSummary() {
  const r = S.review;
  const g = gate(r), out = outcomeOf(), d = decisions();
  const title = r.case?.title ?? 'Review';
  const label = (c) => `${c.clause_label ?? c.clause ? `Clause ${c.clause_label ?? c.clause}, ` : ''}${c.title ?? 'change'}`;
  const blocking = g.blocking, ids = blocking.map((c) => c.id);
  const by = (a) => blocking.filter((c) => d[c.id] === a);
  const accepted = by('accept'), countered = by('counter'), escalated = by('escalate');
  const undecided = blocking.filter((c) => !d[c.id]);
  const pending = [...escalated, ...undecided];
  const rest = changesOf(r).filter((c) => !c.blocking);
  const sent = (c) => String(c.compare?.v1?.quote ?? ownParts(c)[0]?.v1?.quote ?? '').replace(/\s+/g, ' ').trim();
  const when = out ? fmtClock(out.at) : '';
  const kind = out?.kind ?? g.kind;
  const part = (cls, head, sub, nodes, lines, id) => {
    const btn = h('button', { type: 'button', class: 'btn btn--sm' }, ico('copy'), 'Copy');
    btn.addEventListener('click', () => copyText((typeof lines === 'string' ? lines : lines.join('\n')), btn, head));
    return h('section', { class: `sum-part ${cls}` }, h('header', { class: 'sum-head' }, h('div', null, h('h3', null, head), h('p', null, sub)), btn), h('div', { class: 'sum-body', id: id ?? null }, nodes));
  };
  $('#reply-copied').hidden = true;
  $('#reply-undo').hidden = !out;

  // ---- the wrong file came back: the only thing to send is a request for the right one
  if (kind === 'asked') {
    const doc = cmpDoc(r), calls = r.audit?.calls ?? arr(r.audit?.requests).length, credits = r.audit?.credits ?? 0;
    const msg = ['Thank you for sending the file back. It is not a version of the agreement we sent you, so we have not reviewed it.', 'Could you send the returned copy of that agreement? We will review it as soon as it arrives.'];
    const notes = [`${title}: ${String(r.headline ?? 'not the same agreement').toLowerCase()}${when ? `. Asked for the right file at ${when}` : ''}`,
      ...arr(r.reasons).map((x) => `- ${x.source}: ${reasonText(x, r)}`),
      `- Compare read the two files as ${doc?.relation ? words(doc.relation) : 'not one agreement'}${pct(doc?.related) ? `, related ${pct(doc.related)}` : ''}${pct(r.compare?.gate?.floor) ? ` (it stops below ${pct(r.compare.gate.floor)})` : ''}.`,
      `- ${plural(calls, 'Clocked call')}, ${plural(credits, 'credit')}. Nothing else was sent.`];
    $('#reply-title').textContent = 'Ask for the right file';
    $('#reply-body').replaceChildren(
      h('div', { class: 'reply-banner t-red' }, ico('stop'), h('span', null, `${r.headline ?? 'Not the same agreement'}${when ? `. Asked for the right file at ${when}` : ''}`, h('small', null, 'Nothing was reviewed, so there is nothing to approve.'))),
      part('is-customer', 'For the customer', 'A request for the right file. Safe to send.', msg.map((x) => h('p', { class: 'plain' }, x)), msg.join('\n\n'), 'reply-customer'),
      part('is-internal', 'Internal notes', 'What stopped the review. Not for the customer.', [h('ul', { class: 'reply-plain' }, notes.slice(1).map((x) => h('li', null, x.replace(/^- /, ''))))], notes));
    return;
  }

  // ---- header
  $('#reply-title').textContent = kind === 'sent' ? 'Sent to approvers' : 'Review signed off';
  const tally = [[accepted.length, 'accepted'], [countered.length, 'to counter'], [escalated.length, 'to another approver'], [undecided.length, 'undecided']].filter(([n]) => n).map(([n, t]) => `${n} ${t}`).join(', ');
  const banner = kind === 'sent'
    ? h('div', { class: `reply-banner ${g.st === 'changes' ? 't-amber' : 't-red'}` }, ico(g.st === 'changes' ? 'open' : 'stop'), h('span', null, `${HEADLINES[g.st]}. Sent to approvers${when ? ` at ${when}` : ''}`, tally ? h('small', null, tally) : null))
    : h('div', { class: `reply-banner ${countered.length ? 't-amber' : 't-green'}` }, ico(countered.length ? 'open' : 'check'),
      h('span', null, `Review signed off${when ? ` at ${when}` : ''}`, h('small', null, countered.length ? `${tally}. The reply below goes to the customer before anyone signs.` : 'The agreement can be signed.')));

  // ---- for the customer. First choice: the reply Clocked Fill drafted and Clocked Verify checked.
  const rec = S.replies.get(S.base);
  const recorded = rec?.status === 'ok' ? rec.data : null;
  let cn = [], cl = [], drafted = null, sub = 'What we accept and what we do not. Safe to send.';
  const wait = [];
  if (recorded && recorded.text && sameDecisions(d, recorded.decisions, ids)) drafted = recorded;
  else if (S.live && ids.length && !undecided.length) {
    const e = draftLive(ids);
    if (e.status === 'ok') drafted = e.data;
    else if (e.status === 'loading') wait.push(h('p', { class: 'reply-wait' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Clocked Fill is drafting the reply for these decisions, and Clocked Verify is checking it.'));
    else wait.push(h('div', { class: 'form-error' }, e.error), h('p', { class: 'reply-foot' }, 'Below is the plain summary of the decisions instead.'));
  } else if (S.live && ids.length && undecided.length) {
    wait.push(h('p', { class: 'reply-foot' }, `A reply is drafted once every change to resolve has a decision. ${plural(undecided.length, 'change')} left.`));
  } else if (recorded && ids.length && Object.keys(recorded.decisions ?? {}).length) {
    wait.push(h('div', { class: 'reply-suggest' }, h('span', null, 'A drafted reply is recorded for the suggested decisions only.'),
      h('button', { type: 'button', class: 'btn btn--sm', id: 'reply-suggested', onclick: () => useSuggested(recorded.decisions) }, 'Use the suggested decisions')));
  }
  if (drafted) {
    const x = draftedReply(drafted);
    cn = x.nodes; cl = x.text;
    sub = drafted.ran ? 'Drafted by Clocked Fill, checked by Clocked Verify. Safe to send.' : 'Safe to send.';
  } else {
    // The plain summary: what we accept and what we do not, in words that can leave the building.
    const lines = [];
    const opening = g.st === 'ready'
      ? 'We compared the returned copy with the version we sent and found no change of substance. It is ready for signature.'
      : g.st === 'changes' && !countered.length && !pending.length
        ? 'We compared the returned copy with the version we sent. We accept the changes listed below, and the agreement can be signed.'
        : 'We compared the returned copy with the version we sent. We cannot sign it as returned.';
    lines.push(opening); cn.push(h('p', { class: 'plain' }, opening));
    const group = (head, items, line) => {
      if (!items.length) return;
      lines.push('', head, ...items.map((c) => `- ${line(c)}`));
      cn.push(h('div', null, h('p', { class: 'reply-sub' }, head), h('ul', { class: 'reply-plain' }, items.map((c) => h('li', null, line(c))))));
    };
    group('Accepted as returned:', accepted, (c) => `${label(c)}: ${c.summary ?? ''}.`);
    group('Not accepted:', countered, (c) => `${label(c)}: we do not accept the change (${c.summary ?? 'see the clause'}).${sent(c) ? ` Our position is the wording we sent: “${sent(c)}”` : ' Our position is the wording of the version we sent.'}`);
    group('Still under review on our side:', pending, (c) => `${label(c)}: ${c.summary ?? ''}.`);
    cl = lines;
    cn = [...wait, ...cn];
  }

  // ---- internal notes: playbook, approvers, Clocked readings
  const il = [], inn = [];
  il.push(`${title}: ${HEADLINES[g.st].toLowerCase()}${out ? `. ${OUTCOME_TEXT[out.kind]} at ${when}` : ''}`);
  const blockers = arr(r.reasons).filter((x) => x.level === 'block' || x.level === 'incomplete');
  if (blockers.length) {
    il.push('', 'Why this copy cannot be signed:', ...blockers.map((x) => `- ${x.source}: ${reasonText(x, r)}`));
    inn.push(h('div', null, h('p', { class: 'reply-sub' }, 'Why this copy cannot be signed'), h('ul', { class: 'reply-plain' }, blockers.map((x) => h('li', null, `${x.source}: ${reasonText(x, r)}`)))));
  }
  const bad = arr(r.note?.claims).filter((x) => x.verdict === 'contradicted');
  if (bad.length) {
    il.push('', 'Statements in the cover note that the documents contradict:', ...bad.map((x) => `- "${x.text}"`));
    inn.push(h('div', null, h('p', { class: 'reply-sub' }, 'Statements in the cover note that the documents contradict'), h('ul', { class: 'reply-plain' }, bad.map((x) => h('li', null, x.text)))));
  }
  const toAi = arr(r.note?.instructions).filter((x) => x?.text);
  if (toAi.length) {
    il.push('', 'In the cover note, addressed to an AI reviewer (handled by Guard, not followed):', ...toAi.map((x) => `- "${x.text}"`));
    inn.push(h('div', null, h('p', { class: 'reply-sub' }, 'In the cover note, addressed to an AI reviewer (handled by Guard, not followed)'), h('ul', { class: 'reply-plain' }, toAi.map((x) => h('li', null, x.text)))));
  }
  const hidden = arr(r.hidden_text);
  if (hidden.length) {
    il.push('', 'Hidden text in the returned copy:', ...hidden.map((x) => `- Page ${x.page ?? '?'} (${hiddenReason(x)}): "${x.text}"`));
    inn.push(h('div', null, h('p', { class: 'reply-sub' }, 'Hidden text in the returned copy'), h('ul', { class: 'reply-plain' }, hidden.map((x) => h('li', null, `Page ${x.page ?? '?'} (${hiddenReason(x)}): “${x.text}”`)))));
  }
  if (blocking.length) {
    il.push('', 'Changes to resolve:');
    const list = h('div', { class: 'reply-list' });
    blocking.forEach((c, i) => {
      const a = d[c.id];
      const sug = ROUTES[c.route] ? `${ROUTES[c.route]} suggested${c.route_reason ? `. ${c.route_reason}` : ''}` : '';
      const reads = readingsLine(c);
      const passages = arr(c.playbook?.passages);
      const item = h('div', { class: 'reply-item' },
        h('div', { class: 'reply-item-head' }, h('b', null, label(c)), h('span', { class: `chip ${a ? ROUTE_TONE[a] : 't-grey'}` }, a ? ROUTE_DONE[a] : 'Undecided')),
        c.summary ? h('p', { class: 'reply-item-sum' }, c.summary) : null,
        sug ? h('p', { class: 'reply-sub' }, sug) : null,
        reads ? h('p', { class: 'reply-sub' }, `The readings. ${reads}.`) : null,
        passages.map((p) => h('blockquote', null, h('b', null, `Playbook · ${p.title ?? p.id ?? ''}`), p.text ?? '')),
        c.playbook && !passages.length ? h('p', { class: 'reply-sub' }, 'The playbook has no passage for this change.') : null);
      il.push(`${i + 1}. ${label(c)}: ${c.summary ?? ''}`, `   Decision: ${a ? ROUTES[a] : 'undecided'}${sug ? ` (${sug})` : ''}`);
      if (reads) il.push(`   The readings. ${reads}.`);
      for (const p of passages) il.push(`   Playbook, ${p.title ?? p.id ?? 'passage'}: ${p.text ?? ''}`);
      if (c.playbook && !passages.length) il.push('   The playbook has no passage for this change.');
      list.append(item);
    });
    inn.push(list);
  }
  if (drafted && drafted.ran) {
    // What Verify took out of the draft, and why: internal, because the customer never sees those sentences.
    const removed = arr(drafted.removed), n = arr(drafted.paragraphs).length;
    const extra = arr(drafted.audit).length ? ` ${plural(arr(drafted.audit).length, 'more Clocked call')} (${[...new Set(arr(drafted.audit).map((a) => a.endpoint).filter(Boolean))].join(', ')}), ${plural(arr(drafted.audit).reduce((s, a) => s + (Number(a.credits) || 0), 0), 'credit')}.` : '';
    const head = `The reply: drafted by Clocked Fill${drafted.tone ? `, tone ${words(drafted.tone)}` : ''}. Verify checked ${plural(n, 'sentence')} against the decisions and removed ${removed.length}.${extra}`;
    il.push('', head, ...removed.map((x) => `- Removed: "${x.text ?? ''}"${x.reason ? ` (${tidy(x.reason)})` : ''}`), ...arr(drafted.errors).map((x) => `- Error: ${typeof x === 'string' ? x : x?.message ?? JSON.stringify(x)}`));
    inn.push(h('div', null, h('p', { class: 'reply-sub' }, head),
      removed.length || arr(drafted.errors).length ? h('ul', { class: 'reply-plain' },
        removed.map((x) => h('li', null, h('b', null, 'Removed: '), `“${x.text ?? ''}”`, x.reason ? ` ${tidy(x.reason)}` : '')),
        arr(drafted.errors).map((x) => h('li', null, h('b', null, 'Error: '), typeof x === 'string' ? x : x?.message ?? JSON.stringify(x)))) : null));
  }
  if (rest.length) {
    const head = `${plural(rest.length, 'other difference')} needed no decision:`;
    const line = (c) => `${label(c)}: ${c.summary ?? ''} (${(KIND_LABEL[c.kind] ?? CLASSES[c.cls] ?? words(c.cls ?? '')).toLowerCase()})`;
    il.push('', head, ...rest.map((c) => `- ${line(c)}`));
    inn.push(h('div', null, h('p', { class: 'reply-sub' }, head), h('ul', { class: 'reply-plain' }, rest.map((c) => h('li', null, line(c))))));
  } else if (!blocking.length) {
    il.push('', 'The two versions do not differ.');
    inn.push(h('p', { class: 'plain' }, 'The two versions do not differ.'));
  }

  $('#reply-body').replaceChildren(banner,
    part('is-customer', 'For the customer', sub, cn, cl, 'reply-customer'),
    part('is-internal', 'Internal notes', 'Playbook, approvers and the readings. Not for the customer.', inn, il));
}
async function copyText(text, btn, what) {
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch {
    const ta = h('textarea', { style: 'position:fixed;opacity:0' }); ta.value = text;
    $('#reply-dialog').append(ta); ta.select();
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
  }
  const note = $('#reply-copied');
  note.textContent = ok ? `“${what}” copied to the clipboard` : 'Copy failed. Select the text and copy it by hand.';
  note.hidden = false;
  if (ok && btn) { btn.classList.add('is-done'); setTimeout(() => btn.classList.remove('is-done'), 1600); }
}

// ------------------------------------------------------------------- upload
function detailMessage(body, status, what = 'The review could not be run') {
  const d = body?.detail ?? body?.message ?? body?.error;
  if (typeof d === 'string' && d.trim()) return d;
  if (Array.isArray(d)) { const m = d.map((x) => (typeof x === 'string' ? x : x?.msg)).filter(Boolean).join(' '); if (m) return m; }
  if (d && typeof d === 'object' && typeof d.message === 'string') return d.message;
  return `${what} (HTTP ${status}).`;
}
async function submitUpload(e) {
  e.preventDefault();
  const form = $('#upload-form');
  const err = $('#upload-error');
  const fail = (msg) => { err.textContent = msg; err.hidden = false; };
  err.hidden = true;
  const v1 = form.elements.v1.files[0], v2 = form.elements.v2.files[0];
  if (!v1 || !v2) return fail('Choose both PDFs: the version we sent and the copy they returned.');
  const isPdf = (f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name);
  if (!isPdf(v1) || !isPdf(v2)) return fail('Both files must be PDFs.');
  const fd = new FormData();
  fd.append('v1', v1); fd.append('v2', v2); fd.append('note', form.elements.note.value ?? '');
  const busy = (on) => { $('#upload-busy').hidden = !on; $('#upload-submit').disabled = on; };
  busy(true);
  try {
    const res = await fetch('api/review', { method: 'POST', body: fd });
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    if (!res.ok) throw new Error(detailMessage(body, res.status));
    const base = safeBase(body?.base);
    if (!base) throw new Error('The server answered without a review location.');
    const review = await getJSON(`${base}review.json`);
    S.runs = S.runs.filter((x) => x.base !== base);
    S.runs.unshift({ base, title: `${review.case?.title ?? 'Uploaded agreement'} · ${v2.name}`, description: `${v1.name} against ${v2.name}`, status: statusOf(review) });
    $('#upload-dialog').close();
    form.reset();
    await openReview({ base, run: true, preloaded: review });
  } catch (ex) {
    fail(ex?.message || 'The review could not be run.');
  } finally {
    busy(false);
  }
}

// ------------------------------------------------------------------ loading
function showMessage(title, text) {
  S.review = null;
  $('#app').dataset.status = 'error';
  $('#verdict').replaceChildren(h('div', { class: 'verdict-main' }, h('span', { class: 'verdict-glyph' }, ico('dots')), h('h1', { class: 'verdict-headline' }, title), h('p', { class: 'verdict-case' }, text)));
  $('#errors').hidden = true;
  $('#left-body').replaceChildren();
  $('#docs').replaceChildren(h('div', { class: 'splash' }, h('b', null, title), 'Choose a review from the menu at the top.'));
  $('#detail').replaceChildren();
  $('#legend').replaceChildren();
  $('#gate').hidden = true;
  $('#warnings').hidden = true;
  $('#layer-switch').hidden = true;
  $('#drawer').replaceChildren();
  for (const id of ['#count-changes', '#count-note', '#count-terms']) $(id).textContent = '';
  renderTop();
}

const TABS = ['changes', 'note', 'terms'];
async function openReview({ id = null, base, run = false, change = null, view = null, tab = null, moment = null, preloaded = null }) {
  const token = ++S.loadToken;
  let r = preloaded;
  if (!r) {
    try { r = await getJSON(`${base}review.json`); } catch (ex) {
      if (token !== S.loadToken) return;
      S.base = base; S.caseId = id; S.run = run;
      showMessage('Review not available', `The review at ${base} could not be loaded. ${ex?.message ?? ''}`);
      return;
    }
  }
  if (token !== S.loadToken) return;
  S.review = r; S.base = base; S.caseId = id ?? r.case?.id ?? null; S.run = run;
  S.claim = null; S.tab = TABS.includes(tab) ? tab : 'changes'; S.open = {}; S.layer = 'changes';
  sync.offset = 0;
  const ch = changesOf(r);
  // A finding that blocks on its own (text addressed to an AI, hidden text) is what to look at first.
  if (view === 'guard' && guardInfo(r)) S.sel = 'guard';
  else if (change && ch.some((c) => c.id === change)) S.sel = change;
  else S.sel = guardInfo(r) ? 'guard' : (ch.find((c) => c.blocking) ?? ch[0])?.id ?? null;

  // The "start here" moments land on the evidence, not just on the case.
  let focus = null;
  if (moment === 'number') {
    const c = ch.find((x) => x.blocking && String(x.clause_label ?? x.clause) === '4.2') ?? ch.find((x) => x.blocking);
    if (c) S.sel = c.id;
    focus = 'readings';
  } else if (moment === 'rewording') {
    const c = ch.find((x) => x.cls === 'reworded');
    if (c) S.sel = c.id;
    focus = 'readings';
  } else if (moment === 'note') {
    const i = firstContradicted(r, true);
    S.tab = 'note';
    if (i >= 0) {
      S.claim = i;
      const cid = arr(r.note.claims[i].change_ids).find((x) => ch.some((c) => c.id === x));
      if (cid) S.sel = cid;
    }
    focus = 'note';
  } else if (moment === 'readers') {
    const c = ch.find((x) => x.blocking && String(x.clause_label ?? x.clause) === '10') ?? ch.find(disagrees) ?? ch.find((x) => x.blocking);
    if (c) S.sel = c.id;
    focus = 'readings';
  } else if (moment === 'guard') {
    if (guardInfo(r)) S.sel = 'guard';
    focus = 'detail';
  }
  const cur = changeById(S.sel);
  if (cur) S.open[groupOf(cur).key] = true;

  document.title = `${r.case?.title ? `${r.case.title} · ` : ''}Redline Referee`;
  renderTop();
  renderIntro();
  renderVerdict();
  renderErrors();
  renderLeft();
  renderDocs();
  renderLayerSwitch();
  applyAutoZoom();
  renderZoom();
  renderLegend();
  renderDetail();
  renderDrawer();
  requestAnimationFrame(() => {
    markSelection({ scroll: true, instant: true });
    if (focus === 'readings') focusBlock('readings', true);
    if (focus === 'note' && S.claim != null) { const card = $(`.claim[data-claim="${S.claim}"]`); if (card) scrollIntoViewIfNeeded(card, $('#left-body')); }
    if (focus && narrow()) reveal(focus === 'note' ? $('.left') : $('#detail'));
  });
}

function applyHash() {
  const hsh = readHash();
  if (hsh.intro === '0' && !S.noIntro) { S.noIntro = true; renderIntro(); }
  const runBase = hsh.run ? safeBase(hsh.run) : null;
  const wantBase = runBase ?? (hsh.case ? `cases/${hsh.case}/` : null);
  if (wantBase && wantBase !== S.base) {
    if (runBase) return openReview({ base: runBase, run: true, change: hsh.change, view: hsh.view, tab: hsh.tab });
    if (/^[A-Za-z0-9._-]+$/.test(hsh.case)) return openReview({ id: hsh.case, base: wantBase, change: hsh.change, view: hsh.view, tab: hsh.tab });
  }
  if (!S.review) return null;
  const wantTab = TABS.includes(hsh.tab) ? hsh.tab : 'changes';
  if (wantTab !== S.tab) { S.tab = wantTab; renderLeft(); }
  const want = hsh.view === 'guard' && guardInfo() ? 'guard' : changeById(hsh.change)?.id ?? null;
  if (want && want !== S.sel) select(want, { jump: false });
  return null;
}

// --------------------------------------------------------------------- boot
function wire() {
  for (const el of $$('[data-ico]')) el.innerHTML = ICONS[el.dataset.ico] ?? '';

  $('#switcher-btn').addEventListener('click', () => toggleMenu($('#switcher')));
  document.addEventListener('pointerdown', (e) => {
    for (const sw of $$('.switcher')) if (!sw.contains(e.target)) closeMenu(sw);
    // The open drawer lies over the workspace: a click anywhere else puts it away.
    if (S.drawerOpen && !narrow() && e.target instanceof Element && !e.target.closest('.drawer, #clocked-pill, #how-btn, dialog')) closeDrawer();
  });
  $('#switcher-menu').addEventListener('keydown', menuKeys);

  $('#how-btn').addEventListener('click', () => openDrawer('endpoints'));
  $('#clocked-pill').addEventListener('click', () => openDrawer('endpoints'));
  $('#upload-btn').addEventListener('click', () => {
    if (!S.live) return;
    $('#upload-error').hidden = true;
    $('#upload-dialog').showModal();
  });
  $('#upload-form').addEventListener('submit', submitUpload);
  for (const b of $$('dialog [data-close]')) b.addEventListener('click', () => b.closest('dialog').close());
  for (const dlg of $$('dialog')) dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  $('#reply-undo').addEventListener('click', undoOutcome);

  for (const t of $$('#left-tabs .tab')) t.addEventListener('click', () => { S.tab = t.dataset.tab; renderLeft(); });
  for (const b of $$('#view-seg button')) b.addEventListener('click', () => setView(b.dataset.view));
  $('#zoom-in').addEventListener('click', () => setZoom(ZOOMS.find((z) => z > S.zoom + 1e-6) ?? S.zoom));
  $('#zoom-out').addEventListener('click', () => setZoom([...ZOOMS].reverse().find((z) => z < S.zoom - 1e-6) ?? 1));
  $('#zoom-fit').addEventListener('click', () => setZoom(1));

  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || !S.review) return;
    if ($('dialog[open]')) return;
    if (e.key === 'Escape' && S.drawerOpen) { closeDrawer(); return; }
    const t = e.target instanceof Element ? e.target : null;
    if (t?.closest('input, textarea, select, [contenteditable], .switcher')) return;
    const k = e.key;
    if (k === 'ArrowDown' || k === 'ArrowUp') {
      // Arrow keys belong to whatever scrolls. They move the selection only from the list of changes, or from nowhere in particular.
      if (t?.closest('.doc-scroll, .detail-scroll, .drawer, .crop-view, .letter')) return;
      if (S.tab !== 'changes' && t?.closest('.left')) return;
      e.preventDefault();
      move(k === 'ArrowDown' ? 1 : -1);
    } else if (k === 'j') { e.preventDefault(); move(1); }
    else if (k === 'k') { e.preventDefault(); move(-1); }
    else if (!e.shiftKey && (k === 'a' || k === 'c' || k === 'e')) {
      const action = { a: 'accept', c: 'counter', e: 'escalate' }[k];
      if (changeById(S.sel)?.blocking) { e.preventDefault(); decide(S.sel, action); }
    }
  });
  window.addEventListener('hashchange', applyHash);
  new ResizeObserver(onDocsResize).observe($('#docs'));
  // Crossing between one column and three: one document at a time on a phone, side by side again on a desk.
  window.matchMedia?.('(max-width: 899px)').addEventListener?.('change', (e) => {
    if (!S.review) return;
    if (e.matches && S.view === 'both') { setView('v2'); S.wasBoth = true; }
    else if (!e.matches && S.wasBoth) setView('both');
    if (S.drawerOpen) renderDrawer();
    renderDetail({ keepScroll: true });
  });
}

async function boot() {
  wire();
  setLive(null);
  S.noIntro = readHash().intro === '0';
  $('#verdict').replaceChildren(h('div', { class: 'verdict-main' }, h('span', { class: 'verdict-glyph' }, ico('dots')), h('h1', { class: 'verdict-headline' }, 'Loading review'), h('p', { class: 'verdict-case' }, 'Reading the recorded review.')));

  const config = fetch('api/config', { cache: 'no-cache' }).then((res) => (res.ok ? res.json() : null)).catch(() => null);
  try {
    const idx = await getJSON('cases/index.json');
    S.index = arr(idx).filter((c) => c && typeof c.id === 'string');
  } catch { S.index = []; }
  config.then((c) => setLive(c?.live === true));
  renderIntro();

  const pending = applyHash();
  if (pending) { await pending; return; }
  if (S.index.length) { await openReview({ id: S.index[0].id, base: `cases/${S.index[0].id}/` }); return; }
  showMessage('No reviews to show', 'cases/index.json is missing or empty. Build the demo cases, or run locally with an API key to review your own PDFs.');
}

boot();
