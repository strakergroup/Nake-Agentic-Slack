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

## Pull Request Check

GitHub Actions runs `pyright` on pull requests and pushes to the default branch.

The workflow intentionally installs only `Pyright` on the GitHub runner and does not perform a full `pipenv sync --dev`. This keeps the PR check working even when private Straker dependencies are only available on VPN-protected package indexes.

Because the CI job does not install the full application dependency set, type information at those unavailable package boundaries may be less precise than a fully provisioned local development environment.

After the workflow is merged and green on the default branch, make the `pyright` job a required status check in the repository branch protection or ruleset settings.

## Editor Setup

For consistent local feedback, install the recommended VS Code extensions from [`.vscode/extensions.json`](../.vscode/extensions.json), especially:

- `ms-python.python`
- `ms-pyright.pyright`
- `charliermarsh.ruff`
