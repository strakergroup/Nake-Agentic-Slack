import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkCopy } from '../js/copy-rules.js';

const rules = (text) => checkCopy(text).map((v) => v.rule);

test('clean copy passes', () => {
  assert.deepEqual(rules('Arbitr has started the French translation.'), []);
});
test('flags em and en dashes', () => {
  assert.deepEqual(rules('Ready — two files'), ['no-dash']);
  assert.deepEqual(rules('Ready – two files'), ['no-dash']);
});
test('flags emoji', () => {
  assert.deepEqual(rules('Done \u{1F44D}'), ['no-emoji']);
});
test('flags lowercase product name but not commands or paths', () => {
  assert.deepEqual(rules('ask arbitr to translate'), ['capitalise-arbitr']);
  assert.deepEqual(rules('Type /arbitr to start'), []);
});
test('flags exclamation marks', () => {
  assert.deepEqual(rules('Your file is ready!'), ['no-exclamation']);
});
test('flags the word users', () => {
  assert.deepEqual(rules('Users can mute this'), ['no-users']);
});
test('flags retired and off-limits words', () => {
  assert.deepEqual(rules('A seamless experience'), ['retired-word']);
  assert.deepEqual(rules('Your trust score is 92'), ['off-limits-claim']);
  assert.deepEqual(rules('Explained by Glass Box'), ['off-limits-claim']);
});
test('flags retired names', () => {
  assert.deepEqual(rules('Connect to Verify'), ['retired-name']);
  assert.deepEqual(rules('Open LanguageCloud'), ['retired-name']);
});

// ---- every word in the simulation ----
import { scenarios } from '../js/scenarios.js';
import { readFileSync, existsSync } from 'node:fs';

const SKIP = ['id', 'kind', 'next', 'start', 'surface', 'planId', 'taskId', 'state', 'status', 'style', 'type'];
const strings = (value, out = []) => {
  if (typeof value === 'string') out.push(value);
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === 'object') Object.entries(value).forEach(([k, v]) => { if (!SKIP.includes(k)) strings(v, out); });
  return out;
};

test('every scenario string passes the voice rules', () => {
  const bad = scenarios.flatMap((s) => strings(s).flatMap((t) => checkCopy(t).map((v) => `${s.id}: ${v.rule}: ${t}`)));
  assert.deepEqual(bad, []);
});

test('the scenarios carry a meaningful amount of copy', () => {
  assert.ok(scenarios.flatMap((s) => strings(s)).length > 100);
});

test('index.html text passes the voice rules', () => {
  const file = new URL('../index.html', import.meta.url);
  assert.ok(existsSync(file), 'index.html exists');
  const html = readFileSync(file, 'utf8').replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>|<[^>]+>/g, ' ');
  assert.deepEqual(checkCopy(html).map((v) => v.rule), []);
});
