// Draws player state as an imitation Slack surface. All text goes in through
// textContent; nothing here parses HTML.
const LENS = 'vendor/arbitr-ds/assets/logo/lens-device-primary.svg';
const DISCLAIMER = 'AI output can be inaccurate. Human review is available on any job.';
const STATUS_LABEL = { working: 'Working', waiting: 'Waiting on you', ready: 'Ready' };
const TASK_LABEL = { pending: 'Not started', in_progress: 'Working', complete: 'Done', error: 'Failed' };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const initials = (name) => name.split(' ').map((p) => p[0]).join('').slice(0, 2);

function message({ avatar, name, tag, body }) {
  const row = el('div', 'msg');
  row.append(avatar);
  const main = el('div', 'msg-main');
  const head = el('div', 'msg-head');
  head.append(el('span', 'msg-name', name));
  if (tag) head.append(el('span', 'msg-tag', tag));
  main.append(head, ...body);
  row.append(main);
  return row;
}

function personAvatar(name) {
  const a = el('div', 'avatar avatar-person', initials(name));
  a.setAttribute('aria-hidden', 'true');
  return a;
}

function arbitrAvatar() {
  const a = el('div', 'avatar avatar-app');
  const img = el('img');
  img.src = LENS;
  img.alt = '';
  a.append(img);
  return a;
}

function fileChip(name) {
  const chip = el('span', 'file-chip');
  chip.append(el('span', 'file-kind', name.split('.').pop().toUpperCase()), el('span', 'file-name', name));
  return chip;
}

function paragraphs(text, className = 'msg-text') {
  return text.split('\n').map((line) => el('p', className, line));
}

function choiceRow(item, onChoose) {
  const row = el('div', 'choices');
  for (const c of item.choices) {
    // A typed reply is not a Slack button: it is a presenter control, drawn apart from the real ones.
    const b = el('button', c.typed ? 'sbtn sbtn-typed' : `sbtn ${c.style === 'primary' ? 'sbtn-primary' : ''}`.trim(), c.label);
    b.type = 'button';
    if (item.chosen) {
      b.disabled = true;
      if (item.chosen === c.id) b.classList.add('sbtn-chosen');
    } else {
      b.addEventListener('click', () => onChoose(c.id));
    }
    row.append(b);
  }
  return row;
}

const renderers = {
  user(item) {
    const body = paragraphs(item.text);
    if (item.lang) body.forEach((p) => { p.lang = item.lang; });
    if (item.file) body.push(fileChip(item.file));
    return message({ avatar: personAvatar(item.who), name: item.who, body });
  },

  agent(item) {
    const body = paragraphs(item.text);
    if (item.lang) body.forEach((p) => { p.lang = item.lang; });
    if (item.button) {
      const b = el('button', 'sbtn', item.button);
      b.type = 'button';
      b.disabled = true;
      body.push(b);
    }
    if (item.disclaimer) body.push(el('p', 'disclaimer', DISCLAIMER));
    return message({ avatar: arbitrAvatar(), name: 'Arbitr', tag: 'AI agent', body });
  },

  plan(item) {
    const card = el('div', 'plan');
    card.append(el('div', 'plan-title', item.title || 'Plan'));
    for (const t of item.tasks) {
      const row = el('div', `task task-${t.state}`);
      row.append(el('span', 'task-state', TASK_LABEL[t.state]), el('span', 'task-title', t.title), el('span', 'task-detail', t.detail));
      card.append(row);
    }
    return message({ avatar: arbitrAvatar(), name: 'Arbitr', tag: 'AI agent', body: [card] });
  },

  quote(item) {
    const card = el('div', 'quote');
    for (const line of item.lines) {
      const row = el('div', 'quote-line');
      row.append(el('span', '', line.label), el('span', 'amount', line.amount));
      card.append(row);
    }
    const total = el('div', 'quote-line quote-total');
    total.append(el('span', '', 'Total'), el('span', 'amount', item.total));
    card.append(total);
    if (item.footnote) card.append(el('p', 'quote-foot', item.footnote));
    const wrap = el('div', 'indent');
    wrap.append(card);
    return wrap;
  },

  choices(item, onChoose) {
    const wrap = el('div', 'indent');
    wrap.append(choiceRow(item, onChoose));
    return wrap;
  },

  files(item) {
    const wrap = el('div', 'indent files');
    item.files.forEach((f) => wrap.append(fileChip(f)));
    return wrap;
  },

  ephemeral(item, onChoose) {
    const box = el('div', 'ephemeral');
    box.append(el('p', 'ephemeral-label', item.label || 'Only visible to you'));
    box.append(message({ avatar: arbitrAvatar(), name: 'Arbitr', tag: 'AI agent', body: [...paragraphs(item.text), ...(item.choices ? [choiceRow(item, onChoose)] : [])] }));
    return box;
  },

  thread_reply(item) {
    const body = paragraphs(item.text);
    body[0].lang = 'ja';
    body.push(el('p', 'attribution', item.attribution));
    return message({ avatar: arbitrAvatar(), name: 'Arbitr', tag: 'AI agent', body });
  },

  prompts(item) {
    const wrap = el('div', 'prompts');
    wrap.append(el('p', 'prompts-title', item.title));
    const row = el('div', 'choices');
    for (const p of item.prompts) {
      const b = el('button', 'sbtn sbtn-prompt', p);
      b.type = 'button';
      b.disabled = true;
      row.append(b);
    }
    wrap.append(row);
    return wrap;
  },

  divider(item) {
    const d = el('div', 'day-divider');
    d.append(el('span', '', item.label));
    return d;
  },

  notice(item) {
    const n = el('div', 'notice');
    n.append(el('span', 'notice-app', 'Slack notification'), el('span', 'notice-text', item.text));
    return n;
  },

  system(item) {
    return el('p', 'system-line', item.text);
  },

  existing(item) {
    const body = paragraphs(item.text);
    return message({ avatar: arbitrAvatar(), name: 'Arbitr', tag: 'Existing app message', body });
  },

  feedback() {
    const wrap = el('div', 'indent feedback');
    for (const label of ['Helpful', 'Not helpful']) {
      const b = el('button', 'sbtn sbtn-quiet', label);
      b.type = 'button';
      b.addEventListener('click', () => {
        wrap.querySelectorAll('button').forEach((x) => { x.disabled = true; });
        b.classList.add('sbtn-chosen');
      });
      wrap.append(b);
    }
    return wrap;
  },

  preview(item) {
    const box = el('div', 'preview');
    box.append(el('p', 'preview-where', item.where));
    const text = el('p', 'msg-text', item.text);
    text.lang = 'ja';
    box.append(text, el('p', 'attribution', item.attribution));
    const wrap = el('div', 'indent');
    wrap.append(box);
    return wrap;
  },

  modal(item) {
    const scrim = el('div', 'modal-scrim');
    const modal = el('div', 'modal');
    modal.setAttribute('role', 'group');
    modal.setAttribute('aria-label', item.title);
    modal.append(el('div', 'modal-title', item.title));
    for (const f of item.fields) {
      const field = el('div', 'field');
      field.append(el('span', 'field-label', f.label), el('span', 'field-value', f.value));
      modal.append(field);
    }
    const actions = el('div', 'modal-actions');
    item.actions.forEach((a, i) => {
      const b = el('button', `sbtn ${i === 0 ? 'sbtn-primary' : ''}`.trim(), a);
      b.type = 'button';
      b.disabled = true;
      actions.append(b);
    });
    modal.append(actions);
    scrim.append(modal);
    return scrim;
  },

  home(item) {
    const wrap = el('div', 'home');
    const head = el('div', 'home-head');
    head.append(arbitrAvatar());
    const titles = el('div');
    titles.append(el('div', 'home-title', item.heading), el('div', 'home-sub', item.subheading));
    head.append(titles);
    wrap.append(head);
    for (const block of item.blocks) {
      const section = el('section', 'home-block');
      const title = el('div', 'home-block-title', block.title);
      if (block.tag) title.append(el('span', `pill pill-${block.tag.toLowerCase()}`, block.tag));
      if (block.adminOnly) title.append(el('span', 'pill', 'Admins only'));
      section.append(title);
      if (block.type === 'jobs') {
        for (const r of block.rows) {
          const row = el('div', 'home-row');
          row.append(el('span', 'mono', r.ref), el('span', 'grow', r.name), el('span', 'pill', r.state));
          section.append(row);
        }
      } else if (block.type === 'digest') {
        const group = el('div', 'segmented');
        group.setAttribute('role', 'radiogroup');
        group.setAttribute('aria-label', block.title);
        for (const opt of block.options) {
          const b = el('button', 'seg', opt);
          b.type = 'button';
          b.setAttribute('role', 'radio');
          b.setAttribute('aria-checked', String(opt === block.selected));
          b.addEventListener('click', () => {
            group.querySelectorAll('.seg').forEach((s) => s.setAttribute('aria-checked', String(s === b)));
          });
          group.append(b);
        }
        section.append(group, el('p', 'home-helper', block.helper));
      } else {
        for (const r of block.rows) {
          const row = el('div', 'home-row');
          const b = el('button', 'sbtn', r.action);
          b.type = 'button';
          b.disabled = true;
          row.append(el('span', 'grow', r.text), b);
          section.append(row);
        }
      }
      wrap.append(section);
    }
    return wrap;
  },
};

function header(scenario, state) {
  const bar = el('div', 'slack-header');
  if (scenario.surface === 'channel') {
    bar.append(el('span', 'slack-title', scenario.channel));
    bar.append(el('span', 'slack-sub', 'Channel'));
  } else if (scenario.surface === 'home') {
    bar.append(el('span', 'slack-title', 'Arbitr'));
    const tabs = el('span', 'tabs');
    tabs.append(el('span', 'tab tab-active', 'Home'), el('span', 'tab', 'Messages'), el('span', 'tab', 'About'));
    bar.append(tabs);
  } else {
    bar.append(el('span', 'slack-title', 'Arbitr'));
    bar.append(el('span', 'slack-sub', `Session: ${scenario.title}`));
    const pill = el('span', `status status-${state.status}`, STATUS_LABEL[state.status]);
    bar.append(pill);
  }
  return bar;
}

export function render(stageEl, scenario, state, { onChoose, label, fresh } = {}) {
  const pane = el('div', 'slack-pane');
  if (label) pane.append(el('div', 'pane-label', label));
  pane.append(header(scenario, state));
  const stream = el('div', 'stream');
  let lastNode = null;
  if (state.items.length === 0) stream.append(el('p', 'empty', 'Select Next to begin.'));
  for (const item of state.items) {
    const draw = renderers[item.kind];
    if (!draw) throw new Error(`no renderer for kind: ${item.kind}`);
    let node = draw(item, onChoose);
    if (item.inThread || item.kind === 'thread_reply') {
      // Consecutive thread items share one thread container under the parent message.
      let thread = stream.lastElementChild;
      if (!thread || !thread.classList.contains('thread')) {
        thread = el('div', 'thread');
        thread.append(el('p', 'thread-label', 'Thread'));
        stream.append(thread);
      }
      thread.append(node);
      node = thread;
    } else {
      stream.append(node);
    }
    lastNode = node;
  }
  if (fresh && lastNode) lastNode.classList.add('is-new');
  pane.append(stream);
  if (scenario.surface !== 'home') {
    const composer = el('div', 'composer', scenario.surface === 'channel' ? `Message ${scenario.channel}` : 'Message Arbitr');
    composer.setAttribute('aria-hidden', 'true');
    pane.append(composer);
  }
  stageEl.append(pane);
  stream.scrollTop = stream.scrollHeight;
}
