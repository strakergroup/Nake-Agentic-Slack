// Every user-visible word of the simulation lives here so it can be checked
// against the Arbitr voice rules (tests/copy.test.mjs). People, job numbers
// and amounts are fictional.

const DECK_PLAN = [
  { id: 't1', title: 'Read the deck', detail: '', state: 'in_progress' },
  { id: 't2', title: 'Price AI translation', detail: '', state: 'pending' },
  { id: 't3', title: 'Price human review', detail: '', state: 'pending' },
  { id: 't4', title: 'Your approval', detail: '', state: 'pending' },
  { id: 't5', title: 'Translate and deliver here', detail: '', state: 'pending' },
];

const documentRequest = {
  id: 'document', title: 'Document request', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Translate this deck into Japanese and German, and have a person check the legal slide.', file: 'Q3-launch.pptx', next: 's2', note: 'A goal in plain language. No shortcut, no form, no command to remember.' },
    s2: { kind: 'status', status: 'working', next: 's3' },
    s3: { kind: 'plan', planId: 'p1', tasks: DECK_PLAN, next: 's4', note: 'The plan is visible before anything happens. These are Slack’s own task cards.' },
    s4: { kind: 'plan_update', planId: 'p1', taskId: 't1', state: 'complete', detail: '24 slides, English', next: 's5' },
    s5: { kind: 'plan_update', planId: 'p1', taskId: 't2', state: 'complete', detail: '2 languages', next: 's6' },
    s6: { kind: 'plan_update', planId: 'p1', taskId: 't3', state: 'complete', detail: 'Slide 19 only', next: 's7' },
    s7: { kind: 'plan_update', planId: 'p1', taskId: 't4', state: 'waiting', detail: 'Waiting on you', next: 's8' },
    s8: { kind: 'agent', text: 'Here is the plan. Nothing is charged until you approve.', next: 's9' },
    s9: {
      kind: 'quote', unit: 'Credits', total: 165, next: 's10',
      lines: [{ label: 'AI translation, Japanese and German', amount: 120 }, { label: 'Human review, slide 19', amount: 45 }],
      ibm: { footnote: 'Charged to your organization’s wallet.' },
      note: 'The unit is shown as Credits. The real unit is to be confirmed with Wade’s team.',
    },
    s10: { kind: 'status', status: 'waiting', next: 's11', note: 'The session status reads Waiting on you. This is the product working, not an error.' },
    s11: {
      kind: 'choices',
      choices: [
        { id: 'approve', label: 'Approve 165 Credits', style: 'primary', next: 'a1' },
        { id: 'ai_only', label: 'AI only, 120 Credits', style: 'default', next: 'b1' },
        { id: 'decline', label: 'Decline', style: 'default', next: 'c1' },
      ],
      note: 'The click is the only way money moves. The model can propose. It cannot spend. This is enforced in code.',
    },
    a1: { kind: 'plan_update', planId: 'p1', taskId: 't4', state: 'complete', detail: 'Approved by Mika Kato', next: 'a2' },
    a2: { kind: 'status', status: 'working', next: 'a3' },
    a3: { kind: 'plan_update', planId: 'p1', taskId: 't5', state: 'in_progress', detail: 'Japanese, German', next: 'a4', note: 'Progress comes from Straker’s existing services reporting back.' },
    a4: { kind: 'plan_update', planId: 'p1', taskId: 't5', state: 'complete', detail: '2 files', next: 'a5' },
    a5: { kind: 'files', files: ['Q3-launch_ja.pptx', 'Q3-launch_de.pptx'], next: 'a6' },
    a6: { kind: 'agent', disclaimer: true, text: 'The Japanese and German decks are ready. Slide 19 is with a reviewer, and Arbitr will post the reviewed slide in this thread when it comes back.', next: 'a7', note: 'Agent responses carry the AI disclaimer Slack requires.' },
    a7: { kind: 'status', status: 'ready' },
    b1: { kind: 'plan_update', planId: 'p1', taskId: 't3', state: 'complete', detail: 'Skipped at your request', next: 'b2' },
    b2: { kind: 'plan_update', planId: 'p1', taskId: 't4', state: 'complete', detail: 'Approved by Mika Kato, AI only', next: 'b3' },
    b3: { kind: 'plan_update', planId: 'p1', taskId: 't5', state: 'complete', detail: '2 files', next: 'b4' },
    b4: { kind: 'files', files: ['Q3-launch_ja.pptx', 'Q3-launch_de.pptx'], next: 'b5' },
    b5: { kind: 'agent', disclaimer: true, text: 'The Japanese and German decks are ready. No human review was ordered. You can ask for one on any slide later.', next: 'b6' },
    b6: { kind: 'status', status: 'ready' },
    c1: { kind: 'plan_update', planId: 'p1', taskId: 't4', state: 'complete', detail: 'Declined', next: 'c2' },
    c2: { kind: 'agent', text: 'Declined. Nothing was charged and nothing was translated. Ask again whenever you need it.', next: 'c3', note: 'A refusal in three parts: what happened, why it matters, what happens next.' },
    c3: { kind: 'status', status: 'ready' },
  },
};

const jobs = {
  id: 'jobs', title: 'Jobs', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Where’s my job?', next: 's2' },
    s2: { kind: 'plan', planId: 'p1', tasks: [{ id: 't1', title: 'Look up your open jobs', detail: '2 found', state: 'complete' }], next: 's3', note: 'Look-ups run straight away. Only spending and posting need a click.' },
    s3: { kind: 'agent', text: 'You have two open jobs.\nTJ-48213, Q3-launch.pptx, Japanese and German: slide 19 is with a reviewer, due tomorrow.\nTJ-48190, Returns-policy.docx, French: the quote is waiting on you.', next: 's4' },
    s4: { kind: 'user', who: 'Mika Kato', text: 'What’s waiting on me?', next: 's5' },
    s5: { kind: 'agent', text: 'One thing. The quote for Returns-policy.docx, 60 Credits, has been waiting since Thursday.', next: 's6' },
    s6: {
      kind: 'choices',
      choices: [
        { id: 'approve', label: 'Approve 60 Credits', style: 'primary', next: 'a1' },
        { id: 'view', label: 'View quote', style: 'default', next: 'b1' },
        { id: 'later', label: 'Not now', style: 'default', next: 'c1' },
      ],
    },
    a1: { kind: 'agent', disclaimer: true, text: 'Approved. Arbitr has started the French translation and will deliver it in this thread.', next: 'a2' },
    a2: { kind: 'status', status: 'ready' },
    b1: { kind: 'quote', unit: 'Credits', total: 60, lines: [{ label: 'AI translation, French', amount: 60 }], next: 's6b' },
    s6b: {
      kind: 'choices',
      choices: [
        { id: 'approve', label: 'Approve 60 Credits', style: 'primary', next: 'a1' },
        { id: 'later', label: 'Not now', style: 'default', next: 'c1' },
      ],
    },
    c1: { kind: 'agent', text: 'Understood. The quote stays open. Arbitr will remind you once, on Monday.', next: 'c2', note: 'Follow-ups are capped. One reminder, then silence.' },
    c2: { kind: 'status', status: 'ready' },
  },
};

const suggestion = {
  id: 'suggestion', title: 'Private suggestion', surface: 'channel', channel: '#launch-global', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Dana Whitfield', text: 'Launch moves to 14 October. Regional leads, please confirm your readiness by Friday.', next: 's2', note: 'Suggestions are on in this channel because someone turned them on. Off is the default.' },
    s2: {
      kind: 'ephemeral', text: 'Most people in this channel work in Japanese. Want this posted in Japanese as well?',
      choices: [
        { id: 'post', label: 'Post in Japanese', style: 'primary', next: 'a1' },
        { id: 'later', label: 'Not now', style: 'default', next: 'b1' },
        { id: 'never', label: 'Don’t suggest this again', style: 'default', next: 'c1' },
      ],
      note: 'Only Dana sees this. The check used language detection and members’ Slack language settings. Dana’s message has not gone to the LLM.',
    },
    a1: { kind: 'thread_reply', text: 'ローンチは10月14日に変更になりました。各地域のリードは、金曜日までに準備状況をご確認ください。', attribution: 'Translated from English by Arbitr (AI). Posted at Dana Whitfield’s request.', note: 'The LLM is involved only after the click. The post says who asked for it and that it is AI.' },
    b1: { kind: 'ephemeral', text: 'No problem. Nothing was posted.' },
    c1: { kind: 'ephemeral', text: 'Done. Arbitr will not suggest translations to you in this channel. You can change this in the Arbitr Home tab.', note: 'Both opt-outs are on each suggestion. There is also a hard cap on how often Arbitr speaks first.' },
  },
};

const handoff = {
  id: 'handoff', title: 'Hand-off to a form', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Add Japanese subtitles to this video.', file: 'town-hall.mp4', next: 's2' },
    s2: { kind: 'agent', text: 'Subtitles run through the media form. Arbitr has opened it with your file attached.', next: 's3', note: 'Not yet conversational, but never a dead end. The agent understands the request and opens the form that exists today.' },
    s3: {
      kind: 'modal', title: 'Transcribe and translate',
      fields: [
        { label: 'File', value: 'town-hall.mp4' },
        { label: 'Spoken language', value: 'English' },
        { label: 'Subtitle language', value: 'Japanese' },
        { label: 'Output', value: 'Subtitle file (SRT)' },
      ],
      actions: ['Submit', 'Cancel'],
      note: 'This is the existing form, unchanged. IBM’s current habits keep working.',
    },
  },
};

const ibm = {
  id: 'ibm', title: 'IBM workspace', surface: 'dm', compare: true, start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Translate this deck into Japanese.', file: 'Q3-launch.pptx', next: 's2' },
    s2: {
      kind: 'agent', text: 'To price this, Arbitr needs your account. Connect it once and you will not be asked again.', button: 'Connect account', next: 's3',
      ibm: { kind: 'plan', planId: 'p1', text: undefined, button: undefined, tasks: [{ id: 't1', title: 'Read the deck', detail: '24 slides, English', state: 'complete' }, { id: 't2', title: 'Price AI translation', detail: '1 language', state: 'complete' }] },
      note: 'IBM workspaces skip account connect. The Slack identity is the identity. This is existing behaviour, reused, not rebuilt.',
    },
    s3: {
      kind: 'quote', unit: 'Credits', total: 60, lines: [{ label: 'AI translation, Japanese', amount: 60 }],
      footnote: 'Balance after this job: 140 Credits.', link: 'Top up', next: 's4',
      ibm: { footnote: 'Charged to your organization’s wallet.', link: undefined },
      note: 'IBM employees never see a top-up prompt.',
    },
    s4: { kind: 'user', who: 'Mika Kato', text: 'Turn on translation suggestions in #launch-global.', next: 's5' },
    s5: {
      kind: 'agent', text: 'Suggestions are now on in #launch-global. Anyone here can turn them off.',
      ibm: { text: 'In this workspace only an admin can turn on channel suggestions. Arbitr can send the request to your admins.', button: 'Ask an admin' },
      note: 'Admins stay in control inside IBM. A refusal in three parts.',
    },
  },
};

const home = {
  id: 'home', title: 'Home tab', surface: 'home', start: 's1',
  steps: {
    s1: {
      kind: 'home',
      heading: 'Arbitr', subheading: 'Your translation jobs and settings',
      blocks: [
        { type: 'jobs', title: 'Your jobs', rows: [
          { ref: 'TJ-48213', name: 'Q3-launch.pptx', state: 'In review' },
          { ref: 'TJ-48190', name: 'Returns-policy.docx', state: 'Quote waiting' },
          { ref: 'TJ-48102', name: 'Onboarding-guide.docx', state: 'Delivered' },
        ] },
        { type: 'digest', title: 'Digest', isNew: true, options: ['Off', 'Daily', 'Weekly'], selected: 'Off', helper: 'A summary of your jobs, sent by direct message.' },
        { type: 'list', title: 'Muted suggestions', isNew: true, rows: [{ text: '#launch-global, translation offers', action: 'Unmute' }] },
        { type: 'list', title: 'Channel suggestions', isNew: true, adminOnly: true, rows: [{ text: '#launch-global, on, turned on by Dana Whitfield', action: 'Turn off' }] },
      ],
      note: 'The job list is today’s Home tab, unchanged.\n\nDigest is opt-in only. Off by default.\n\nMutes are visible and reversible.\n\nThe channel list is shown to admins only.',
    },
  },
};

export const scenarios = [documentRequest, jobs, suggestion, handoff, ibm, home];
