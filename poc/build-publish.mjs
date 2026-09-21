// Produces the file the Artifact tool publishes. The publisher wraps the page in
// its own <html>/<head>/<body>, so this strips ours and keeps title, stylesheet
// links, body content and the module script. Run: node build-publish.mjs
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';

const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
const title = html.match(/<title>[\s\S]*?<\/title>/)[0];
const links = [...html.matchAll(/<link rel="stylesheet"[^>]*>/g)].map((m) => m[0]).join('\n');
const body = html.match(/<body>([\s\S]*?)<\/body>/)[1].trim();

mkdirSync(new URL('./dist/', import.meta.url), { recursive: true });
writeFileSync(new URL('./dist/arbitr-in-slack.html', import.meta.url), `${title}\n${links}\n${body}\n`);
console.log('wrote dist/arbitr-in-slack.html');
