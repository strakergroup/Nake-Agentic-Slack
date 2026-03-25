# Slack Ray Translator Documentation

This folder contains documentation for the Slack Ray Translator application.

## Table of Contents

- [Communication Boundaries](communication-boundaries.md) - Sequence diagram and route summary for all inbound/outbound service communication
- [Internal Services & Redis Stream Events](internal-services.md) - Comprehensive catalogue of all internal service endpoints called and Redis stream events dispatched
- [VerifyLoop Integration](verifyloop.md) - Configuration, endpoints, modal form, and data flow for the VerifyLoop feature
- [VerifyLoop Architecture Diagrams](verifyloop-architecture.md) - Visual diagrams of system architecture, auth flow, task pipeline, and component dependencies
- [Channel Info Caching](channel-info-caching.md) - How channel names and privacy status are cached in the database
- [Source Language Options](source-language-options.md) - How Cloud Verify source-language options flow from the Verify API and how Slack now matches them
- [Pyright Workflow](pyright-workflow.md) - Local type checking, ignored-module backlog, and GitHub PR check setup
- [Changelog](changelog.md) - Record of all changes to the codebase

## Unreleased

- [Image-to-Markdown Integration](image-to-markdown-integration.md) - Converting images to markdown in Slack via int-image-consumer
- [OCR + Translate Pipeline](ocr-translate-pipeline.md) - Image OCR and translation pipeline using output_stream forwarding between consumers
- [Image Render Pipeline](image-render-pipeline.md) - Full image translation pipeline: XLIFF extraction, translation, and rendered image output
- [Cloud-Verify-Consumer Requirements](cloud-verify-consumer-requirements.md) - Specification for CV XLIFF translation and pipeline forwarding support

## Dev Tools

- [UI Export Tool](ui-export.md) - Renders all Slack Block Kit templates to a single HTML file for visual review

## Database Migrations

When deploying changes that require database schema updates, see the individual feature documentation for migration SQL scripts.
