"""Slack integration package.

This package intentionally does **not** re-export submodule symbols at
import time. Doing so previously caused a hard-to-diagnose circular import:
importing any ``app.slack.X`` triggered ``from .app import app`` which in
turn loaded ``app.slack.templates.messages`` -> ``app.slack.select_options``
-> ``app.ray``. If anything in that downstream chain happened to also import
from ``app.slack.*`` (e.g. ``app.ray.service`` -> ``app.slack.buglog_notifier``)
during a cold import, the parent package was still mid-initialisation and
the import failed with ``ImportError: cannot import name ... from
partially initialized module``.

Callers should import from explicit submodules:

* ``from app.slack.app import app``
* ``from app.slack.listeners import slack_handler``
"""
