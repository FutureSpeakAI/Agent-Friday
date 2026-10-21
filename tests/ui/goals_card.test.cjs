// System > Goals, rendered from the served copy (index.html) with a minimal
// React stand-in: each goal shows its state and what its steps wait on, the
// owner's moves post the right transition, and starting a stopped step again
// asks for a yes first when its outcome is unknown.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

function slice(source, name) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name + ' exists');
  return source.slice(start, source.indexOf('\n}\n', start) + 3);
}

const DATA = { goals: [{
  goal_id: 'goal_synthetic', title: 'Synthetic goal', status: 'needs_owner', state_words: 'needs you',
  done: 0, total: 1, deadline: null, 'budget_cap_mψ': 0, 'spent_mψ': 0, risk: [], completion: null,
  approval_required: false, allowed_routes: [], success_criteria: 'a synthetic draft exists',
  moves: [{ to: 'paused', label: 'Pause' }, { to: 'met_unverified', label: 'Mark it met (my judgment)' }, { to: 'abandoned', label: 'End this goal' }],
  milestones: [{ milestone_id: 'ms_1', name: 'Draft', status: 'blocked', waits: 'Friday stopped while “Draft” was running.',
    can_restart: true, restart_needs_confirmation: true, can_run: false, verified: false }],
}], unreadable: [] };

function render(calls, confirms, data = DATA, open = null, answer = true, def = null, preview = null) {
  const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/\r\n/g, '\n');
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) });
  let slot = 0;
  const slots = [data, '', false, open, def, preview];
  const ctx = vm.createContext({
    React: { createElement }, useState: init => { const i = slot++; return [i in slots ? slots[i] : init, () => {}]; },
    useEffect: () => {}, useCallback: fn => fn, encodeURIComponent, setInterval: () => 0, clearInterval: () => {},
    apiFetch: (url, opts) => { calls.push([url, opts && opts.method, opts && opts.body]); return Promise.resolve({ ok: true, status: 200, json: async () => ({}) }); },
    window: { confirm: () => { confirms.push(1); return answer; }, prompt: () => null, fridayOpenWorkspace: () => {} },
    String, Number, Object,
  });
  vm.runInContext(slice(source, 'GoalsCard'), ctx);
  return ctx.GoalsCard();
}

const all = (n, f, o = []) => { if (n && typeof n === 'object') { if (f(n)) o.push(n); (n.children || []).forEach(c => all(c, f, o)); } return o; };
const label = b => (b.children || []).filter(c => typeof c === 'string').join('');

test('the Goals card shows each goal, its state and what it waits on', () => {
  const tree = render([], []);
  const text = all(tree, () => true).flatMap(n => (n.children || []).filter(c => typeof c === 'string')).join(' ');
  for (const s of ['Goals', 'Synthetic goal', 'needs you', '0 of 1 done', 'Friday stopped while “Draft” was running.']) {
    assert.ok(text.includes(s), 'missing: ' + s);
  }
  assert.equal(all(tree, n => n.props && n.props.role === 'status').length, 1);
});

test('a move posts its transition and a restart of an unknown outcome asks first', () => {
  const calls = [], confirms = [];
  const tree = render(calls, confirms);
  const buttons = all(tree, n => n.type === 'button');
  buttons.find(b => label(b) === 'Pause').props.onClick();
  assert.equal(calls.at(-1)[0], '/api/goals/goal_synthetic/transition');
  assert.deepEqual(JSON.parse(calls.at(-1)[2]), { status: 'paused', reason: 'Pause' });
  buttons.find(b => label(b) === 'Start it again').props.onClick();
  assert.equal(confirms.length, 1, 'the owner was not asked before a restart of an unknown outcome');
  assert.equal(calls.at(-1)[0], '/api/goals/goal_synthetic/milestones/ms_1/restart');
  assert.deepEqual(JSON.parse(calls.at(-1)[2]), { confirm_unknown_outcome: true });
});

test('defining a goal: the summary first, then create only that summary (17.4)', () => {
  const def = { title: 'Synthetic', intent: '', done: 'a report exists', proof: 'report.md', stop: 'it needs money', steps: 'Draft\nCheck',
    people: '', deadline: '', minutes: '20', does: ['read', 'draft'], autonomy: { read: 'on_its_own', draft: 'on_its_own', write: 'ask_me', send: 'ask_me', spend: 'ask_me', publish: 'ask_me', delete: 'never' } };
  let calls = [];
  let buttons = all(render(calls, [], DATA, null, true, def, null), n => n.type === 'button');
  assert.ok(!buttons.some(b => label(b) === 'Create this goal as summarised'), 'a goal could be created before its summary was shown');
  buttons.find(b => label(b) === 'Show me the summary').props.onClick();
  assert.equal(calls.at(-1)[0], '/api/goals/draft');
  const spec = JSON.parse(calls.at(-1)[2]);
  assert.equal(spec.completion_condition, 'a report exists');
  assert.deepEqual(spec.milestones.map(m => m.name), ['Draft', 'Check']);
  assert.equal(spec.budget.runtime_minutes, 20);
  // With open questions there is no create button.
  buttons = all(render([], [], DATA, null, true, def, { questions: [{ key: 'people', ask: 'Who will it reach?' }], summary: 'Goal: Synthetic.', summary_id: 'abc' }), n => n.type === 'button');
  assert.ok(!buttons.some(b => label(b) === 'Create this goal as summarised'));
  calls = [];
  buttons = all(render(calls, [], DATA, null, true, def, { questions: [], summary: 'Goal: Synthetic.', summary_id: 'abc' }), n => n.type === 'button');
  buttons.find(b => label(b) === 'Create this goal as summarised').props.onClick();
  assert.equal(calls.at(-1)[0], '/api/goals/define');
  assert.equal(JSON.parse(calls.at(-1)[2]).summary_id, 'abc');
});

test('a restart whose outcome is known does not ask and claims nothing', () => {
  const calls = [], confirms = [];
  const known = JSON.parse(JSON.stringify(DATA));
  known.goals[0].milestones[0].restart_needs_confirmation = false;
  const tree = render(calls, confirms, known);
  all(tree, n => n.type === 'button').find(b => label(b) === 'Start it again').props.onClick();
  assert.equal(confirms.length, 0);
  assert.deepEqual(JSON.parse(calls.at(-1)[2]), {});
});

test('loosening where a goal may run, or its asking, needs a yes; tightening does not', () => {
  const tight = JSON.parse(JSON.stringify(DATA));
  tight.goals[0].allowed_routes = ['local_only'];
  tight.goals[0].approval_required = true;
  // Refused: nothing is sent.
  let calls = [], confirms = [];
  let buttons = all(render(calls, confirms, tight, 'goal_synthetic', false), n => n.type === 'button');
  buttons.find(b => label(b) === 'with any routing').props.onClick();
  buttons.find(b => label(b) === 'Stop asking before every step').props.onClick();
  assert.equal(confirms.length, 2);
  assert.equal(calls.length, 0, 'a looser setting was sent without a yes');
  // Tightening goes straight through.
  const loose = JSON.parse(JSON.stringify(DATA));
  loose.goals[0].allowed_routes = [];
  calls = []; confirms = [];
  buttons = all(render(calls, confirms, loose, 'goal_synthetic'), n => n.type === 'button');
  buttons.find(b => label(b) === 'only on this PC').props.onClick();
  assert.equal(confirms.length, 0);
  assert.deepEqual(JSON.parse(calls.at(-1)[2]), { allowed_routes: ['local_only'] });
});
