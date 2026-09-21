// Every user-visible word of the simulation lives here so it can be checked
// against the Arbitr voice rules (tests/copy.test.mjs). People, job numbers
// and amounts are fictional. Version 2, after the mock-up review of 2026-09-21:
// the agent speaks as "I" in conversation, asks before guessing, accepts
// amendments in plain language, and the plans match what Slack can render
// (buttons only when a streamed message ends, so Quote and Delivery are two plans).

const DECK = 'Q3-launch.pptx';
const UNITS_NOTE = 'Amounts show dollars, which is what the quote system returns today, and Credits. The Credits figures are illustrative until the conversion rule is confirmed.';

const documentRequest = {
  id: 'document', title: 'Document request', surface: 'dm', start: 'p0',
  steps: {
    p0: { kind: 'prompts', title: 'What do you need translated?', prompts: ['Translate a document', 'Where’s my job?', 'What’s waiting on me?'], next: 's1', note: 'Suggested prompts are a native Slack feature at the start of an agent session.' },
    s1: { kind: 'user', who: 'Mika Kato', text: 'Translate this deck into Japanese and German, and have a person check the legal slide.', file: DECK, next: 's2', note: 'A goal in plain language. No shortcut, no form, no command to remember.' },
    s2: { kind: 'status', status: 'working', next: 's3' },
    s3: { kind: 'agent', text: 'I can’t read inside the deck, so I can’t tell which slide is the legal one. A reviewer can check the whole Japanese deck, or you can send that slide as its own file. Which would you like?', next: 's3b', note: 'The language model sees the request and the file name, never the contents. So the agent asks; it does not guess. Human review is per file in today’s system.' },
    s3b: { kind: 'status', status: 'waiting', next: 's4' },
    s4: { kind: 'user', who: 'Mika Kato', text: 'Check the whole Japanese deck. Actually, add French too.', next: 's4b', note: 'A mid-plan amendment, in plain language. This is the difference between an agent and a form.' },
    s4b: { kind: 'status', status: 'working', next: 's5' },
    s5: { kind: 'plan', planId: 'quote', title: 'Quote', next: 's6', tasks: [
      { id: 'q1', title: 'Check the file', detail: '24 slides, English', state: 'complete' },
      { id: 'q2', title: 'Quote', detail: 'Japanese, German, French, plus review', state: 'in_progress' },
    ], note: 'Task cards are Slack’s own. They support not started, working, done and failed. There is no waiting state, so waiting is shown by the session status, not by a card.' },
    s6: { kind: 'plan_update', planId: 'quote', taskId: 'q2', state: 'complete', detail: 'Ready', next: 's7' },
    s7: { kind: 'quote', next: 's8', lines: [
      { label: 'AI translation: Japanese, German, French', amount: 'USD 18.00 (180 Credits)' },
      { label: 'Human review: Japanese deck', amount: 'USD 96.00 (960 Credits)' },
    ], total: 'USD 114.00 (1,140 Credits)', ibm: { footnote: 'Charged to your organization’s wallet.' }, note: UNITS_NOTE },
    s8: { kind: 'status', status: 'waiting', next: 's9', note: 'Waiting on you is the product working, not an error.' },
    s9: { kind: 'user', who: 'Mika Kato', text: 'yes, go ahead', next: 's10', note: 'People will type yes. The agent has to handle it.' },
    s10: { kind: 'agent', text: 'I can’t take a typed yes for anything that costs money. It has to be the button, so there is a record of who approved what. Here it is again.', next: 's11' },
    s11: { kind: 'choices', choices: [
      { id: 'approve', label: 'Approve USD 114.00', style: 'primary', next: 'a1' },
      { id: 'ai_only', label: 'AI only, USD 18.00', style: 'default', next: 'b1' },
      { id: 'decline', label: 'Decline', style: 'default', next: 'c1' },
    ], note: 'Slack only lets buttons attach when a streamed message ends. So the Quote plan is one message and Delivery is a second one. The click is verified in code: right person, right quote, not expired.' },
    a1: { kind: 'status', status: 'working', next: 'a2' },
    a2: { kind: 'plan', planId: 'delivery', title: 'Delivery', next: 'a3', tasks: [
      { id: 'd1', title: 'Translate: Japanese, German, French', detail: '', state: 'in_progress' },
      { id: 'd2', title: 'Deliver here', detail: '', state: 'pending' },
      { id: 'd3', title: 'Human review: Japanese deck', detail: '', state: 'pending' },
    ], note: 'A second message with its own plan. Progress comes from Straker’s existing services reporting back.' },
    a3: { kind: 'plan_update', planId: 'delivery', taskId: 'd1', state: 'complete', detail: '3 languages', next: 'a4' },
    a4: { kind: 'plan_update', planId: 'delivery', taskId: 'd2', state: 'complete', detail: '3 files', next: 'a5' },
    a5: { kind: 'plan_update', planId: 'delivery', taskId: 'd3', state: 'in_progress', detail: 'With a reviewer', next: 'a6' },
    a6: { kind: 'files', files: ['Q3-launch_ja.pptx', 'Q3-launch_de.pptx', 'Q3-launch_fr.pptx'], next: 'a7' },
    a7: { kind: 'agent', disclaimer: true, text: 'All three decks are here. The Japanese deck is with a reviewer. I’ll post the reviewed version in this thread when it comes back, usually within a working day.', next: 'a8', note: 'The AI disclaimer sits on deliverables only, not on every message.' },
    a8: { kind: 'feedback', next: 'a9', note: 'Feedback buttons are a native Slack block. They go on deliveries.' },
    a9: { kind: 'status', status: 'ready' },
    b1: { kind: 'status', status: 'working', next: 'b2' },
    b2: { kind: 'plan', planId: 'delivery', title: 'Delivery', next: 'b3', tasks: [
      { id: 'd1', title: 'Translate: Japanese, German, French', detail: '', state: 'in_progress' },
      { id: 'd2', title: 'Deliver here', detail: '', state: 'pending' },
    ], note: 'No review was ordered, so the review step is simply not in this plan. Nothing is marked done that was skipped.' },
    b3: { kind: 'plan_update', planId: 'delivery', taskId: 'd1', state: 'complete', detail: '3 languages', next: 'b4' },
    b4: { kind: 'plan_update', planId: 'delivery', taskId: 'd2', state: 'complete', detail: '3 files', next: 'b5' },
    b5: { kind: 'files', files: ['Q3-launch_ja.pptx', 'Q3-launch_de.pptx', 'Q3-launch_fr.pptx'], next: 'b6' },
    b6: { kind: 'agent', disclaimer: true, text: 'All three decks are here. No human review was ordered. You can ask for one on any of them later.', next: 'b7' },
    b7: { kind: 'feedback', next: 'b8' },
    b8: { kind: 'status', status: 'ready' },
    c1: { kind: 'agent', text: 'Declined. Nothing was charged and nothing was translated. Ask again whenever you need it.', next: 'c2', note: 'A refusal or a stop in three parts: what happened, why it matters, what happens next.' },
    c2: { kind: 'status', status: 'ready' },
  },
};

const nextDay = {
  id: 'next-day', title: 'The next day', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'divider', label: 'Tuesday', next: 's2' },
    s2: { kind: 'agent', text: 'All three decks are here. The Japanese deck is with a reviewer. I’ll post the reviewed version in this thread when it comes back, usually within a working day.', next: 's3', note: 'Where scenario 1 ended. The session stays open. Nothing is running and nobody is waiting on Mika.' },
    s3: { kind: 'divider', label: 'Wednesday, 09:12', next: 's4' },
    s4: { kind: 'notice', text: 'Arbitr: the reviewed Japanese deck is ready', next: 's5', note: 'The reviewer finished overnight. Straker’s service reports it, the session wakes, and Mika gets an ordinary Slack notification because the reply lands in a thread she is part of.' },
    s5: { kind: 'plan', planId: 'review', title: 'Human review', next: 's6', tasks: [
      { id: 'r1', title: 'Human review: Japanese deck', detail: 'Back from the reviewer', state: 'complete' },
      { id: 'r2', title: 'Deliver here', detail: '1 file', state: 'complete' },
    ] },
    s6: { kind: 'files', files: ['Q3-launch_ja_reviewed.pptx'], next: 's7' },
    s7: { kind: 'agent', text: 'The reviewed Japanese deck is back. It replaces yesterday’s AI version. I can’t see what the reviewer changed, so check slide 19 yourself.', next: 's8', note: 'Same thread, same session, a day later. The agent stays candid about what it cannot see.' },
    s8: { kind: 'feedback', next: 's9' },
    s9: { kind: 'user', who: 'Mika Kato', text: 'Good. Send the German one for review as well.', next: 's10', note: 'The session picks up where it left off. No restating the file or the languages.' },
    s10: { kind: 'quote', lines: [{ label: 'Human review: German deck', amount: 'USD 96.00 (960 Credits)' }], total: 'USD 96.00 (960 Credits)', next: 's11', note: UNITS_NOTE },
    s11: { kind: 'status', status: 'waiting', next: 's12' },
    s12: { kind: 'choices', choices: [
      { id: 'approve', label: 'Approve USD 96.00', style: 'primary', next: 'a1' },
      { id: 'decline', label: 'Decline', style: 'default', next: 'c1' },
    ] },
    a1: { kind: 'agent', text: 'Approved. The German deck is with a reviewer. I’ll post it here when it comes back.', next: 'a2' },
    a2: { kind: 'status', status: 'ready' },
    c1: { kind: 'agent', text: 'Declined. Nothing was charged. The AI version of the German deck stays as it is.', next: 'c2' },
    c2: { kind: 'status', status: 'ready' },
  },
};

const jobs = {
  id: 'jobs', title: 'Jobs', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Where’s my job?', next: 's2' },
    s2: { kind: 'status', status: 'working', next: 's3', note: 'A simple look-up shows the working status only. A one-line plan card would be noise.' },
    s3: { kind: 'agent', text: 'You have two open jobs.\nTJ-48213, Q3-launch.pptx, Japanese, German and French: the Japanese deck is with a reviewer, due tomorrow.\nTJ-48190, Returns-policy.docx, French: the quote is waiting on you.', next: 's3b' },
    s3b: { kind: 'status', status: 'ready', next: 's4' },
    s4: { kind: 'user', who: 'Mika Kato', text: 'What’s waiting on me?', next: 's5' },
    s5: { kind: 'agent', text: 'One thing. The quote for Returns-policy.docx, USD 6.00 (60 Credits), has been waiting since Thursday.', next: 's6', note: UNITS_NOTE },
    s6: { kind: 'choices', choices: [
      { id: 'approve', label: 'Approve USD 6.00', style: 'primary', next: 'a1' },
      { id: 'view', label: 'View quote', style: 'default', next: 'b1' },
      { id: 'later', label: 'Not now', style: 'default', next: 'c1' },
    ] },
    a1: { kind: 'agent', text: 'Approved. The French translation has started. I’ll deliver it in this thread.', next: 'a2', note: 'No AI disclaimer here: a confirmation is not AI output.' },
    a2: { kind: 'status', status: 'ready' },
    b1: { kind: 'quote', lines: [{ label: 'AI translation: French', amount: 'USD 6.00 (60 Credits)' }], total: 'USD 6.00 (60 Credits)', next: 's6b' },
    s6b: { kind: 'choices', choices: [
      { id: 'approve', label: 'Approve USD 6.00', style: 'primary', next: 'a1' },
      { id: 'later', label: 'Not now', style: 'default', next: 'c1' },
    ] },
    c1: { kind: 'agent', text: 'Understood. The quote stays open. I’ll remind you once, on Monday.', next: 'c2', note: 'Follow-ups are capped. One reminder, then silence.' },
    c2: { kind: 'status', status: 'ready' },
  },
};

const JA_POST = 'ローンチは10月14日に変更になりました。各地域のリードは、金曜日までに準備状況をご確認ください。';
const METERED = 'Inline translation is metered against your organization’s balance, as it is today.';

const channelMention = {
  id: 'mention', title: 'In a channel', surface: 'channel', channel: '#launch-global', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Dana Whitfield', text: 'Launch moves to 14 October. Regional leads, please confirm your readiness by Friday.', next: 's2' },
    s2: { kind: 'user', who: 'Kenji Sato', inThread: true, text: '@Arbitr post this in Japanese for the Tokyo team', next: 's3', note: 'Mentioning the agent in a thread is the main way it gets invoked in channels.' },
    s3: { kind: 'agent', inThread: true, text: `I can post a Japanese version in this thread for everyone to see. ${METERED} Kenji, you asked, so it is your click.`, next: 's4', note: 'Inline translation has no quote in today’s system: it is metered, like the existing translate shortcut. So the promise is precise: quoted work waits for a click, and nothing is posted for others without a click.' },
    s4: { kind: 'choices', inThread: true, choices: [
      { id: 'kenji', label: 'Post in Japanese (Kenji clicks)', style: 'primary', next: 'a1' },
      { id: 'dana', label: 'Post in Japanese (Dana clicks)', style: 'default', next: 'b1' },
      { id: 'decline', label: 'Decline', style: 'default', next: 'c1' },
    ], note: 'The rule in a channel: only the person who asked can approve. In real Slack there is one button; the two here let you demo both people.' },
    a1: { kind: 'thread_reply', text: JA_POST, attribution: 'Translated from English by Arbitr (AI). Posted at Kenji Sato’s request.', next: 'a2', note: 'The post says who asked for it and that it is AI.' },
    a2: { kind: 'feedback', inThread: true },
    b1: { kind: 'ephemeral', inThread: true, label: 'Only visible to Dana', text: 'That approval belongs to Kenji. I only accept a click from the person who asked. Ask for it yourself and I’ll prepare a new one.', next: 'b2', note: 'Refused privately. Nothing was posted. Kenji’s button still works.' },
    b2: { kind: 'choices', inThread: true, choices: [{ id: 'kenji', label: 'Post in Japanese (Kenji clicks)', style: 'primary', next: 'a1' }] },
    c1: { kind: 'agent', inThread: true, text: 'Declined. Nothing was posted.' },
  },
};

const suggestion = {
  id: 'suggestion', title: 'Suggestion by DM', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'system', text: 'Dana Whitfield has just posted an announcement in #launch-global, where suggestions are switched on.', next: 's2', note: 'Off by default. Someone turned suggestions on for that channel. The app already receives messages in channels it is a member of; that is how channel auto-translate works today. A suggestion adds one language-detection call to Straker’s own service. Nothing goes to the language model provider.' },
    s2: { kind: 'agent', text: `About your post in #launch-global: most people there work in Japanese. Want a Japanese version posted in the thread? ${METERED}`, next: 's3', note: 'Delivered as a direct message, not an ephemeral message. Ephemeral messages vanish on reload and are unreliable on mobile. A direct message stays put and only Dana sees it.' },
    s3: { kind: 'choices', choices: [
      { id: 'post', label: 'Post in Japanese', style: 'primary', next: 'a1' },
      { id: 'later', label: 'Not now', style: 'default', next: 'b1' },
      { id: 'never', label: 'Don’t suggest this again', style: 'default', next: 'c1' },
    ], note: 'Both opt-outs are on each suggestion. There is a hard cap on how often the agent speaks first. The language model is involved only after the click.' },
    a1: { kind: 'preview', where: '#launch-global, in the thread', text: JA_POST, attribution: 'Translated from English by Arbitr (AI). Posted at Dana Whitfield’s request.', next: 'a2' },
    a2: { kind: 'agent', text: 'Posted in the thread under your message.' },
    b1: { kind: 'agent', text: 'No problem. Nothing was posted.' },
    c1: { kind: 'agent', text: 'Done. I won’t suggest translations for your posts in that channel. You can change this in the Arbitr Home tab.' },
  },
};

const handoff = {
  id: 'handoff', title: 'Hand-off to a form', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Add Japanese subtitles to this video.', file: 'town-hall.mp4', next: 's2' },
    s2: { kind: 'agent', text: 'Subtitles run through the media form. Open it and your file will already be attached.', next: 's3', note: 'Slack only opens a form from a click, never from a typed message. So the agent posts a button. Collecting the fields in conversation is a later phase; the form stays as the fallback.' },
    s3: { kind: 'choices', choices: [{ id: 'open', label: 'Open media form', style: 'primary', next: 's4' }] },
    s4: {
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

const failures = {
  id: 'failures', title: 'When things go wrong', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'divider', label: 'An expired quote', next: 's2' },
    s2: { kind: 'existing', text: 'This translation quote has expired. Please request a new quote.', next: 's3', note: 'Mika clicked Accept on yesterday’s quote. This line is the existing app’s own message. Quotes last 12 hours.' },
    s3: { kind: 'agent', text: 'That quote ran out after 12 hours. Prices can change, so I don’t act on old quotes. Want a fresh one?', next: 's4' },
    s4: { kind: 'choices', choices: [{ id: 'fresh', label: 'Get a new quote', style: 'primary', next: 's5' }, { id: 'skip', label: 'Not now', style: 'default', next: 's5' }] },
    s5: { kind: 'divider', label: 'Stop, in the middle of a job', next: 's6' },
    s6: { kind: 'plan', planId: 'delivery', title: 'Delivery', next: 's7', tasks: [
      { id: 'd1', title: 'Translate: Japanese, German, French', detail: '2 of 3', state: 'in_progress' },
      { id: 'd2', title: 'Deliver here', detail: '', state: 'pending' },
    ] },
    s7: { kind: 'system', text: 'Mika pressed Stop.', next: 's8', note: 'Slack shows a native Stop button while an agent is working.' },
    s8: { kind: 'agent', text: 'Stopped. The translation you approved is already paid for, so it will still be delivered here. Nothing else will start.', next: 's9', note: 'The rule: Stop cancels anything not yet approved. Work that was approved and paid for carries on. Cancelling a paid job is its own explicit action.' },
    s9: { kind: 'divider', label: 'The language model is unavailable', next: 's10' },
    s10: { kind: 'user', who: 'Mika Kato', text: 'Where’s my job?', next: 's11' },
    s11: { kind: 'agent', text: 'I couldn’t think that through just now. The language model did not respond. The buttons below still work, or try again in a moment.', next: 's12', note: 'Never an invented answer, never raw error text. The existing buttons keep working without the model.' },
    s12: { kind: 'choices', choices: [
      { id: 'jobs', label: 'My jobs', style: 'default', next: 's13' },
      { id: 'new', label: 'New translation', style: 'default', next: 's13' },
      { id: 'help', label: 'Help', style: 'default', next: 's13' },
    ] },
    s13: { kind: 'system', text: 'The existing app answers from here, exactly as it does today.', note: 'Not shown: insufficient balance. The existing app already handles it with its own prompt, and IBM workspaces never see one.' },
  },
};

const ibm = {
  id: 'ibm', title: 'IBM workspace', surface: 'dm', compare: true, start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Translate this deck into Japanese.', file: DECK, next: 's2' },
    s2: {
      kind: 'agent', text: 'To price this, I need your account. Connect it once and you won’t be asked again.', next: 's3',
      ibm: { text: 'Document translation runs through the document form. Open it and your file will already be attached.' },
      note: 'IBM workspaces skip account connect: the Slack identity is the identity. And most IBM employees cannot see quotes, so they get the existing document form, exactly as today. Existing behaviour, reused, not rebuilt.',
    },
    s3: { kind: 'choices', choices: [{ id: 'go', label: 'Connect account', style: 'primary', next: 's4' }], ibm: { choices: [{ id: 'go', label: 'Open document form', style: 'primary', next: 's4' }] } },
    s4: { kind: 'user', who: 'Mika Kato', text: 'How much will it cost?', next: 's5' },
    s5: {
      kind: 'agent', text: 'Paid work starts with a quote. It arrives as its own message with an Accept button, and nothing is charged until it is accepted.', next: 's6',
      ibm: { text: 'I can’t show pricing here. Pricing in this workspace is visible to administrators. An administrator can share the quote with you.' },
      note: 'IBM employees never see prices, balances or top-up prompts.',
    },
    s6: { kind: 'user', who: 'Mika Kato', text: 'Turn on translation suggestions in #launch-global.', next: 's7' },
    s7: {
      kind: 'agent', text: 'Suggestions are now on in #launch-global. Anyone there can turn them off.',
      ibm: { text: 'In this workspace only an admin can turn on channel suggestions. This keeps administrators in control of where I speak first. I can send the request to your admins.', button: 'Ask an admin' },
      note: 'Admins stay in control inside IBM.',
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
        { type: 'list', title: 'Waiting on you', tag: 'Planned', rows: [{ text: 'Returns-policy.docx, French: quote USD 6.00 (60 Credits)', action: 'Approve' }] },
        { type: 'jobs', title: 'Your jobs', rows: [
          { ref: 'TJ-48213', name: 'Q3-launch.pptx', state: 'In review' },
          { ref: 'TJ-48190', name: 'Returns-policy.docx', state: 'Quote waiting' },
          { ref: 'TJ-48102', name: 'Onboarding-guide.docx', state: 'Delivered' },
        ] },
        { type: 'digest', title: 'Digest', tag: 'New', options: ['Off', 'Daily', 'Weekly'], selected: 'Off', helper: 'A summary of your jobs, sent by direct message.' },
        { type: 'list', title: 'Default target languages', tag: 'Planned', rows: [{ text: 'Japanese, German', action: 'Change' }] },
        { type: 'list', title: 'Muted suggestions', tag: 'New', rows: [{ text: '#launch-global, translation offers', action: 'Unmute' }] },
        { type: 'list', title: 'Channel suggestions', tag: 'New', adminOnly: true, rows: [{ text: '#launch-global, on, turned on by Dana Whitfield', action: 'Turn off' }] },
        { type: 'digest', title: 'Spend policy', tag: 'Concept', adminOnly: true, options: ['Always ask', 'Auto-approve under USD 10', 'Auto-approve under USD 50'], selected: 'Always ask', helper: 'A concept, not part of this release. It changes who authorises spending, so it needs a billing-policy decision by Straker and the customer’s administrators.' },
      ],
      note: 'The job list is today’s Home tab, unchanged.\n\nNew: digest, muted suggestions, channel suggestions. All opt-in, visible and reversible.\n\nPlanned: a Waiting on you block with inline approval, and default target languages.\n\nConcept only: a spend policy. Today only administrators see quotes, and everyone else’s work is billed to the organization without one.',
    },
  },
};

export const scenarios = [documentRequest, nextDay, jobs, channelMention, suggestion, handoff, failures, ibm, home];
