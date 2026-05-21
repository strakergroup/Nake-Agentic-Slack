"""Import regression coverage for Ray event scheduling helpers."""


def test_ray_event_models_import_without_saq_cycle():
    import app.ray.events.models  # noqa: F401


def test_saq_jobs_import_without_ray_events_cycle():
    import app.saq_jobs  # noqa: F401
