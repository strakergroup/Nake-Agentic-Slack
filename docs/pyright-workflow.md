# Pyright Workflow

This repository now uses `Pyright` for static type checking.

## Local Workflow

Install development dependencies with Pipenv:

```bash
pipenv install --dev
```

Run the repository type check locally:

```bash
pipenv run pyright
```

The repo also runs `Pyright` through `pre-commit`, so developers can catch type regressions before opening a pull request.

## Current Scope

`Pyright` is configured in [`pyproject.toml`](../pyproject.toml) and currently checks the `app/` package.

To keep the rollout practical, a small set of backlog-heavy modules are temporarily ignored:

- `app/auth/connector.py`
- `app/routers/ray.py`
- `app/slack/listener_actions.py`
- `app/slack/listeners.py`

These files should be removed from the ignore list incrementally as they are cleaned up.

## Current Rollout

Type checking runs locally and as a **non-blocking** GitHub Actions
report — see [`lint-typecheck-ci.md`](lint-typecheck-ci.md).

Local commands:

- `pipenv run pyright`
- `pipenv run pre-commit run pyright --all-files`

The CI workflow installs the internal Straker libraries directly from
their private GitHub repos using the `PRIVATE_REPO_TOKEN` org secret,
which removes the previous VPN-only blocker (the internal PyPI server
is still used by Jenkins and other internal workloads).

## Editor Setup

For consistent local feedback, install the recommended VS Code extensions from [`.vscode/extensions.json`](../.vscode/extensions.json), especially:

- `ms-python.python`
- `ms-pyright.pyright`
- `charliermarsh.ruff`
