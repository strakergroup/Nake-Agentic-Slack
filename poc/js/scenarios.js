// Every user-visible word of the simulation lives here so it can be checked
// against the Arbitr voice rules (tests/copy.test.mjs). People, job numbers
// and amounts are fictional. Version 3, after two mock-up reviews on 2026-09-21:
// the agent speaks as "I" in conversation, asks before guessing, accepts
// amendments in plain language, and the plans match what Slack can render
// (buttons ship with the quote, when that streamed message ends). Replies lead with
// the way forward and put the limit second.

const DECK = 'Q3-launch.pptx';
const UNITS_NOTE = 'Amounts show dollars, which is what the quote system returns today, and Credits. The Credits figures are illustrative until the conversion rule is confirmed.';

const TYPED_NOTE = 'People will type yes. The answer points back to the button that is already there. There is never a second live button for one quote, and a used approval cannot be used again.';

const documentRequest = {
  id: 'document', title: 'Document request', surface: 'dm', start: 'p0',
  steps: {
    p0: { kind: 'prompts', title: 'What do you need translated?', prompts: ['Translate a document', 'Where’s my job?', 'What’s waiting on me?'], next: 's1', note: 'Suggested prompts are a native Slack feature at the start of an agent session. Flip the IBM switch above to see this scenario as a typical IBM employee, who never sees a price.' },
    s1: { kind: 'user', who: 'Mika Kato', text: 'Translate this deck into Japanese and German, and have a person check the legal slide.', file: DECK, next: 's2', note: 'A goal in plain language. No shortcut, no form, no command to remember.' },
    s2: { kind: 'status', status: 'working', next: 's3' },
    s3: { kind: 'agent', text: 'Two ways to get the legal slide checked: a reviewer can check the whole Japanese deck, or send me that slide as its own file and only that gets reviewed. I only see the file name. The translation service reads the contents, I don’t.', next: 's3b',
      ibm: { text: 'Two ways to get the legal slide checked: a reviewer can check the whole Japanese deck, or send me that slide as its own file. Review needs a quote, and quotes in this workspace go to your administrators. I only see the file name. The translation service reads the contents, I don’t.' },
      note: 'The way forward first, the limit second. The model sees the request and the file name, never the contents, so it asks; it does not guess. Whether the service could split out one slide by number is a question for engineering.' },
    s3b: { kind: 'status', status: 'waiting', next: 's4' },
    s4: { kind: 'user', who: 'Mika Kato', text: 'Check the whole Japanese deck. Actually, add French too.', next: 's4b', ibm: { text: 'Skip the review for now. Actually, add French too.' }, note: 'A mid-plan amendment, in plain language. This is the difference between an agent and a form.' },
    s4b: { kind: 'status', status: 'working', next: 's5' },
    s5: { kind: 'plan', planId: 'quote', title: 'Quote', next: 's6', tasks: [
      { id: 'q1', title: 'Check the file', detail: '24 slides, English (from the translation service)', state: 'complete' },
      { id: 'q2', title: 'Quote', detail: 'Japanese, German, French, plus review', state: 'in_progress' },
    ], ibm: { kind: 'agent', text: 'Ready to translate the deck into Japanese, German and French. This will be charged to your organization.', tasks: undefined, planId: undefined, title: undefined },
      note: 'Task cards are Slack’s own: not started, working, done, failed. There is no waiting state, so waiting is shown by the session status. The slide count comes from the translation service, and the card says so.' },
    s6: { kind: 'plan_update', planId: 'quote', taskId: 'q2', state: 'complete', detail: 'Ready', next: 's7', ibm: { kind: 'status', status: 'waiting', planId: undefined, taskId: undefined, state: undefined, detail: undefined } },
    s7: { kind: 'quote', next: 's8', lines: [
      { label: 'AI translation: Japanese, German, French', amount: 'USD 18.00 (180 Credits)' },
      { label: 'Human review: Japanese deck', amount: 'USD 96.00 (960 Credits)' },
    ], total: 'USD 114.00 (1,140 Credits)', note: UNITS_NOTE,
      ibm: { kind: 'system', text: 'No price is shown. People in this workspace do not see quotes; the work is billed to the organization’s wallet.', lines: undefined, total: undefined, note: 'The typical IBM employee: goal, amendment, one confirming click, delivery. No price, no connect prompt, no top-up. The click is what stands between a misread request and the customer’s money.' } },
    s8: { kind: 'status', status: 'waiting', next: 's11', note: 'Waiting on you is the product working, not an error.' },
    s11: { kind: 'choices', choices: [
      { id: 'approve', label: 'Approve USD 114.00', style: 'primary', next: 'a1' },
      { id: 'ai_only', label: 'AI only, USD 18.00', style: 'default', next: 'b1' },
      { id: 'decline', label: 'Decline', style: 'default', next: 'c1' },
      { id: 'typed', label: 'Mika types “yes, go ahead”', typed: true, next: 't1' },
    ], ibm: { choices: [
      { id: 'approve', label: 'Translate now', style: 'primary', next: 'a1' },
      { id: 'decline', label: 'Not now', style: 'default', next: 'c1' },
      { id: 'typed', label: 'Mika types “yes, go ahead”', typed: true, next: 't1' },
    ] }, note: 'The buttons ship with the quote, because Slack attaches buttons when a streamed message ends. The click is verified in code: right person, this quote, not expired, not already used.' },
    t1: { kind: 'user', who: 'Mika Kato', text: 'yes, go ahead', next: 't2' },
    t2: { kind: 'agent', text: 'One click on the button above confirms it, so there’s a record of who approved.', reopen: 's11', note: TYPED_NOTE },
    a1: { kind: 'agent', text: 'Approved. The translation service has all three languages, and the Japanese deck goes to a reviewer after that. I’ll post the files here as they arrive.', next: 'a2', ibm: { text: 'Started. The translation service has all three languages. I’ll post the files here as they arrive.' } },
    a2: { kind: 'status', status: 'ready', next: 'a3', note: 'The job is handed to the translation service and the session goes back to Ready. It does not sit on Working for the length of a job, so Stop rarely comes up.' },
    a3: { kind: 'divider', label: 'Four minutes later', next: 'a6' },
    a6: { kind: 'files', files: ['Q3-launch_ja.pptx', 'Q3-launch_de.pptx', 'Q3-launch_fr.pptx'], next: 'a7' },
    a7: { kind: 'agent', disclaimer: true, text: 'All three decks are here. The Japanese deck is now with a reviewer, usually back within a working day.', next: 'a8', ibm: { text: 'All three decks are here.' }, note: 'Files and one sentence. No plan card that arrives already finished. The AI disclaimer sits on deliverables only.' },
    a8: { kind: 'feedback', note: 'Feedback buttons are a native Slack block. They go on deliveries.' },
    b1: { kind: 'agent', text: 'Approved, AI only. The translation service has all three languages. I’ll post the files here as they arrive.', next: 'b2' },
    b2: { kind: 'status', status: 'ready', next: 'b3' },
    b3: { kind: 'divider', label: 'Four minutes later', next: 'b5' },
    b5: { kind: 'files', files: ['Q3-launch_ja.pptx', 'Q3-launch_de.pptx', 'Q3-launch_fr.pptx'], next: 'b6' },
    b6: { kind: 'agent', disclaimer: true, text: 'All three decks are here. You can ask for a human review of any of them later.', next: 'b7' },
    b7: { kind: 'feedback' },
    c1: { kind: 'agent', text: 'Declined. Nothing was charged and nothing was translated. Ask again whenever you need it.', next: 'c2' },
    c2: { kind: 'status', status: 'ready' },
  },
};

const nextDay = {
  id: 'next-day', title: 'The next day', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'divider', label: 'Tuesday', next: 's2' },
    s2: { kind: 'agent', text: 'All three decks are here. The Japanese deck is now with a reviewer, usually back within a working day.', next: 's3', note: 'Where scenario 1 ended. The session is open, nothing is running, and nobody is waiting on Mika.' },
    s3: { kind: 'divider', label: 'Wednesday, 09:12', next: 's4' },
    s4: { kind: 'notice', text: 'Arbitr: the reviewed Japanese deck is ready', next: 's6', note: 'To verify with engineering: does a reply in a day-old agent session notify the person, or only appear in her Threads view? If it is the latter, the agent also posts a one-line pointer at the top level.' },
    s6: { kind: 'files', files: ['Q3-launch_ja_reviewed.pptx'], next: 's7' },
    s7: { kind: 'agent', text: 'The reviewed Japanese deck is back. It replaces yesterday’s AI version.', next: 's8', note: 'A file and one sentence. No plan card: a card that arrives fully done is noise. A change note from the reviewer would make this better; whether the service can return one is a question for engineering.' },
    s8: { kind: 'feedback', next: 's9' },
    s9: { kind: 'user', who: 'Mika Kato', text: 'Good. Send the German one for review as well.', next: 's10', note: 'The session picks up where it left off. No restating the file or the languages.' },
    s10: { kind: 'quote', lines: [{ label: 'Human review: German deck', amount: 'USD 96.00 (960 Credits)' }], total: 'USD 96.00 (960 Credits)', footnote: 'Valid for 30 days.', next: 's11', note: UNITS_NOTE },
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
  id: 'jobs', title: 'Jobs, in Japanese', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', lang: 'ja', text: '翻訳ジョブの状況を教えてください。', next: 's2', note: 'Mika asks in Japanese: “Please tell me the status of my translation jobs.” The agent answers in the language it is addressed in.' },
    s2: { kind: 'status', status: 'working', next: 's3', note: 'A simple look-up shows the working status only. A one-line plan card would be noise.' },
    s3: { kind: 'agent', lang: 'ja', text: '進行中のジョブは2件です。\nTJ-48213（Q3-launch.pptx、日本語・ドイツ語・フランス語）：日本語版はレビュー中で、明日完了予定です。\nTJ-48190（Returns-policy.docx、フランス語）：見積もりが承認待ちです。', next: 's3b', note: 'In English: “There are 2 jobs in progress. TJ-48213 (Japanese, German, French): the Japanese version is in review, due tomorrow. TJ-48190 (French): the quote is awaiting approval.” Written to the approved Japan localisation rules: polite plain register, no second person, half-width numerals.' },
    s3b: { kind: 'status', status: 'ready', next: 's4' },
    s4: { kind: 'user', who: 'Mika Kato', text: 'What’s waiting on me?', next: 's5', note: 'She switches to English, and so does the agent.' },
    s5: { kind: 'agent', text: 'One thing: the human translation quote for Returns-policy.docx, USD 60.00 (600 Credits). It has been waiting since Thursday and is valid for 30 days.', next: 's6', note: 'Human translation quotes last 30 days, so a reminder is legitimate. Document translation quotes last 12 hours and never get a reminder after they expire.' },
    s6: { kind: 'choices', choices: [
      { id: 'approve', label: 'Approve USD 60.00', style: 'primary', next: 'a1' },
      { id: 'view', label: 'View quote', style: 'default', next: 'b1' },
      { id: 'later', label: 'Not now', style: 'default', next: 'c1' },
    ] },
    a1: { kind: 'agent', text: 'Approved. The French translation has started. I’ll deliver it in this thread.', next: 'a2', note: 'No AI disclaimer here: a confirmation is not AI output.' },
    a2: { kind: 'status', status: 'ready' },
    b1: { kind: 'quote', lines: [{ label: 'Human translation: French', amount: 'USD 60.00 (600 Credits)' }], total: 'USD 60.00 (600 Credits)', footnote: 'Valid until 18 October.', next: 's6b' },
    s6b: { kind: 'choices', choices: [
      { id: 'approve', label: 'Approve USD 60.00', style: 'primary', next: 'a1' },
      { id: 'later', label: 'Not now', style: 'default', next: 'c1' },
    ] },
    c1: { kind: 'agent', text: 'Understood. The quote stays open until 18 October. I’ll remind you once, on Monday.', next: 'c2', note: 'Follow-ups are capped. One reminder, well inside the quote’s life, then silence.' },
    c2: { kind: 'status', status: 'ready' },
  },
};

const JA_POST = 'ローンチは10月14日に変更になりました。各地域のリードは、金曜日までに準備状況をご確認ください。';
const METERED = 'Inline translation is metered against your organization’s balance, as it is today.';

const channelMention = {
  id: 'mention', title: 'In a channel', surface: 'channel', channel: '#launch-global', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Dana Whitfield', text: 'Launch moves to 14 October. Regional leads, please confirm your readiness by Friday.', next: 's2' },
    s2: { kind: 'user', who: 'Kenji Sato', inThread: true, text: '@Arbitr post this in Japanese for the Tokyo team', next: 's3', note: 'Mentioning the agent in a thread is the main way it gets invoked in channels. Kenji asked in public, for a metered action with no quote. His mention is the attributable record, so there is no confirming click, just as the translate shortcut has none today.' },
    s3: { kind: 'thread_reply', text: JA_POST, attribution: 'Translated from English by Arbitr (AI). Posted at Kenji Sato’s request. Inline translation is metered against your organization’s balance.', next: 's4', note: 'The post says who asked, that it is AI, and that it is metered. Confirming clicks are kept for quoted work and for when the agent speaks first.' },
    s4: { kind: 'choices', inThread: true, choices: [
      { id: 'kenji', label: 'Remove (Kenji clicks)', style: 'default', next: 'a1' },
      { id: 'dana', label: 'Remove (Dana clicks)', style: 'default', next: 'b1' },
      { id: 'keep', label: 'Leave it', style: 'default', next: 'c1' },
    ], note: 'Only the person who asked can remove it. In real Slack there is one Remove button; the two here let you demo both people. Attribution and Remove need a small change to the existing translation-result handler; they are not in the first hand-over.' },
    a1: { kind: 'system', inThread: true, text: 'The Japanese post was removed by Kenji Sato.' },
    b1: { kind: 'ephemeral', inThread: true, label: 'Only visible to Dana', text: 'Kenji asked for that post, so only Kenji can remove it. Ask him, or ask me for a different version.', note: 'Refused privately. The post stays.' },
    c1: { kind: 'feedback', inThread: true },
  },
};

const suggestion = {
  id: 'suggestion', title: 'Suggestion by DM', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'system', text: 'Dana Whitfield has just posted an announcement in #launch-global, where suggestions are switched on.', next: 's2', note: 'Off by default. Someone turned suggestions on for that channel. The app already receives messages in channels it is a member of; that is how channel auto-translate works today. A suggestion adds one language-detection call to Straker’s own service. Nothing goes to the language model provider.' },
    s2: { kind: 'agent', text: `About your post in #launch-global: most people there work in Japanese. Want a Japanese version posted in the thread? ${METERED}`, next: 's3', note: 'Here the agent speaks first, so a click is required. Delivered as a direct message, not an ephemeral message. Ephemeral messages vanish on reload and are unreliable on mobile. A direct message stays put and only Dana sees it.' },
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
    s2: { kind: 'agent', text: 'Open the media form and your file will already be attached. Subtitles run through that form.', next: 's3', note: 'Slack only opens a form from a click, never from a typed message. So the agent posts a button. Collecting the fields in conversation is a later phase; the form stays as the fallback.' },
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
    s2: { kind: 'existing', text: 'This translation quote has expired. Please request a new quote.', next: 's3', note: 'Mika clicked Accept on yesterday’s document quote. This line is the existing app’s own message. Document quotes last 12 hours.' },
    s3: { kind: 'agent', text: 'Here’s a fresh quote. The price is unchanged from yesterday.', next: 's3q', note: 'Re-quoting is a free look-up, so the agent just does it and says what changed. It does not ask whether she wants one.' },
    s3q: { kind: 'quote', lines: [{ label: 'AI translation: Japanese, German, French', amount: 'USD 18.00 (180 Credits)' }], total: 'USD 18.00 (180 Credits)', footnote: 'Valid for 12 hours.', next: 's4' },
    s4: { kind: 'choices', choices: [{ id: 'approve', label: 'Approve USD 18.00', style: 'primary', next: 's5' }, { id: 'skip', label: 'Not now', style: 'default', next: 's5' }] },
    s5: { kind: 'divider', label: 'Stop, while the agent is thinking', next: 's6' },
    s6: { kind: 'user', who: 'Mika Kato', text: 'Get me quotes for the whole onboarding folder in every language you support', next: 's7' },
    s7: { kind: 'system', text: 'Mika pressed Stop.', next: 's8', note: 'Slack shows a native Stop button while an agent is working. Stop applies to the agent’s own work, and there it really stops.' },
    s8: { kind: 'agent', text: 'Stopped. Nothing was ordered and nothing will start. Jobs already with the translation service carry on, and each can be cancelled from its own job message.', next: 's9', note: 'Stop never pretends to cancel paid work. Cancelling a job is the existing, explicit action on the job itself. Because the session does not sit on Working through a job, Stop during paid work rarely comes up.' },
    s9: { kind: 'divider', label: 'The agent cannot answer', next: 's10' },
    s10: { kind: 'user', who: 'Mika Kato', text: 'Where’s my job?', next: 's11' },
    s11: { kind: 'agent', text: 'The buttons below still work. I can’t answer in my own words right now, and your jobs are unaffected. Try me again in a moment.', next: 's12', note: 'The way forward first. Never an invented answer, never raw error text, no internal jargon.' },
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
      kind: 'agent', text: 'Connect your account once and I’ll pick this request straight back up. I need it to price the work.', next: 's3',
      ibm: { text: 'Ready to translate the deck into Japanese. This will be charged to your organization.' },
      note: 'IBM workspaces skip account connect: the Slack identity is the identity. Most IBM employees cannot see quotes, so they get one confirming click and no price. Elsewhere, resuming the original request after connecting is planned, not built.',
    },
    s3: { kind: 'choices', choices: [{ id: 'go', label: 'Connect account', style: 'primary', next: 's4' }], ibm: { choices: [{ id: 'go', label: 'Translate now', style: 'primary', next: 's4' }] } },
    s4: { kind: 'user', who: 'Mika Kato', text: 'How much will it cost?', next: 's5' },
    s5: {
      kind: 'agent', text: 'Paid work starts with a quote. It arrives as its own message with an Approve button, and nothing is charged until someone clicks it.', next: 's6',
      ibm: { text: 'An administrator can share the quote with you. Pricing in this workspace is visible to administrators only. Everything else works as usual.' },
      note: 'IBM employees never see prices, balances or top-up prompts.',
    },
    s6: { kind: 'user', who: 'Mika Kato', text: 'Turn on translation suggestions in #launch-global.', next: 's7' },
    s7: {
      kind: 'agent', text: 'Suggestions are now on in #launch-global. Anyone there can turn them off.',
      ibm: { text: 'I can send the request to your admins. In this workspace only an admin can turn on channel suggestions. That keeps administrators in control of where I speak first.', button: 'Ask an admin' },
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
        { type: 'list', title: 'Waiting on you', tag: 'Planned', rows: [{ text: 'Returns-policy.docx, French: human translation quote, valid until 18 October', action: 'Approve USD 60.00' }] },
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
      note: 'The job list is today’s Home tab, unchanged.\n\nNew: digest, muted suggestions, channel suggestions. All opt-in, visible and reversible.\n\nPlanned: a Waiting on you block with inline approval (the amount is on the button), default target languages, and using what the person has open beside the agent, so “translate the last announcement” works next to a channel.\n\nConcept only: a spend policy. Today only administrators see quotes, and everyone else’s work is billed to the organization without one.',
    },
  },
};

export const scenarios = [documentRequest, nextDay, jobs, channelMention, suggestion, handoff, failures, ibm, home];
