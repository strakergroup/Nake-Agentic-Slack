from app.media.media_workflow import (
    MediaWorkflowCommand,
    MediaWorkflowEvent,
    MediaWorkflowStage,
    MediaWorkflowTransitionError,
    MediaWorkflowType,
    advance_media_workflow,
    make_media_workflow_session,
)


def test_quote1_accepted_starts_transcribe():
    session = make_media_workflow_session(
        workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
        embed_source=False,
        embed_translated=False,
        review_gate=True,
    )
    decision = advance_media_workflow(session, MediaWorkflowEvent.QUOTE1_ACCEPTED)
    assert decision.session.stage == MediaWorkflowStage.TRANSCRIBING
    assert decision.commands == (MediaWorkflowCommand.START_TRANSCRIBE,)


def _transcribing(session):
    return advance_media_workflow(session, MediaWorkflowEvent.QUOTE1_ACCEPTED).session


def test_transcription_completed_with_review_gate_posts_source_review():
    session = _transcribing(
        make_media_workflow_session(
            workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
            embed_source=True,
            embed_translated=False,
            review_gate=True,
        )
    )
    decision = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
    )
    assert decision.session.stage == MediaWorkflowStage.AWAITING_SOURCE_REVIEW
    assert decision.commands == (MediaWorkflowCommand.POST_SOURCE_REVIEW,)


def test_transcription_completed_gate_off_transcribe_only_no_embed_is_done():
    session = _transcribing(
        make_media_workflow_session(
            workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
            embed_source=False,
            embed_translated=False,
            review_gate=False,
        )
    )
    decision = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
    )
    assert decision.session.stage == MediaWorkflowStage.DONE
    assert decision.commands == (MediaWorkflowCommand.MARK_DONE,)


def test_source_srt_approved_starts_source_embed():
    session = advance_media_workflow(
        _transcribing(
            make_media_workflow_session(
                workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
                embed_source=True,
                embed_translated=False,
                review_gate=True,
            )
        ),
        MediaWorkflowEvent.TRANSCRIPTION_COMPLETED,
    ).session
    decision = advance_media_workflow(session, MediaWorkflowEvent.SOURCE_SRT_APPROVED)
    assert decision.session.stage == MediaWorkflowStage.EMBEDDING_SOURCE
    assert decision.commands == (MediaWorkflowCommand.START_SOURCE_EMBED,)


def test_source_embed_completed_transcribe_only_is_done():
    session = make_media_workflow_session(
        workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
        embed_source=True,
        embed_translated=False,
        review_gate=True,
    )
    session = _transcribing(session)
    session = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
    ).session
    session = advance_media_workflow(
        session, MediaWorkflowEvent.SOURCE_SRT_APPROVED
    ).session
    decision = advance_media_workflow(
        session, MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
    )
    assert decision.session.stage == MediaWorkflowStage.DONE
    assert decision.commands == (MediaWorkflowCommand.MARK_DONE,)


def test_transcription_completed_gate_off_translate_posts_quote2():
    session = _transcribing(
        make_media_workflow_session(
            workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
            embed_source=False,
            embed_translated=False,
            review_gate=False,
            target_languages=("de",),
        )
    )
    decision = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
    )
    assert decision.session.stage == MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT
    assert decision.commands == (MediaWorkflowCommand.POST_QUOTE2,)


def test_quote2_accepted_starts_translate():
    session = advance_media_workflow(
        _transcribing(
            make_media_workflow_session(
                workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
                embed_source=False,
                embed_translated=False,
                review_gate=False,
                target_languages=("de",),
            )
        ),
        MediaWorkflowEvent.TRANSCRIPTION_COMPLETED,
    ).session
    decision = advance_media_workflow(session, MediaWorkflowEvent.QUOTE2_ACCEPTED)
    assert decision.session.stage == MediaWorkflowStage.TRANSLATING
    assert decision.commands == (MediaWorkflowCommand.START_TRANSLATE,)


def test_translation_completed_gate_off_no_embed_is_done():
    session = make_media_workflow_session(
        workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
        embed_source=False,
        embed_translated=False,
        review_gate=False,
        target_languages=("de",),
    )
    session = _transcribing(session)
    session = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
    ).session
    session = advance_media_workflow(
        session, MediaWorkflowEvent.QUOTE2_ACCEPTED
    ).session
    decision = advance_media_workflow(session, MediaWorkflowEvent.TRANSLATION_COMPLETED)
    assert decision.session.stage == MediaWorkflowStage.DONE
    assert decision.commands == (MediaWorkflowCommand.MARK_DONE,)


def test_source_srt_replaced_stays_in_review_without_commands():
    session = advance_media_workflow(
        _transcribing(
            make_media_workflow_session(
                workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
                embed_source=True,
                embed_translated=True,
                review_gate=True,
                target_languages=("de",),
            )
        ),
        MediaWorkflowEvent.TRANSCRIPTION_COMPLETED,
    ).session
    decision = advance_media_workflow(session, MediaWorkflowEvent.SOURCE_SRT_REPLACED)
    assert decision.session.stage == MediaWorkflowStage.AWAITING_SOURCE_REVIEW
    assert decision.commands == ()


def test_source_approved_with_embed_and_translate_embeds_before_quote2():
    session = advance_media_workflow(
        _transcribing(
            make_media_workflow_session(
                workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
                embed_source=True,
                embed_translated=True,
                review_gate=True,
                target_languages=("de",),
            )
        ),
        MediaWorkflowEvent.TRANSCRIPTION_COMPLETED,
    ).session
    decision = advance_media_workflow(session, MediaWorkflowEvent.SOURCE_SRT_APPROVED)
    assert decision.session.stage == MediaWorkflowStage.EMBEDDING_SOURCE
    assert decision.commands == (MediaWorkflowCommand.START_SOURCE_EMBED,)


def test_source_embed_completed_then_posts_quote2_when_translating():
    session = advance_media_workflow(
        _transcribing(
            make_media_workflow_session(
                workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
                embed_source=True,
                embed_translated=True,
                review_gate=True,
                target_languages=("de",),
            )
        ),
        MediaWorkflowEvent.TRANSCRIPTION_COMPLETED,
    ).session
    session = advance_media_workflow(
        session, MediaWorkflowEvent.SOURCE_SRT_APPROVED
    ).session
    decision = advance_media_workflow(
        session, MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
    )
    assert decision.session.stage == MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT
    assert decision.commands == (MediaWorkflowCommand.POST_QUOTE2,)


def test_source_embed_completed_during_quote2_is_ignored():
    from app.media.media_workflow import (
        MediaWorkflowConfig,
        MediaWorkflowSession,
    )

    session = MediaWorkflowSession(
        stage=MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT,
        config=MediaWorkflowConfig(
            workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
            embed_source=True,
            embed_translated=True,
            review_gate=True,
            target_languages=("de",),
        ),
    )
    decision = advance_media_workflow(
        session, MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
    )
    assert decision.session.stage == MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT
    assert decision.commands == ()


def test_late_source_embed_during_translated_embed_is_noop():
    session = make_media_workflow_session(
        workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
        embed_source=True,
        embed_translated=True,
        review_gate=False,
        target_languages=("es",),
    )
    session = _transcribing(session)
    session = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
    ).session
    session = advance_media_workflow(
        session, MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
    ).session
    session = advance_media_workflow(
        session, MediaWorkflowEvent.QUOTE2_ACCEPTED
    ).session
    session = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSLATION_COMPLETED
    ).session
    assert session.stage == MediaWorkflowStage.EMBEDDING_TRANSLATED
    decision = advance_media_workflow(
        session, MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
    )
    assert decision.session.stage == MediaWorkflowStage.EMBEDDING_TRANSLATED
    assert decision.commands == ()


def test_quote1_cancelled_has_no_start_commands():
    session = make_media_workflow_session(
        workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
        embed_source=False,
        embed_translated=False,
        review_gate=True,
    )
    decision = advance_media_workflow(session, MediaWorkflowEvent.QUOTE1_CANCELLED)
    assert decision.session.stage == MediaWorkflowStage.CANCELLED
    assert decision.commands == ()


def test_source_approve_while_transcribing_is_illegal():
    session = _transcribing(
        make_media_workflow_session(
            workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
            embed_source=False,
            embed_translated=False,
            review_gate=True,
        )
    )
    try:
        advance_media_workflow(session, MediaWorkflowEvent.SOURCE_SRT_APPROVED)
    except MediaWorkflowTransitionError as exc:
        assert exc.stage is MediaWorkflowStage.TRANSCRIBING
        assert exc.event is MediaWorkflowEvent.SOURCE_SRT_APPROVED
    else:
        raise AssertionError("expected MediaWorkflowTransitionError")


def test_translated_review_then_embed():
    session = make_media_workflow_session(
        workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
        embed_source=False,
        embed_translated=True,
        review_gate=True,
        target_languages=("de",),
    )
    session = _transcribing(session)
    session = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
    ).session
    session = advance_media_workflow(
        session, MediaWorkflowEvent.SOURCE_SRT_APPROVED
    ).session
    session = advance_media_workflow(
        session, MediaWorkflowEvent.QUOTE2_ACCEPTED
    ).session
    decision = advance_media_workflow(session, MediaWorkflowEvent.TRANSLATION_COMPLETED)
    assert decision.session.stage == MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW
    assert decision.commands == (MediaWorkflowCommand.POST_TRANSLATION_REVIEW,)
    session = decision.session
    decision = advance_media_workflow(
        session, MediaWorkflowEvent.TRANSLATED_SRT_APPROVED
    )
    assert decision.session.stage == MediaWorkflowStage.EMBEDDING_TRANSLATED
    assert decision.commands == (MediaWorkflowCommand.START_TRANSLATED_EMBED,)
    decision = advance_media_workflow(
        decision.session, MediaWorkflowEvent.TRANSLATED_EMBED_COMPLETED
    )
    assert decision.session.stage == MediaWorkflowStage.DONE
    assert decision.commands == (MediaWorkflowCommand.MARK_DONE,)


def test_media_workflow_session_from_quote_returns_none_for_legacy_session():
    from app.media.media_workflow import media_workflow_session_from_quote

    assert (
        media_workflow_session_from_quote(
            {
                "stage": "transcribing",
                "pipeline_kind": "transcribe_translate",
            }
        )
        is None
    )


def test_media_workflow_session_from_quote_returns_none_for_unknown_stage():
    from app.media.media_workflow import media_workflow_session_from_quote

    assert (
        media_workflow_session_from_quote(
            {
                "workflow_type": "transcribe_only",
                "stage": "not-a-stage",
            }
        )
        is None
    )
    assert (
        media_workflow_session_from_quote({"workflow_type": "transcribe_only"}) is None
    )


def test_media_workflow_session_from_quote_maps_configure_flags():
    from app.media.media_workflow import media_workflow_session_from_quote

    session = media_workflow_session_from_quote(
        {
            "stage": "transcribing",
            "workflow_type": "transcribe_translate",
            "embed_source": True,
            "embed_translated": False,
            "review_gate": True,
            "target_languages": ["fr", "de"],
        }
    )
    assert session is not None
    assert session.stage is MediaWorkflowStage.TRANSCRIBING
    assert session.config.workflow_type is MediaWorkflowType.TRANSCRIBE_TRANSLATE
    assert session.config.embed_source is True
    assert session.config.embed_translated is False
    assert session.config.review_gate is True
    assert session.config.target_languages == ("fr", "de")
