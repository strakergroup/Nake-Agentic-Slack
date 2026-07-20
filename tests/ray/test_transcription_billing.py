"""Tests for media transcription billing submission ids (RAY-80734)."""

from app.ray.transcription_billing import transcription_billing_submission_id


def test_prefers_slack_file_id_and_duration():
    assert (
        transcription_billing_submission_id(
            task_uuid="task-a",
            duration_ms=2062560,
            extra_data={"slack_file_id": "F0BAJ616N8Z"},
        )
        == "F0BAJ616N8Z:2062560"
    )


def test_same_file_different_tasks_share_billing_id():
    a = transcription_billing_submission_id(
        task_uuid="task-1",
        duration_ms=1000,
        extra_data={"slack_file_id": "F123"},
    )
    b = transcription_billing_submission_id(
        task_uuid="task-2",
        duration_ms=1000,
        extra_data={"slack_file_id": "F123"},
    )
    assert a == b


def test_falls_back_to_task_uuid_without_file_id():
    assert (
        transcription_billing_submission_id(
            task_uuid="task-only",
            duration_ms=1000,
            extra_data={},
        )
        == "task-only"
    )


def test_uses_original_video_file_id_fallback():
    assert (
        transcription_billing_submission_id(
            task_uuid="task-x",
            duration_ms=500,
            extra_data={"original_video_file_id": "FVID"},
        )
        == "FVID:500"
    )
