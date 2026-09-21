import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPlayer } from '../js/engine.js';

const demo = {
  id: 'demo', title: 'Demo', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Hello', next: 's2', note: 'first' },
    s2: { kind: 'plan', planId: 'p1', tasks: [{ id: 't1', title: 'Read', detail: '', state: 'in_progress' }], next: 's3' },
    s3: { kind: 'plan_update', planId: 'p1', taskId: 't1', state: 'complete', detail: '24 slides', next: 's4' },
    s4: { kind: 'status', status: 'waiting', next: 's5' },
    s5: { kind: 'choices', choices: [{ id: 'yes', label: 'Approve', style: 'primary', next: 'a1' }, { id: 'no', label: 'Decline', style: 'default', next: 'c1' }] },
    a1: { kind: 'agent', text: 'Approved.', disclaimer: true, ibm: { text: 'Approved for IBM.' } },
    c1: { kind: 'agent', text: 'Declined.' },
  },
};

test('starts empty, ready, not done', () => {
  const p = createPlayer(demo);
  assert.deepEqual(p.state.items, []);
  assert.equal(p.state.status, 'ready');
  assert.equal(p.state.done, false);
});

test('next() reveals one step and exposes its note', () => {
  const p = createPlayer(demo);
  p.next();
  assert.equal(p.state.items.length, 1);
  assert.equal(p.state.items[0].text, 'Hello');
  assert.equal(p.state.note, 'first');
});

test('plan_update mutates the earlier plan instead of adding an item', () => {
  const p = createPlayer(demo);
  p.next(); p.next(); p.next();
  assert.equal(p.state.items.length, 2);
  assert.equal(p.state.items[1].tasks[0].state, 'complete');
  assert.equal(p.state.items[1].tasks[0].detail, '24 slides');
});

test('status steps change status without adding an item', () => {
  const p = createPlayer(demo);
  p.next(); p.next(); p.next(); p.next();
  assert.equal(p.state.status, 'waiting');
  assert.equal(p.state.items.length, 2);
});

test('choices block next() until choose() is called', () => {
  const p = createPlayer(demo);
  for (let i = 0; i < 5; i++) p.next();
  assert.equal(p.state.awaiting.choices.length, 2);
  p.next();
  assert.equal(p.state.items.at(-1).kind, 'choices');
  p.choose('no');
  assert.equal(p.state.awaiting, null);
  assert.equal(p.state.items.at(-1).text, 'Declined.');
  assert.equal(p.state.items.at(-2).chosen, 'no');
  assert.equal(p.state.done, true);
});

test('choose() with an unknown id throws', () => {
  const p = createPlayer(demo);
  for (let i = 0; i < 5; i++) p.next();
  assert.throws(() => p.choose('maybe'), /unknown choice/);
});

test('ibm mode merges overrides', () => {
  const p = createPlayer(demo, { ibm: true });
  for (let i = 0; i < 5; i++) p.next();
  p.choose('yes');
  assert.equal(p.state.items.at(-1).text, 'Approved for IBM.');
});

test('reset() returns to the start', () => {
  const p = createPlayer(demo);
  p.next(); p.next();
  p.reset();
  assert.deepEqual(p.state.items, []);
  assert.equal(p.state.done, false);
});

test('the scenario object is never mutated', () => {
  const before = JSON.stringify(demo);
  const p = createPlayer(demo);
  p.next(); p.next(); p.next();
  assert.equal(JSON.stringify(demo), before);
});
