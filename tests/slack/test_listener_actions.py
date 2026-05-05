import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest

from app.slack.listener_actions import (
    VIDEO_FILE_TYPES,
    _language_code_from_srt_filename,
    ai_translate_help,
    approve_pending_client,
    build_thread_media_embed_action_value,
    create_service_language_mapping,
    document_machine_translate,
    get_groups,
    get_mt_translation,
    is_video_file,
    maybe_show_thread_media_embed_option,
    post_batch_list,
    post_file_list,
    post_job_status,
    post_job_target_lang,
    submit_job,
    update_machine_translation_score,
    verify_help,
)
from app.slack.templates.messages import AutoTranslationMessage


class _FakeAsyncClient:
    def __init__(self):
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):
        self.posts.append({"url": url, "json": json})
        return SimpleNamespace(status_code=200)


class TestDocumentMachineTranslate:
    @pytest.mark.asyncio
    async def test_emits_one_event_for_multiple_target_languages(
        self, context, ray_client
    ):
        from app.auth.connector import RayConnection

        fake_http_client = _FakeAsyncClient()
        context["ray"] = RayConnection(super_group=[], client=ray_client)

        with (
            patch(
                "app.slack.listener_actions.get_group_mt_engine",
                new_callable=AsyncMock,
                return_value="google",
            ),
            patch(
                "app.slack.listener_actions.create_slack_job",
                new_callable=AsyncMock,
                return_value="task-123",
            ) as create_job,
            patch(
                "app.slack.listener_actions.httpx.AsyncClient",
                return_value=fake_http_client,
            ),
        ):
            await document_machine_translate(
                context,
                "gridfs-file-1",
                "en",
                ["fr", "de"],
                {"fr": 101, "de": 102},
            )

        create_job.assert_awaited_once()
        task_data = create_job.await_args.args[0]
        assert task_data.target_language == "fr"
        assert task_data.source_language == "en"
        assert task_data.target_languages == ["fr", "de"]
        assert task_data.submission_ids == {"fr": 101, "de": 102}
        assert task_data.submission_id == 101

        assert len(fake_http_client.posts) == 1
        event_data = fake_http_client.posts[0]["json"]["data"]
        assert event_data["task_uuid"] == "task-123"
        assert event_data["source_language"] == "en"
        assert event_data["target_language"] == "fr"
        assert event_data["target_languages"] == ["fr", "de"]
        assert event_data["submission_ids"] == {"fr": 101, "de": 102}


class TestCreateServiceLanguageMapping:
    """Tests for create_service_language_mapping function."""

    def test_create_service_language_mapping_empty_list(self):
        """Test with empty list."""
        result = create_service_language_mapping([])
        assert result == {}

    def test_create_service_language_mapping_google_languages(self):
        """Test with Google-supported languages."""
        result = create_service_language_mapping(["en", "fr", "es"])
        assert "google" in result
        assert result["google"] == {"en": "", "fr": "", "es": ""}
        assert "microsoft" not in result

    def test_create_service_language_mapping_french_canada(self):
        """Test with French Canada language."""
        result = create_service_language_mapping(["fr-ca"])
        assert "microsoft" in result
        assert result["microsoft"] == {"fr-ca": ""}
        assert "google" not in result

    def test_create_service_language_mapping_french_canadian(self):
        """Test with french-canadian variant."""
        result = create_service_language_mapping(["french-canadian"])
        assert "microsoft" in result
        assert result["microsoft"] == {"fr-ca": ""}

    def test_create_service_language_mapping_french_canada_variant(self):
        """Test with french-canada variant."""
        result = create_service_language_mapping(["french-canada"])
        assert "microsoft" in result
        assert result["microsoft"] == {"fr-ca": ""}

    def test_create_service_language_mapping_mixed(self):
        """Test with mixed languages."""
        result = create_service_language_mapping(
            ["en", "fr-ca", "es", "french-canadian"]
        )
        assert "google" in result
        assert "microsoft" in result
        assert result["google"] == {"en": "", "es": ""}
        assert result["microsoft"] == {"fr-ca": ""}

    def test_create_service_language_mapping_multiple_french_canada(self):
        """Regional variants all map to the same Microsoft fr-ca key."""
        result = create_service_language_mapping(
            ["fr-ca", "french-canada", "french-canadian"]
        )
        assert "microsoft" in result
        assert result["microsoft"] == {"fr-ca": ""}


class TestIsVideoFile:
    """Tests for is_video_file function."""

    def test_is_video_file_by_filetype(self):
        """Test detection by filetype."""
        for filetype in VIDEO_FILE_TYPES:
            file_details = {"filetype": filetype, "name": "test"}
            assert is_video_file(file_details) is True

    def test_is_video_file_by_extension(self):
        """Test detection by file extension."""
        for ext in VIDEO_FILE_TYPES:
            file_details = {"filetype": "", "name": f"test.{ext}"}
            assert is_video_file(file_details) is True

    def test_is_video_file_case_insensitive_filetype(self):
        """Test case-insensitive filetype detection."""
        file_details = {"filetype": "MP4", "name": "test"}
        assert is_video_file(file_details) is True

    def test_is_video_file_case_insensitive_extension(self):
        """Test case-insensitive extension detection."""
        file_details = {"filetype": "", "name": "test.MP4"}
        assert is_video_file(file_details) is True

    def test_is_video_file_mpga_special_case(self):
        """Test special mpga case."""
        file_details = {"filetype": "", "name": "test.mpga"}
        assert is_video_file(file_details) is True

    def test_is_video_file_not_video(self):
        """Test non-video files."""
        file_details = {"filetype": "pdf", "name": "test.pdf"}
        assert is_video_file(file_details) is False

    def test_is_video_file_no_filetype_no_extension(self):
        """Test file with no filetype and no extension."""
        file_details = {"filetype": "", "name": "test"}
        assert is_video_file(file_details) is False

    def test_is_video_file_missing_filetype(self):
        """Test file with missing filetype key."""
        file_details = {"name": "test.mp4"}
        assert is_video_file(file_details) is True

    def test_is_video_file_missing_name(self):
        """Test file with missing name key."""
        file_details = {"filetype": "mp4"}
        assert is_video_file(file_details) is True

    def test_is_video_file_empty_dict(self):
        """Test with empty dictionary."""
        file_details = {}
        assert is_video_file(file_details) is False

    def test_is_video_file_multiple_dots_in_filename(self):
        """Test filename with multiple dots."""
        file_details = {"filetype": "", "name": "test.file.mp4"}
        assert is_video_file(file_details) is True

    def test_is_video_file_all_video_types(self):
        """Test all video file types."""
        for video_type in VIDEO_FILE_TYPES:
            # Test by filetype
            file_details = {"filetype": video_type, "name": "test"}
            assert (
                is_video_file(file_details) is True
            ), f"Failed for filetype: {video_type}"

            # Test by extension
            file_details = {"filetype": "", "name": f"test.{video_type}"}
            assert (
                is_video_file(file_details) is True
            ), f"Failed for extension: {video_type}"


class TestThreadMediaEmbedOption:
    def test_build_thread_media_embed_action_value(self):
        """Test thread embed payload is rebuilt from the root media message."""
        root_message = {
            "files": [
                {"id": "V123", "name": "video.mp4", "filetype": "mp4"},
                {"id": "A123", "name": "audio.mp3", "filetype": "mp3"},
            ]
        }
        reply_message = {
            "thread_ts": "123456.789",
            "files": [{"id": "F123", "name": "captions.srt", "filetype": "srt"}],
        }

        updated_action_value = build_thread_media_embed_action_value(
            "C123", "123456.789", root_message, reply_message
        )

        action_data = json.loads(updated_action_value)
        assert action_data["channel_id"] == "C123"
        assert action_data["files"] == [{"file_id": "V123", "file_name": "video.mp4"}]
        assert action_data["subtitle_file"]["file_id"] == "F123"
        assert action_data["subtitle_file"]["file_name"] == "captions.srt"
        assert action_data["subtitle_file"]["language_code"] == "und"
        assert action_data["thread_ts"] == "123456.789"

    def test_build_thread_media_embed_action_value_detects_language_from_filename(self):
        """Test language code is inferred from SRT filename like video_Japanese.srt."""
        root_message = {"files": [{"id": "V1", "name": "clip.mp4", "filetype": "mp4"}]}
        reply_message = {
            "thread_ts": "100.200",
            "files": [{"id": "F1", "name": "clip_Japanese.srt", "filetype": "srt"}],
        }

        result = json.loads(
            build_thread_media_embed_action_value(
                "C1", "100.200", root_message, reply_message
            )
        )
        assert result["subtitle_file"]["language_code"] == "ja"
        assert result["subtitle_file"]["file_name"] == "clip_Japanese.srt"

    @pytest.mark.asyncio
    async def test_maybe_show_thread_media_embed_option_uses_thread_root_message(self):
        """Test SRT uploads use the root thread message instead of thread replies."""
        client = AsyncMock()
        client.conversations_history.return_value = {
            "messages": [
                {
                    "files": [
                        {"id": "V123", "name": "video.mp4", "filetype": "mp4"},
                    ]
                }
            ]
        }
        context = MagicMock()
        context.get.return_value = "C123"
        context.say = AsyncMock()
        message = {
            "thread_ts": "123456.789",
            "files": [{"id": "F123", "name": "captions.srt", "filetype": "srt"}],
        }

        with patch(
            "app.slack.listener_actions.require_ray_client", new_callable=AsyncMock
        ) as mock_require:
            mock_require.return_value = True
            handled = await maybe_show_thread_media_embed_option(
                client, context, message
            )

        assert handled is True
        client.conversations_history.assert_called_once_with(
            channel="C123",
            latest="123456.789",
            oldest="123456.789",
            inclusive=True,
            limit=1,
        )
        assert context.say.call_count == 1
        assert context.say.call_args.kwargs["thread_ts"] == "123456.789"
        assert context.say.call_args.kwargs["blocks"][0]["accessory"]["action_id"] == (
            "video_embed_subtitles"
        )
        updated_action_data = json.loads(
            context.say.call_args.kwargs["blocks"][0]["accessory"]["value"]
        )
        assert updated_action_data["files"][0]["file_id"] == "V123"
        assert updated_action_data["subtitle_file"]["file_id"] == "F123"

    @pytest.mark.asyncio
    async def test_maybe_show_thread_media_embed_option_returns_false_for_non_media_root(
        self,
    ):
        """Test non-video root thread messages do not show the embed CTA."""
        client = AsyncMock()
        client.conversations_history.return_value = {
            "messages": [
                {
                    "files": [
                        {"id": "A123", "name": "audio.mp3", "filetype": "mp3"},
                    ]
                }
            ]
        }
        context = MagicMock()
        context.get.return_value = "C123"
        context.say = AsyncMock()
        message = {
            "thread_ts": "123456.789",
            "files": [{"id": "F123", "name": "captions.srt", "filetype": "srt"}],
        }

        with patch(
            "app.slack.listener_actions.require_ray_client", new_callable=AsyncMock
        ) as mock_require:
            mock_require.return_value = True
            handled = await maybe_show_thread_media_embed_option(
                client, context, message
            )

        assert handled is False
        assert context.say.call_count == 0


class TestLanguageCodeFromSrtFilename:
    """Tests for _language_code_from_srt_filename helper."""

    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("video_Japanese.srt", "ja"),
            ("video_Spanish.srt", "es"),
            ("video_French.srt", "fr"),
            ("clip_Chinese (Simplified).srt", "zh-CN"),
            ("video_ja.srt", "ja"),
            ("video_es.srt", "es"),
            ("video_japanese.srt", "ja"),
            ("captions.srt", "und"),
            ("subtitles.srt", "und"),
            ("video_UnknownLang.srt", "und"),
            ("video.srt", "und"),
        ],
    )
    def test_filename_to_language_code(self, filename: str, expected: str):
        assert _language_code_from_srt_filename(filename) == expected


class TestApprovePendingClient:
    """Tests for approve_pending_client function."""

    @pytest.mark.asyncio
    async def test_approve_pending_client(self, ray_client):
        """Test approving a pending client."""
        with patch(
            "app.slack.listener_actions.approve_pending_groups", new_callable=AsyncMock
        ) as mock_approve:
            mock_approve.return_value = {"status": "approved"}
            result = await approve_pending_client(
                ray_client, "pending-123", "pending.user"
            )
            mock_approve.assert_called_once_with(
                ray_client.id, "pending-123", "pending.user"
            )
            assert result == {"status": "approved"}


class TestGetGroups:
    """Tests for get_groups function."""

    @pytest.mark.asyncio
    async def test_get_groups(self, ray_client):
        """Test getting groups for a client."""
        mock_group1 = MagicMock()
        mock_group1.name = "Group A"
        mock_group1.id = "group-1"

        mock_group2 = MagicMock()
        mock_group2.name = "group b"
        mock_group2.id = "group-2"

        mock_group3 = MagicMock()
        mock_group3.name = "Group C"
        mock_group3.id = "group-3"

        mock_service = MagicMock()
        mock_service.get_groups = AsyncMock(
            return_value=[mock_group3, mock_group1, mock_group2]
        )

        with patch(
            "app.slack.listener_actions.RayService.get_service",
            return_value=mock_service,
        ):
            result = await get_groups(ray_client)

            # Should be sorted by name lowercase
            assert len(result) == 3
            assert result[0]["value"] == "group-1"  # Group A
            assert result[1]["value"] == "group-2"  # group b
            assert result[2]["value"] == "group-3"  # Group C

            # Check format
            assert result[0]["text"]["type"] == "plain_text"
            assert result[0]["text"]["text"] == "Group A"
            assert result[0]["text"]["emoji"] is False

    @pytest.mark.asyncio
    async def test_get_groups_empty(self, ray_client):
        """Test getting groups when there are no groups."""
        mock_service = MagicMock()
        mock_service.get_groups = AsyncMock(return_value=[])

        with patch(
            "app.slack.listener_actions.RayService.get_service",
            return_value=mock_service,
        ):
            result = await get_groups(ray_client)
            assert result == []


class TestUpdateMachineTranslationScore:
    """Tests for update_machine_translation_score function."""

    @pytest.mark.asyncio
    async def test_update_machine_translation_score_early_return(self):
        """Test that update_machine_translation_score returns early (disabled function)."""
        # This function is currently disabled and returns early
        client = AsyncMock()
        # AutoTranslationMessage requires source_text to be non-empty
        message = AutoTranslationMessage(
            "Hello", "en", translations={"fr": ["Bonjour"]}
        )
        result = await update_machine_translation_score(
            client, "C123", "123456.789", message, "en", "Hello", [("fr", "Bonjour")]
        )
        # Should return None (early return)
        assert result is None
        # Client should not be called since function returns early
        client.chat_update.assert_not_called()


class TestSubmitJob:
    """Tests for submit_job function."""

    @pytest.mark.asyncio
    async def test_submit_job_success(self, ray_client):
        """Test successful job submission."""
        from app.slack.templates.models import NewJobForm, RayLanguage, SlackFile

        mock_client = AsyncMock()
        mock_file_paths = ["/tmp/file1.txt", "/tmp/file2.txt"]
        mock_responses = [MagicMock(), MagicMock()]

        form = NewJobForm(
            files=[
                SlackFile(id="file1", title="test1.txt"),
                SlackFile(id="file2", title="test2.txt"),
            ],
            source_lang=RayLanguage(code="en", name="English"),
            target_langs=[RayLanguage(code="fr", name="French")],
            group_id="group-123",
            service="Translation",
            timeframe="3",
            reference="REF-123",
            notes="Test notes",
        )

        with patch(
            "app.slack.listener_actions.download_files", new_callable=AsyncMock
        ) as mock_download:
            mock_download.return_value = mock_file_paths

            mock_service = MagicMock()
            mock_service.new_job = AsyncMock(return_value=mock_responses)

            with patch(
                "app.slack.listener_actions.RayService.get_service",
                return_value=mock_service,
            ):
                result = await submit_job(mock_client, ray_client, form)

                mock_download.assert_called_once()
                mock_service.new_job.assert_called_once_with(
                    files=mock_file_paths,
                    sl="en",
                    tl=["fr"],
                    group_id="group-123",
                    workflow="TRANSLATION",
                    timeframe="3",
                    reference="REF-123",
                    job_notes="Test notes",
                )
                assert result == mock_responses

    @pytest.mark.asyncio
    async def test_submit_job_no_files_downloaded(self, ray_client):
        """Test job submission when files fail to download."""
        from app.slack.templates.models import NewJobForm, RayLanguage, SlackFile

        mock_client = AsyncMock()
        form = NewJobForm(
            files=[SlackFile(id="file1", title="test1.txt")],
            source_lang=RayLanguage(code="en", name="English"),
            target_langs=[RayLanguage(code="fr", name="French")],
            group_id="group-123",
            service="Translation",
            timeframe="3",
            reference="REF-123",
            notes="",
        )

        with patch(
            "app.slack.listener_actions.download_files", new_callable=AsyncMock
        ) as mock_download:
            mock_download.return_value = []

            with pytest.raises(Exception, match="Failed to download files"):
                await submit_job(mock_client, ray_client, form)


class TestAiTranslateHelp:
    """Tests for ai_translate_help function."""

    @pytest.mark.asyncio
    async def test_ai_translate_help_with_respond(self, ray_client, context):
        """Test ai_translate_help using respond when available."""
        mock_client = AsyncMock()
        mock_respond = AsyncMock()
        context["response_url"] = "https://hooks.slack.com/test"
        context["channel_id"] = "C123"

        with patch.object(
            context.__class__,
            "respond",
            new_callable=PropertyMock,
            return_value=mock_respond,
        ):
            await ai_translate_help(mock_client, context, ray_client)

            mock_respond.assert_called_once()
            call_args = mock_respond.call_args
            assert "text" in call_args[1]
            assert "blocks" in call_args[1]
            mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_ai_translate_help_with_chat_post_message(self, ray_client, context):
        """Test ai_translate_help using chat_postMessage when respond not available."""
        mock_client = AsyncMock()
        context["response_url"] = None
        context["channel_id"] = "C123"

        await ai_translate_help(mock_client, context, ray_client, channel_id="C456")

        mock_client.chat_postMessage.assert_called_once()
        call_args = mock_client.chat_postMessage.call_args
        assert call_args[1]["channel"] == "C456"
        assert "text" in call_args[1]
        assert "blocks" in call_args[1]

    @pytest.mark.asyncio
    async def test_ai_translate_help_with_thread_ts(self, ray_client, context):
        """Test ai_translate_help with thread timestamp."""
        mock_client = AsyncMock()
        context["response_url"] = None
        context["channel_id"] = "C123"

        await ai_translate_help(
            mock_client, context, ray_client, thread_ts="123456.789"
        )

        mock_client.chat_postMessage.assert_called_once()
        assert mock_client.chat_postMessage.call_args[1]["thread_ts"] == "123456.789"


class TestVerifyHelp:
    """Tests for verify_help function."""

    @pytest.mark.asyncio
    async def test_verify_help_with_respond(self, ray_client, context):
        """Test verify_help using respond when available."""
        mock_client = AsyncMock()
        mock_respond = AsyncMock()
        context["response_url"] = "https://hooks.slack.com/test"
        context["channel_id"] = "C123"

        with patch.object(
            context.__class__,
            "respond",
            new_callable=PropertyMock,
            return_value=mock_respond,
        ):
            await verify_help(mock_client, context, ray_client)

            mock_respond.assert_called_once()
            call_args = mock_respond.call_args
            assert "text" in call_args[1]
            assert "blocks" in call_args[1]
            assert call_args[1]["replace_original"] is False
            mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_help_with_chat_post_message(self, ray_client, context):
        """Test verify_help using chat_postMessage when respond not available."""
        mock_client = AsyncMock()
        context["response_url"] = None
        context["channel_id"] = "C123"

        await verify_help(mock_client, context, ray_client, channel_id="C456")

        mock_client.chat_postMessage.assert_called_once()
        call_args = mock_client.chat_postMessage.call_args
        assert call_args[1]["channel"] == "C456"
        assert "text" in call_args[1]
        assert "blocks" in call_args[1]

    @pytest.mark.asyncio
    async def test_verify_help_with_thread_ts(self, ray_client, context):
        """Test verify_help with thread timestamp."""
        mock_client = AsyncMock()
        context["response_url"] = None
        context["channel_id"] = "C123"

        await verify_help(mock_client, context, ray_client, thread_ts="123456.789")

        mock_client.chat_postMessage.assert_called_once()
        assert mock_client.chat_postMessage.call_args[1]["thread_ts"] == "123456.789"


class TestGetMtTranslation:
    @pytest.mark.asyncio
    async def test_direct_mt_uses_non_empty_frca_glossary_when_only_en_us_exists(
        self, user_id, team_id, ray_client
    ):
        from app.auth.connector import RayConnection, RayContext, RaySuperGroup

        mock_client = AsyncMock()
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "D123",
                "ray": RayConnection(super_group=[super_group], client=ray_client),
            }
        )

        with (
            patch(
                "app.slack.listener_actions.require_mt_tokens",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.slack.listener_actions.resolve_language",
                new_callable=AsyncMock,
                side_effect=[["fr-ca"], ["en"]],
            ),
            patch(
                "app.slack.listener_actions.get_group_id",
                new_callable=AsyncMock,
                return_value="group-a:group-b",
            ),
            patch(
                "app.mt.service.get_client_groups",
                new_callable=AsyncMock,
                return_value=["group-a"],
            ),
            patch("app.mt.service.fetch_one", new_callable=AsyncMock) as mock_fetch_one,
            patch(
                "app.slack.listener_actions.send_mt_translation_request",
                new_callable=AsyncMock,
            ) as mock_send_mt,
        ):

            async def _fetch_one_side_effect(query, _engine):
                params = {name: bind.value for name, bind in query._bindparams.items()}
                if params["sl"] == "en-us" and params["tl"] == "fr-ca":
                    return {"terminology_id": "group-a:en-us:fr-ca"}
                return None

            mock_fetch_one.side_effect = _fetch_one_side_effect

            await get_mt_translation(
                mock_client,
                context,
                target_lang="fr-ca",
                source_lang="en-us",
                sentence="this is my GLSS Account test",
            )

            mock_send_mt.assert_called_once()
            assert (
                mock_send_mt.await_args.kwargs["service_language_mapping"]["microsoft"][
                    "fr-ca"
                ]
                == "group-a:en-us:fr-ca"
            )
            assert (
                mock_send_mt.await_args.kwargs["extra_data"].service_language_mapping[
                    "microsoft"
                ]["fr-ca"]
                == "group-a:en-us:fr-ca"
            )

    @pytest.mark.asyncio
    async def test_get_mt_translation_skips_when_resolved_source_equals_target(
        self, user_id, team_id, ray_client
    ):
        """Skip MT and notify the user when resolved source and target match exactly."""
        from app.auth.connector import RayConnection, RayContext, RaySuperGroup

        mock_client = AsyncMock()
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "D123",
                "ray": RayConnection(super_group=[super_group], client=ray_client),
            }
        )

        with (
            patch(
                "app.slack.listener_actions.require_mt_tokens",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.slack.listener_actions.resolve_language",
                new_callable=AsyncMock,
                side_effect=[["en"], ["en"]],
            ),
            patch(
                "app.slack.listener_actions.send_mt_translation_request",
                new_callable=AsyncMock,
            ) as mock_send_mt,
        ):
            await get_mt_translation(
                mock_client,
                context,
                target_lang="en",
                source_lang="en",
                sentence="hello",
                usage_type="shortcut_translate",
            )

        mock_send_mt.assert_not_called()
        mock_client.chat_postMessage.assert_called_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert post_kwargs["channel"] == "D123"
        assert "same" in post_kwargs["text"].lower()
        assert "en" in post_kwargs["text"].lower()

    @pytest.mark.asyncio
    async def test_get_mt_translation_skips_for_zh_cn_to_zh_dialect_pair(
        self, user_id, team_id, ray_client
    ):
        """Skip MT when resolved codes are dialect/base pair (zh-CN ↔ zh).

        Mirrors the real ``resolve_language`` behaviour where ``zh-CN`` falls
        through to a DB lookup and comes back as ``zh-CN`` while ``zh`` is
        returned as ``zh`` from the dict path. The raw equality check used to
        miss this; the helper now catches it.
        """
        from app.auth.connector import RayConnection, RayContext, RaySuperGroup

        mock_client = AsyncMock()
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "D123",
                "ray": RayConnection(super_group=[super_group], client=ray_client),
            }
        )

        with (
            patch(
                "app.slack.listener_actions.require_mt_tokens",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.slack.listener_actions.resolve_language",
                new_callable=AsyncMock,
                # First call resolves target "zh" -> ["zh"], second resolves
                # source "zh-CN" -> ["zh-CN"] (DB-resolved, case preserved).
                side_effect=[["zh"], ["zh-CN"]],
            ),
            patch(
                "app.slack.listener_actions.send_mt_translation_request",
                new_callable=AsyncMock,
            ) as mock_send_mt,
        ):
            await get_mt_translation(
                mock_client,
                context,
                target_lang="zh",
                source_lang="zh-CN",
                sentence="你好",
                usage_type="shortcut_translate",
            )

        mock_send_mt.assert_not_called()
        mock_client.chat_postMessage.assert_called_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert post_kwargs["channel"] == "D123"
        assert "same" in post_kwargs["text"].lower()


class TestPostJobStatus:
    """Tests for post_job_status function."""

    @pytest.mark.asyncio
    async def test_post_job_status_no_channel(self, ray_client, context):
        """Test post_job_status raises error when no channel available."""
        mock_client = AsyncMock()
        context["channel_id"] = None
        context["user_id"] = None
        context["response_url"] = None

        with pytest.raises(AssertionError, match="No channel to post to"):
            await post_job_status(mock_client, context, ray_client, "TJ123")

    @pytest.mark.asyncio
    async def test_post_job_status_job_not_found(self, ray_client, context):
        """Test post_job_status when job is not found."""
        mock_client = AsyncMock()
        context["channel_id"] = "C123"
        context["response_url"] = None
        # Add log mock for API logging
        mock_log = MagicMock()
        mock_log.add_api_log = MagicMock()
        context["log"] = mock_log

        mock_service = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.url = "https://api.example.com/job/TJ123"
        mock_response.json = MagicMock(return_value={})
        mock_response.content = b""
        mock_service.get_job = AsyncMock(return_value=(None, mock_response))

        with patch(
            "app.slack.listener_actions.RayService.get_service",
            return_value=mock_service,
        ):
            await post_job_status(mock_client, context, ray_client, "TJ123")

            # Should post InvalidJobMessage (only text, not blocks)
            mock_client.chat_postMessage.assert_called_once()
            call_args = mock_client.chat_postMessage.call_args
            assert (
                "TJ123" in call_args[1]["text"].upper()
                or "find" in call_args[1]["text"].lower()
            )


class TestPostBatchList:
    """Tests for post_batch_list function."""

    @pytest.mark.asyncio
    async def test_post_batch_list_no_channel(self, ray_client, context):
        """Test post_batch_list raises error when no channel available."""
        mock_client = AsyncMock()
        context["channel_id"] = None
        context["user_id"] = None
        context["response_url"] = None

        with pytest.raises(AssertionError, match="No channel to post to"):
            await post_batch_list(mock_client, context, ray_client, "TJ123", 1, 5)

    @pytest.mark.asyncio
    async def test_post_batch_list_job_not_found(self, ray_client, context):
        """Test post_batch_list when job is not found (empty jobs list)."""
        mock_client = AsyncMock()
        context["channel_id"] = "C123"
        context["response_url"] = None
        mock_log = MagicMock()
        mock_log.add_api_log = MagicMock()
        context["log"] = mock_log

        mock_service = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.url = "https://api.example.com/job/TJ123"
        mock_response.json = MagicMock(return_value={})
        mock_response.content = b""
        mock_response.headers = {}
        # Return empty list instead of None to trigger the else clause
        mock_service.get_job = AsyncMock(return_value=([], mock_response))

        with patch(
            "app.slack.listener_actions.RayService.get_service",
            return_value=mock_service,
        ):
            await post_batch_list(mock_client, context, ray_client, "TJ123", 1, 5)

            # Should post InvalidJobMessage when jobs list is empty
            mock_client.chat_postMessage.assert_called_once()
            call_args = mock_client.chat_postMessage.call_args
            assert (
                "TJ123" in call_args[1]["text"].upper()
                or "find" in call_args[1]["text"].lower()
            )

    @pytest.mark.asyncio
    async def test_post_batch_list_shows_empty_state_when_job_has_no_batches(
        self, ray_client, context
    ):
        """Valid jobs with no batches should return a clear empty-state message."""
        mock_client = AsyncMock()
        context["channel_id"] = "C123"
        context["response_url"] = None
        mock_log = MagicMock()
        mock_log.add_api_log = MagicMock()
        context["log"] = mock_log

        job = SimpleNamespace(
            id="TJ123456",
            status="IN_PROGRESS",
            batches="[]",
            pagination=SimpleNamespace(page=1, total_pages=1, rows_per_page=5),
        )
        mock_service = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://api.example.com/job/TJ123456"
        mock_response.json = MagicMock(return_value={})
        mock_response.content = b""
        mock_response.headers = {}
        mock_service.get_job = AsyncMock(return_value=([job], mock_response))

        with patch(
            "app.slack.listener_actions.RayService.get_service",
            return_value=mock_service,
        ):
            await post_batch_list(mock_client, context, ray_client, "TJ123456", 1, 5)

        mock_client.chat_postMessage.assert_called_once()
        call_args = mock_client.chat_postMessage.call_args
        assert "no in-progress files are available" in call_args[1]["text"].lower()


class TestPostFileList:
    """Tests for post_file_list function."""

    @pytest.mark.asyncio
    async def test_post_file_list_no_channel(self, ray_client, context):
        """Test post_file_list raises error when no channel available."""
        mock_client = AsyncMock()
        context["channel_id"] = None
        context["user_id"] = None
        context["response_url"] = None

        with pytest.raises(AssertionError, match="No channel to post to"):
            await post_file_list(mock_client, context, ray_client, "TJ123", 1, 5)

    @pytest.mark.asyncio
    async def test_post_file_list_job_not_found(self, ray_client, context):
        """Test post_file_list when job is not found (empty jobs list)."""
        mock_client = AsyncMock()
        context["channel_id"] = "C123"
        context["response_url"] = None
        mock_log = MagicMock()
        mock_log.add_api_log = MagicMock()
        context["log"] = mock_log

        mock_service = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.url = "https://api.example.com/job/TJ123"
        mock_response.json = MagicMock(return_value={})
        mock_response.content = b""
        mock_response.headers = {}
        # Return empty list instead of None to trigger the else clause
        mock_service.get_job = AsyncMock(return_value=([], mock_response))

        with patch(
            "app.slack.listener_actions.RayService.get_service",
            return_value=mock_service,
        ):
            await post_file_list(mock_client, context, ray_client, "TJ123", 1, 5)

            # Should post InvalidJobMessage when jobs list is empty
            mock_client.chat_postMessage.assert_called_once()
            call_args = mock_client.chat_postMessage.call_args
            assert (
                "TJ123" in call_args[1]["text"].upper()
                or "find" in call_args[1]["text"].lower()
            )

    @pytest.mark.asyncio
    async def test_post_file_list_shows_empty_state_when_job_has_no_files(
        self, ray_client, context
    ):
        """Valid jobs with no translated files should return a clear empty-state message."""
        mock_client = AsyncMock()
        context["channel_id"] = "C123"
        context["response_url"] = None
        mock_log = MagicMock()
        mock_log.add_api_log = MagicMock()
        context["log"] = mock_log

        job = SimpleNamespace(
            id="TJ123456",
            translated_file=[],
            sl=SimpleNamespace(name="EN-US"),
            f_pagination=SimpleNamespace(page=1, total_pages=1, rows_per_page=5),
            pagination=SimpleNamespace(page=1, total_pages=1, rows_per_page=5),
        )
        mock_service = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://api.example.com/job/TJ123456"
        mock_response.json = MagicMock(return_value={})
        mock_response.content = b""
        mock_response.headers = {}
        mock_service.get_job = AsyncMock(return_value=([job], mock_response))

        with patch(
            "app.slack.listener_actions.RayService.get_service",
            return_value=mock_service,
        ):
            await post_file_list(mock_client, context, ray_client, "TJ123456", 1, 5)

        mock_client.chat_postMessage.assert_called_once()
        call_args = mock_client.chat_postMessage.call_args
        assert "no completed files are available" in call_args[1]["text"].lower()


class TestPostJobTargetLang:
    """Tests for post_job_target_lang function."""

    @pytest.mark.asyncio
    async def test_post_job_target_lang_empty_jobs(self, ray_client, context):
        """Test post_job_target_lang with empty jobs list."""
        mock_client = AsyncMock()
        context["channel_id"] = "C123"  # Set channel so it doesn't fail early
        context["response_url"] = None
        # Need to set up ray connection for this function
        from app.auth.connector import RayConnection

        context["ray"] = RayConnection(super_group=[], client=ray_client)
        mock_log = MagicMock()
        mock_log.add_api_log = MagicMock()
        context["log"] = mock_log

        # This function doesn't check channel upfront, it calls get_job first
        # So we need to mock the service to avoid the actual check
        mock_service = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.url = "https://api.example.com/job/TJ123"
        mock_response.json = MagicMock(return_value={})
        mock_response.content = b""
        mock_response.headers = {}
        # Return empty list to trigger the else clause
        mock_service.get_job = AsyncMock(return_value=([], mock_response))

        with patch(
            "app.slack.listener_actions.RayService.get_service",
            return_value=mock_service,
        ):
            # This function doesn't raise AssertionError for no channel
            # It just processes the job and may call other functions
            await post_job_target_lang(
                mock_client, context, ray_client, "TJ123", channel_id="C123"
            )
            # Should post InvalidJobMessage when jobs list is empty
            mock_client.chat_postMessage.assert_called_once()
            call_args = mock_client.chat_postMessage.call_args
            assert (
                "TJ123" in call_args[1]["text"].upper()
                or "find" in call_args[1]["text"].lower()
            )
