/**
 * Render a focused quote flow with the same Slack renderer as the UI catalog.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Message } from "slack-blocks-to-jsx";

const require = createRequire(import.meta.url);
/** @type {{ emojify: (text: string) => string }} */
const emoji = require("node-emoji");

const __dirname = dirname(fileURLToPath(import.meta.url));

function argValue(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index === -1 || index + 1 >= process.argv.length) return fallback;
  return process.argv[index + 1];
}

const inputPath = argValue(
  "--input",
  join(__dirname, "output", "ibm-ht-staged-quote-flow.json")
);
const outputPath = argValue(
  "--output",
  join(__dirname, "output", "ibm-ht-staged-quote-flow.html")
);
const libCss = readFileSync(
  join(__dirname, "node_modules", "slack-blocks-to-jsx", "dist", "style.css"),
  "utf-8"
);

/** @type {{ title: string, subtitle?: string, steps: Array<{ title: string, note: string, entry_name: string, type: string, blocks: import("slack-blocks-to-jsx").Block[] }> }} */
const data = JSON.parse(readFileSync(inputPath, "utf-8"));

/** @type {Record<string, string>} */
const SLACK_EMOJI_FALLBACKS = {
  large_blue_circle: "\u{1F535}",
  large_green_circle: "\u{1F7E2}",
  large_orange_circle: "\u{1F7E0}",
  large_red_circle: "\u{1F534}",
  large_yellow_circle: "\u{1F7E1}",
  sports_medal: "\u{1F3C5}",
  coin: "\u{1FA99}",
};

/**
 * @param {unknown} text
 * @returns {unknown}
 */
function sanitiseMrkdwn(text) {
  if (typeof text !== "string") return text;
  let result = emoji.emojify(text);
  result = result.replace(/:([a-z0-9_+-]+):/g, (match, name) => {
    return SLACK_EMOJI_FALLBACKS[name] || match;
  });
  return result;
}

/**
 * @param {import("slack-blocks-to-jsx").Block[] | undefined} blocks
 * @returns {import("slack-blocks-to-jsx").Block[] | undefined}
 */
function sanitiseBlocks(blocks) {
  if (!Array.isArray(blocks)) return blocks;
  return /** @type {import("slack-blocks-to-jsx").Block[]} */ (
    JSON.parse(
      JSON.stringify(blocks, (key, value) => {
        if (key === "text" && typeof value === "string") return sanitiseMrkdwn(value);
        return value;
      })
    )
  );
}

/**
 * @param {string} value
 * @returns {string}
 */
function escapeHtml(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * @param {import("slack-blocks-to-jsx").Block[]} blocks
 * @param {string} name
 * @returns {string}
 */
function renderBlocks(blocks, name) {
  try {
    const el = createElement(Message, {
      blocks: sanitiseBlocks(blocks),
      name: "Straker Translate",
      logo: "https://avatars.slack-edge.com/2024-01-01/placeholder.png",
      withoutWrapper: false,
    });
    return renderToStaticMarkup(el);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return `<div class="render-error">Render error for ${escapeHtml(name)}: ${escapeHtml(
      message
    )}</div>`;
  }
}

const stepsHtml = data.steps
  .map((step) => {
    const rendered = renderBlocks(step.blocks, step.entry_name);
    return `
      <section class="flow-step">
        <div class="step-copy">
          <h2>${escapeHtml(step.title)}</h2>
          <p>${escapeHtml(step.note)}</p>
          <code>${escapeHtml(step.entry_name)}</code>
        </div>
        <div class="template-card">
          <div class="template-header">
            <span class="template-name">${escapeHtml(step.entry_name)}</span>
            <span class="template-type type-message">Message</span>
          </div>
          <div class="template-body">${rendered}</div>
        </div>
      </section>`;
  })
  .join("");

const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(data.title)} — Straker Translate</title>
  <style>
    ${libCss}
    :root {
      --bg-content: #ffffff;
      --text-dark: #1d1c1d;
      --accent: #1264a3;
      --card-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.08);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #f4f4f4;
      color: var(--text-dark);
      min-height: 100vh;
    }
    .main {
      max-width: 980px;
      margin: 0 auto;
      padding: 32px 40px 56px;
    }
    .page-title {
      font-size: 28px;
      font-weight: 900;
      margin-bottom: 8px;
    }
    .page-subtitle {
      color: #616061;
      margin-bottom: 32px;
      font-size: 14px;
    }
    .flow-step {
      display: grid;
      grid-template-columns: minmax(260px, 0.8fr) minmax(360px, 1.2fr);
      gap: 20px;
      align-items: start;
      margin-bottom: 28px;
    }
    .step-copy h2 {
      font-size: 20px;
      font-weight: 800;
      margin-bottom: 8px;
    }
    .step-copy p {
      color: #616061;
      margin-bottom: 12px;
      font-size: 14px;
      line-height: 1.45;
    }
    code {
      display: inline-block;
      background: #e8e8e8;
      border-radius: 6px;
      color: #444;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      padding: 4px 6px;
    }
    .template-card {
      background: var(--bg-content);
      border-radius: 8px;
      box-shadow: var(--card-shadow);
      overflow: hidden;
    }
    .template-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 16px;
      background: #f8f8f8;
      border-bottom: 1px solid #e8e8e8;
    }
    .template-name {
      font-size: 14px;
      font-weight: 700;
      color: var(--text-dark);
    }
    .template-type {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .type-message { background: #e8f5e9; color: #2e7d32; }
    .template-body { padding: 16px; }
    .render-error {
      color: #b00020;
      background: #ffebee;
      padding: 12px;
      border-radius: 4px;
    }
    @media (max-width: 820px) {
      .main { padding: 24px 18px 40px; }
      .flow-step { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="main">
    <h1 class="page-title">${escapeHtml(data.title)}</h1>
    <p class="page-subtitle">
      ${escapeHtml(
        data.subtitle ||
          "Focused static mock of the new RAY-79115 IBM HT staged quote steps, rendered with the same Slack Block Kit renderer and styles as the full UI catalog."
      )}
    </p>
    ${stepsHtml}
  </main>
</body>
</html>`;

mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, html);
console.log(`Rendered ${data.steps.length} quote flow steps -> ${outputPath}`);
