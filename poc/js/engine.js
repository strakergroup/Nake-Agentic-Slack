// Walks a scripted scenario and produces the list of visible items. No DOM.
export function createPlayer(scenario, { ibm = false } = {}) {
  let state;
  let cursor;

  const resolve = (id) => {
    const raw = scenario.steps[id];
    if (!raw) throw new Error(`unknown step: ${id}`);
    const step = structuredClone(raw);
    if (ibm && step.ibm) Object.assign(step, step.ibm);
    delete step.ibm;
    return { id, ...step };
  };

  const reset = () => {
    cursor = scenario.start;
    state = { items: [], status: 'ready', awaiting: null, done: false, note: '', lastKind: null };
  };

  const apply = (step) => {
    state.note = step.note ?? '';
    state.lastKind = step.kind;
    if (step.kind === 'plan_update') {
      const plan = state.items.find((i) => i.kind === 'plan' && i.planId === step.planId);
      if (!plan) throw new Error(`plan_update before plan: ${step.planId}`);
      const task = plan.tasks.find((t) => t.id === step.taskId);
      if (!task) throw new Error(`unknown task: ${step.taskId}`);
      task.state = step.state;
      if (step.detail !== undefined) task.detail = step.detail;
    } else if (step.kind === 'status') {
      state.status = step.status;
    } else {
      state.items.push(step);
    }
    if (step.reopen) {
      // Go back to buttons that are still live (someone typed instead of clicking).
      const open = state.items.find((i) => i.id === step.reopen);
      if (!open) throw new Error(`reopen before choices: ${step.reopen}`);
      state.awaiting = { stepId: open.id, choices: open.choices };
      cursor = null;
    } else if (step.choices?.length) {
      state.awaiting = { stepId: step.id, choices: step.choices };
      cursor = null;
    } else {
      cursor = step.next ?? null;
      if (cursor === null) state.done = true;
    }
  };

  const next = () => {
    if (state.done || state.awaiting || cursor === null) return;
    apply(resolve(cursor));
  };

  const choose = (choiceId) => {
    if (!state.awaiting) return;
    const choice = state.awaiting.choices.find((c) => c.id === choiceId);
    if (!choice) throw new Error(`unknown choice: ${choiceId}`);
    const item = state.items.find((i) => i.id === state.awaiting.stepId);
    if (choice.typed) {
      // Typing is not clicking: the buttons stay live and this path can only be taken once.
      item.choices = item.choices.filter((c) => c.id !== choiceId);
    } else {
      item.chosen = choiceId;
    }
    state.awaiting = null;
    cursor = choice.next ?? null;
    if (cursor === null) { state.done = true; return; }
    apply(resolve(cursor));
  };

  reset();
  return { get state() { return state; }, next, choose, reset };
}
