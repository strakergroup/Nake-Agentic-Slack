# Slack Ray Translator Documentation

This folder contains documentation for the Slack Ray Translator application.

## Table of Contents

- [VerifyLoop Integration](verifyloop.md) - Configuration, endpoints, modal form, and data flow for the VerifyLoop feature
- [VerifyLoop Architecture Diagrams](verifyloop-architecture.md) - Visual diagrams of system architecture, auth flow, task pipeline, and component dependencies
- [Channel Info Caching](channel-info-caching.md) - How channel names and privacy status are cached in the database
- [Image-to-Markdown Integration](image-to-markdown-integration.md) - Converting images to markdown in Slack via int-image-consumer
- [OCR + Translate Pipeline](ocr-translate-pipeline.md) - Image OCR and translation pipeline using output_stream forwarding between consumers
- [Image Render Pipeline](image-render-pipeline.md) - Full image translation pipeline: XLIFF extraction, translation, and rendered image output
- [Cloud-Verify-Consumer Requirements](cloud-verify-consumer-requirements.md) - Specification for CV XLIFF translation and pipeline forwarding support
- [Service Dependencies & Architecture](service-dependencies.md) - Comprehensive service dependency graphs, event flows, and communication patterns
- [UI Export Tool](ui-export.md) - Renders all Slack Block Kit templates to a single HTML file for visual review
- [Changelog](changelog/changelog.md) - Record of all changes to the codebase

## Database Migrations

When deploying changes that require database schema updates, see the individual feature documentation for migration SQL scripts.
