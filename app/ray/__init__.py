"""RAY platform integration package.

This package intentionally does **not** re-export submodule symbols at
import time. Re-exporting ``RayService`` and ``get_languages`` from
``app.ray.service`` previously caused a circular import: the eager
``from .service import ...`` triggered ``app.slack.buglog_notifier``, which
loaded ``app.slack.__init__.py``, which transitively pulled
``app.slack.select_options`` whose own ``from ..ray import get_languages``
hit a partially-initialised ``app.ray`` package and crashed.

Callers should import from explicit submodules:

* ``from app.ray.service import RayService, get_languages``
"""
