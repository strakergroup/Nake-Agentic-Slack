# Slack Ray Translator Documentation

This folder contains documentation for the Slack Ray Translator application.

## Table of Contents

- [Communication Boundaries](communication-boundaries.md) - Sequence diagram and route summary for all inbound/outbound service communication
- [Internal Services & Redis Stream Events](internal-services.md) - Comprehensive catalogue of all internal service endpoints called and Redis stream events dispatched
- [Document MT PDF Billing](document-mt-pdf-billing.md) - Deferred PDF conversion-fee billing after successful delivery (RAY-80417)
- [Document MT without user login](document-mt-without-login.md) - Org-billed Document MT vs channel/shortcut MT; login gates, gaps, and implications (not HT/QE)
- [Changelog](changelog.md) - Includes RAY-80734 Document MT same-language-family submit rejection (`es`↔`es-419`)
- [VerifyLoop Integration](verifyloop.md) - Configuration, endpoints, modal form, and data flow for the VerifyLoop feature
- [VerifyLoop Architecture Diagrams](verifyloop-architecture.md) - Visual diagrams of system architecture, auth flow, task pipeline, and component dependencies
- [Channel Info Caching](channel-info-caching.md) - How channel names and privacy status are cached in the database
- [Bot Message Channel Translation](bot-message-channel-translation.md) - Bot-message channel translation behavior and Redis quota flow
- [Source Language Options](source-language-options.md) - How Cloud Verify source-language options flow from the Verify API and how Slack now matches them
- [Portuguese Auto-Translate Options](portuguese-auto-translate-options.md) - RAY-80734: `pt-pt` / `pt-BR` replace bare `pt` to avoid Brazil UUID collapse
- [Pyright Workflow](pyright-workflow.md) - Local type checking and ignored-module backlog during the Pyright rollout
- [Google Chat notifications](google-chat-notifications.md) - Standalone webhook alerts and mirroring from `buglog_notifier`
- [SAQ Durable File Handling](saq-durable-file-handling.md) - Durable Slack file uploads and side-effect background work via SAQ + Redis
- [Changelog](changelog.md) - Record of all changes to the codebase

## Unreleased

- [Image-to-Markdown Integration](image-to-markdown-integration.md) - Converting images to markdown in Slack via int-image-consumer
- [OCR + Translate Pipeline](ocr-translate-pipeline.md) - Image OCR and translation pipeline using output_stream forwarding between consumers
- [Image Render Pipeline](image-render-pipeline.md) - Full image translation pipeline: XLIFF extraction, translation, and rendered image output
- [Cloud-Verify-Consumer Requirements](cloud-verify-consumer-requirements.md) - Specification for CV XLIFF translation and pipeline forwarding support

## Dev Tools

- [UI Export Tool](ui-export.md) - Renders all Slack Block Kit templates to a single HTML file for visual review
- [Translation Missing-String Export](translation-export.md) - Exports Slack-locale UI strings missing from the translation database

## Database Migrations

When deploying changes that require database schema updates, see the individual feature documentation for migration SQL scripts.
