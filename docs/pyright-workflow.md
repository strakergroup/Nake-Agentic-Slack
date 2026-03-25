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

Type checking currently runs through local development tooling only:

- `pipenv run pyright`
- `pipenv run pre-commit run pyright --all-files`

There is intentionally no repository-level GitHub Actions workflow for `Pyright` at the moment because several repositories depend on internal packages that are only available behind the VPN.

## Editor Setup

For consistent local feedback, install the recommended VS Code extensions from [`.vscode/extensions.json`](../.vscode/extensions.json), especially:

- `ms-python.python`
- `ms-pyright.pyright`
- `charliermarsh.ruff`
