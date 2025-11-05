"""Tests for duplicate submission checking logic."""

import os
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engines
from app.models import SlackFileTranslationSubmission
from app.ray.submissions import (
    SubmissionStatus,
    check_and_record_submission_async,
    updated_submission_status,
)


@pytest.fixture
def temp_file():
    """Create a temporary file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("test content")
        temp_path = f.name
    yield temp_path
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def cleanup_submissions():
    """Clean up test submissions after each test."""
    yield
    # Clean up any test submissions created during tests
    with Session(engines["ray_integration"]) as session:
        session.execute(
            select(SlackFileTranslationSubmission).where(
                SlackFileTranslationSubmission.user_id.like("test_user_%")
            )
        )
        test_submissions = session.scalars(
            select(SlackFileTranslationSubmission).where(
                SlackFileTranslationSubmission.user_id.like("test_user_%")
            )
        ).all()
        for submission in test_submissions:
            session.delete(submission)
        session.commit()


@pytest.mark.asyncio
async def test_check_and_record_submission_new_file(temp_file, cleanup_submissions):
    """Test that a new file submission is recorded correctly."""
    user_id = "test_user_1"
    team_id = "test_team_1"
    channel_id = "test_channel_1"
    file_name = "test.txt"
    file_id = "test_file_id_1"
    target_language = "en"

    is_dup, record = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language,
    )

    assert is_dup is False
    assert record is not None
    assert record.user_id == user_id
    assert record.team_id == team_id
    assert record.channel_id == channel_id
    assert record.file_name == file_name
    assert record.file_id == file_id
    assert record.target_language == target_language
    assert record.processing_status == SubmissionStatus.CREATED.value


@pytest.mark.asyncio
async def test_check_and_record_submission_duplicate_same_file(
    temp_file, cleanup_submissions
):
    """Test that submitting the same file twice is detected as duplicate."""
    user_id = "test_user_2"
    team_id = "test_team_2"
    channel_id = "test_channel_2"
    file_name = "test.txt"
    file_id_1 = "test_file_id_2a"
    file_id_2 = "test_file_id_2b"
    target_language = "en"

    # First submission
    is_dup_1, record_1 = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id_1,
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language,
    )

    assert is_dup_1 is False
    assert record_1 is not None

    # Second submission with same file
    is_dup_2, record_2 = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id_2,
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language,
    )

    assert is_dup_2 is True
    assert record_2.id == record_1.id  # Should return the existing record
    assert record_2.file_id == file_id_1  # Original file_id, not the new one


@pytest.mark.asyncio
async def test_check_and_record_submission_different_languages(
    temp_file, cleanup_submissions
):
    """Test that same file with different target languages are not duplicates."""
    user_id = "test_user_3"
    team_id = "test_team_3"
    channel_id = "test_channel_3"
    file_name = "test.txt"
    file_id = "test_file_id_3"
    target_language_1 = "en"
    target_language_2 = "es"

    # First submission - English
    is_dup_1, record_1 = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language_1,
    )

    assert is_dup_1 is False

    # Second submission - Spanish (different language)
    is_dup_2, record_2 = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id,
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language_2,
    )

    assert is_dup_2 is False  # Should not be duplicate due to different language
    assert record_2.id != record_1.id
    assert record_2.target_language == target_language_2


@pytest.mark.asyncio
async def test_check_and_record_submission_different_users(
    temp_file, cleanup_submissions
):
    """Test that same file submitted by different users are not duplicates."""
    user_id_1 = "test_user_4a"
    user_id_2 = "test_user_4b"
    team_id = "test_team_4"
    channel_id = "test_channel_4"
    file_name = "test.txt"
    file_id = "test_file_id_4"
    target_language = "en"

    # First submission - User 1
    is_dup_1, record_1 = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id,
        user_id=user_id_1,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language,
    )

    assert is_dup_1 is False

    # Second submission - User 2 (different user)
    is_dup_2, record_2 = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id,
        user_id=user_id_2,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language,
    )

    assert is_dup_2 is False  # Should not be duplicate due to different user
    assert record_2.id != record_1.id
    assert record_2.user_id == user_id_2


@pytest.mark.asyncio
async def test_check_and_record_submission_failed_status_not_duplicate(
    temp_file, cleanup_submissions
):
    """Test that files with FAILED status are not considered duplicates."""
    user_id = "test_user_5"
    team_id = "test_team_5"
    channel_id = "test_channel_5"
    file_name = "test.txt"
    file_id_1 = "test_file_id_5a"
    file_id_2 = "test_file_id_5b"
    target_language = "en"

    # First submission
    is_dup_1, record_1 = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id_1,
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language,
    )

    assert is_dup_1 is False

    # Mark the first submission as FAILED
    updated_submission_status(
        submission_id=record_1.id,
        processing_status=SubmissionStatus.FAILED,
    )

    # Second submission - should NOT be duplicate because first one is FAILED
    is_dup_2, record_2 = await check_and_record_submission_async(
        path=temp_file,
        file_name=file_name,
        file_id=file_id_2,
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        target_language=target_language,
    )

    assert is_dup_2 is False  # Should not be duplicate because first one is FAILED
    assert record_2.id != record_1.id


@pytest.mark.asyncio
async def test_updated_submission_status(cleanup_submissions):
    """Test that submission status can be updated."""
    # Create a test submission directly in the database
    user_id = "test_user_6"
    team_id = "test_team_6"
    channel_id = "test_channel_6"

    with Session(engines["ray_integration"]) as session:
        submission = SlackFileTranslationSubmission(
            user_id=user_id,
            team_id=team_id,
            channel_id=channel_id,
            file_hash="test_hash",
            file_name="test.txt",
            file_size=100,
            target_language="en",
            file_id="test_file_id",
            processing_status=SubmissionStatus.CREATED.value,
        )
        session.add(submission)
        session.commit()
        session.refresh(submission)
        submission_id = submission.id

    # Update status to COMPLETED
    result = updated_submission_status(
        submission_id=submission_id,
        processing_status=SubmissionStatus.COMPLETED,
    )

    assert result is True

    # Verify the update
    with Session(engines["ray_integration"]) as session:
        updated = session.scalars(
            select(SlackFileTranslationSubmission).where(
                SlackFileTranslationSubmission.id == submission_id
            )
        ).first()
        assert updated.processing_status == SubmissionStatus.COMPLETED.value

    # Clean up
    with Session(engines["ray_integration"]) as session:
        session.delete(updated)
        session.commit()


@pytest.mark.asyncio
async def test_submission_status_updated_to_failed_on_error(cleanup_submissions):
    """Test that submission status is updated to FAILED when an error occurs."""
    # Create a test submission directly in the database
    user_id = "test_user_7"
    team_id = "test_team_7"
    channel_id = "test_channel_7"

    with Session(engines["ray_integration"]) as session:
        submission = SlackFileTranslationSubmission(
            user_id=user_id,
            team_id=team_id,
            channel_id=channel_id,
            file_hash="test_hash",
            file_name="test.txt",
            file_size=100,
            target_language="en",
            file_id="test_file_id",
            processing_status=SubmissionStatus.CREATED.value,
        )
        session.add(submission)
        session.commit()
        session.refresh(submission)
        submission_id = submission.id

    # Verify initial status is CREATED
    with Session(engines["ray_integration"]) as session:
        initial = session.scalars(
            select(SlackFileTranslationSubmission).where(
                SlackFileTranslationSubmission.id == submission_id
            )
        ).first()
        assert initial.processing_status == SubmissionStatus.CREATED.value

    # Simulate error - update status to FAILED
    result = updated_submission_status(
        submission_id=submission_id,
        processing_status=SubmissionStatus.FAILED,
    )

    assert result is True

    # Verify the status was updated to FAILED
    with Session(engines["ray_integration"]) as session:
        updated = session.scalars(
            select(SlackFileTranslationSubmission).where(
                SlackFileTranslationSubmission.id == submission_id
            )
        ).first()
        assert updated.processing_status == SubmissionStatus.FAILED.value

    # Clean up
    with Session(engines["ray_integration"]) as session:
        session.delete(updated)
        session.commit()
