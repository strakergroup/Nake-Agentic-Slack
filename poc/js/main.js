import { scenarios } from './scenarios.js';
import { createPlayer } from './engine.js';
import { render } from './render.js';

const $ = (id) => document.getElementById(id);
const stage = $('stage');
const notes = $('notes');
const counter = $('counter');
const nextBtn = $('next');
const ibmSwitch = $('ibm-switch');
const ibmToggle = $('ibm-toggle');

const STORE_KEY = 'arbitr-slack-poc:scenario';
const remembered = () => { try { return localStorage.getItem(STORE_KEY); } catch { return null; } };
const remember = (id) => { try { localStorage.setItem(STORE_KEY, id); } catch { /* storage is optional */ } };

let scenario = scenarios.find((s) => s.id === remembered()) ?? scenarios[0];
let ibm = false;
let players = [];
let timer = null;
const BEAT_MS = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 600;

function start() {
  const modes = scenario.compare ? [false, true] : [ibm];
  players = modes.map((mode) => ({ mode, player: createPlayer(scenario, { ibm: mode }) }));
  clearTimeout(timer);
  timer = null;
  if (scenario.surface === 'home') advance();
  else draw();
}

function advance() {
  timer = null;
  if (players.every(({ player }) => player.state.done || player.state.awaiting)) return;
  const before = players.map(({ player }) => player.state.items.length);
  players.forEach(({ player }) => player.next());
  draw(players.map(({ player }, i) => player.state.items.length > before[i]));
  // Status changes and plan updates without a presenter note are quick beats:
  // play them on a timer so the task cards tick over like the real thing.
  const quiet = (p) => !p.state.done && !p.state.awaiting && !p.state.note && ['status', 'plan_update'].includes(p.state.lastKind);
  if (players.every(({ player }) => quiet(player))) {
    timer = setTimeout(advance, BEAT_MS);
    nextBtn.disabled = true;
  }
}

function choose(player, id) {
  const before = player.state.items.length;
  player.choose(id);
  draw(players.map((p) => p.player === player && player.state.items.length > before));
  if (!player.state.done && !player.state.awaiting && !player.state.note && ['status', 'plan_update'].includes(player.state.lastKind)) {
    timer = setTimeout(advance, BEAT_MS);
    nextBtn.disabled = true;
  }
}

function draw(fresh = []) {
  stage.replaceChildren();
  stage.classList.toggle('compare', Boolean(scenario.compare));
  players.forEach(({ mode, player }, i) => {
    render(stage, scenario, player.state, {
      fresh: Boolean(fresh[i]),
      onChoose: (id) => choose(player, id),
      label: scenario.compare ? (mode ? 'IBM workspace' : 'Other workspaces') : undefined,
    });
  });
  const lead = players.at(-1).player.state;
  notes.textContent = lead.note;
  const waiting = players.some(({ player }) => player.state.awaiting);
  const finished = players.every(({ player }) => player.state.done);
  nextBtn.disabled = waiting || finished;
  nextBtn.textContent = finished ? 'End of scenario' : waiting ? 'Choose in Slack' : 'Next';
  counter.textContent = `${String(scenarios.indexOf(scenario) + 1).padStart(2, '0')} / ${String(scenarios.length).padStart(2, '0')}`;
  ibmToggle.hidden = Boolean(scenario.compare) || scenario.surface === 'home' || scenario.surface === 'channel';
  document.querySelectorAll('.scenario-btn').forEach((b) => b.setAttribute('aria-current', String(b.dataset.id === scenario.id)));
}

function buildPicker() {
  const nav = $('scenarios');
  scenarios.forEach((s, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'scenario-btn';
    b.dataset.id = s.id;
    const num = document.createElement('span');
    num.className = 'num';
    num.textContent = String(i + 1).padStart(2, '0');
    b.append(num, s.title);
    b.addEventListener('click', () => { scenario = s; remember(s.id); restart(); });
    nav.append(b);
  });
}

function restart() {
  start();
}

nextBtn.addEventListener('click', advance);
$('restart').addEventListener('click', restart);
ibmSwitch.addEventListener('click', () => {
  ibm = !ibm;
  ibmSwitch.setAttribute('aria-checked', String(ibm));
  restart();
});
document.addEventListener('keydown', (e) => {
  if (e.target.closest('button, input, select, textarea')) return;
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); advance(); }
  if (e.key.toLowerCase() === 'r') restart();
});

buildPicker();
restart();
