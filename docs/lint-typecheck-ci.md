# Lint & Typecheck CI (non-blocking)

GitHub Actions workflow that runs `ruff check` and `pyright` on every
pull request and push to long-lived branches. Both jobs are **explicitly
non-blocking** (`continue-on-error: true`) — they exist to surface a
quality report in the PR checks UI and as a downloadable artifact, not
to gate merges.

- Workflow: [`.github/workflows/lint-typecheck.yml`](../.github/workflows/lint-typecheck.yml)
- Tools configured in [`pyproject.toml`](../pyproject.toml)
  (`[tool.ruff]`, `[tool.pyright]`)
- Local equivalents are documented in
  [`pyright-workflow.md`](pyright-workflow.md).

## What runs

| Job       | Tool                  | Output (PR summary)        | Artifact          |
| --------- | --------------------- | -------------------------- | ----------------- |
| `ruff`    | `ruff check app tests` | Inline in step summary     | `ruff.sarif`      |
| `pyright` | `pyright`              | Inline in step summary     | `pyright.json`    |

Both jobs run in parallel on `ubuntu-latest` with Python 3.11.

## Triggers

- `pull_request` against `master`, `main`, `develop`, `uat`
- `push` to the same branches
- `workflow_dispatch` (manual run from the Actions tab)

A `concurrency` group keyed on `github.ref` cancels superseded runs so
that only the latest commit on a branch produces an active report.

## Installing private Straker libraries

GitHub Actions runners cannot reach the VPN-only internal PyPI
(`mgmt-k8s-pypi-fra02.straker.io`). Internal libraries are installed
from their **private GitHub repos** instead, pinned to the same git
tags that match the Pipfile versions.

The five Straker libraries used by this app:

| Pipfile package | GitHub repo                                                                                                       | Tag installed in CI |
| --------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------- |
| `straker-auth`  | [`strakergroup/sup-python-auth`](https://github.com/strakergroup/sup-python-auth)                                 | `v0.1.8`            |
| `ray-sdk`       | [`strakergroup/sup-python-languagecloud-sdk`](https://github.com/strakergroup/sup-python-languagecloud-sdk)       | `v0.6.2` ⚠️         |
| `ray-logger`    | [`strakergroup/sup-python-languagecloud-logger`](https://github.com/strakergroup/sup-python-languagecloud-logger) | `v0.1.2`            |
| `buglog`        | [`strakergroup/sup-python-buglog-sdk`](https://github.com/strakergroup/sup-python-buglog-sdk)                     | `v0.1.3`            |
| `straker-utils` | [`strakergroup/sup-python-utils`](https://github.com/strakergroup/sup-python-utils)                               | `v0.3.0.7`          |

> ⚠️ **`ray-sdk` drift:** the Pipfile pins `ray-sdk==0.6.3`, but
> `sup-python-languagecloud-sdk` does not have a `v0.6.3` git tag at
> the time of writing (latest is `v0.6.2`). CI installs `v0.6.2` as a
> best-effort. Tag `v0.6.3` in the GitHub repo (or bump the Pipfile to
> a tag that exists) to bring CI fully back in sync. Because the
> workflow is non-blocking, this drift does not break PRs.

## Authentication: `PRIVATE_REPO_TOKEN`

Authenticated `git+https://github.com/strakergroup/...` installs
require an org-level GitHub Actions secret named **`PRIVATE_REPO_TOKEN`**.

It is a fine-grained PAT owned by a dedicated service account, scoped
to the six private library repositories listed in OPS-6457, with the
minimum permission **Contents: Read**.

The workflow uses a single `git config --global url."…".insteadOf`
rewrite, so the token is never echoed on a `pip install` command line
or in logs:

```yaml
git config --global \
  url."https://x-access-token:${PRIVATE_REPO_TOKEN}@github.com/strakergroup/".insteadOf \
  "https://github.com/strakergroup/"
```

If the secret is missing, the workflow logs a `::warning::` and skips
the private library install step. `pyright` will still run; type
errors for symbols imported from those libraries are expected and
ignored thanks to `reportMissingImports = "none"` in
[`pyproject.toml`](../pyproject.toml).

## Why non-blocking

Pyright is being rolled out incrementally — see
[`pyright-workflow.md`](pyright-workflow.md) for the list of
temporarily ignored modules. Until that backlog is drained, gating PRs
on a clean type-check would block too much real work. The same
philosophy applies to ruff: the report is a guide, not a gate, while
the codebase catches up.

To make either job blocking later, drop `continue-on-error: true` from
the relevant job in the workflow file and (optionally) make the report
step `exit` with the tool's real exit code instead of always `0`.

## Reading the report

1. **PR checks tab** — the step summary for both jobs renders the
   tool's full output as a fenced code block, including the exit code.
2. **Artifacts** — `ruff.sarif` and `pyright.json` are uploaded for 14
   days. Download from the workflow run page if you want to feed them
   into another tool (e.g. GitHub code-scanning for SARIF).

## Background / source-of-truth

Migration from Bitbucket-hosted libraries and the VPN-locked internal
PyPI to private GitHub repos is tracked in
[OPS-6457](https://app.clickup.com/t/36600298/OPS-6457). The interim
model documented there is intentional: Jenkins and internal workloads
keep using the internal PyPI server, while GitHub Actions installs
straight from GitHub via `PRIVATE_REPO_TOKEN`.
