import json
from types import SimpleNamespace
from unittest.mock import patch

from app.auth.connector import RayClient, RayConnection, RaySuperGroup
from app.slack.templates.messages import (
    AutoTranslateSettingsChangedMessage,
    AutoTranslateSettingsDisabledMessage,
    AutoTranslationMessage,
    BatchListMessage,
    CancelJobMessage,
    ClientAlreadyApprovedMessage,
    ClientApprovedMessage,
    ConnectionInfoMessage,
    DocInvalidPdfErrorMessage,
    DocMtMessage,
    DocParseErrorMessage,
    EvaluateErrorMessage,
    EvaluateSuccessMessage,
    FileListMessage,
    HelpMessage,
    HumanJobMessage,
    InfoMessage,
    InvalidCommandMessage,
    InvalidJobMessage,
    InvalidMTResultMessage,
    JobCreationMessage,
    JobStatusNoIdMessage,
    JobTargetsNoIdMessage,
    LoginMessage,
    LogoutMessage,
    MachineTranslationMessage,
    MediaEmbedOptionMessage,
    NewJobMessage,
    OnboardingMessage,
    RequiresMtTokenMessage,
    SlackPermissionsMessage,
    SrtTranslateMessage,
    SuccessfulLoginMessage,
    SuccessfulLogoutMessage,
    TranscriptionMessage,
    VerifyCompleteMessage,
    VerifyHelperMessage,
    VideoOptionsMessage,
    WelcomeBackMessage,
    get_account_blocks,
    get_workspace_block,
)
from app.translate import _


def _blocks_contain_action(blocks: list, action_id: str) -> bool:
    """Return True if any block element has the given action_id."""
    for block in blocks:
        for element in block.get("elements", []):
            if element.get("action_id") == action_id:
                return True
            if element.get("accessory", {}).get("action_id") == action_id:
                return True
        if block.get("accessory", {}).get("action_id") == action_id:
            return True
    return False


def _blocks_contain_text(blocks: list, text: str) -> bool:
    """Return True if any block or element text contains the given text."""
    for block in blocks:
        block_text = block.get("text", {})
        if text in block_text.get("text", ""):
            return True
        for element in block.get("elements", []):
            element_text = element.get("text")
            if isinstance(element_text, dict) and text in element_text.get("text", ""):
                return True
        accessory = block.get("accessory", {})
        if text in accessory.get("text", {}).get("text", ""):
            return True
    return False


def _assert_media_translation_help(blocks: list) -> None:
    media_url = (
        "https://help.straker.ai/en/docs/ai-translate-for-videos-in-straker-translate-app-for-slack"
    )
    assert _blocks_contain_text(blocks, "Learn Media Translation and Transcription")
    assert _blocks_contain_text(blocks, "Media Translation Help")
    assert _blocks_contain_action(blocks, "link_media_translation_help")
    assert any(block.get("accessory", {}).get("url") == media_url for block in blocks)


def _pagination(page: int = 1, total_pages: int = 1, rows_per_page: int = 5):
    return SimpleNamespace(
        page=page, total_pages=total_pages, rows_per_page=rows_per_page
    )


class TestLoginMessage:
    def test_no_enterprise_id(self, user_id: str, team_id: str, channel_id: str):
        message = LoginMessage(user_id, team_id, None, channel_id)
        assert message.text == "Connect your account"
        message = LoginMessage(user_id, team_id, "", channel_id)
        assert message.text == "Connect your account"

    def test_normal_variation(
        self, user_id: str, team_id: str, enterprise_id: str, channel_id: str
    ):
        message = LoginMessage(user_id, team_id, enterprise_id, channel_id)
        assert message.text == "Connect your account"
        assert (
            "In order to use the Straker Translate features, please login. Click this button below;"
            in message.blocks[0]["text"]["text"]
        )

    def test_connected_variation(
        self,
        user_id: str,
        team_id: str,
        enterprise_id: str,
        channel_id: str,
        ray_client: RayClient,
    ):
        message = LoginMessage(
            user_id, team_id, enterprise_id, channel_id, ray_client=ray_client
        )
        assert message.text == "Connect your account"
        assert "Your connected account is" in message.blocks[0]["text"]["text"]
        assert ray_client.username in message.blocks[0]["text"]["text"]
        assert (
            "You can connect a different account by clicking this button"
            in message.blocks[0]["text"]["text"]
        )

    def test_get_job_variation(
        self, user_id: str, team_id: str, enterprise_id: str, channel_id: str
    ):
        message = LoginMessage(
            user_id, team_id, enterprise_id, channel_id, variation=LoginMessage.GET_JOB
        )
        assert message.text == "Connect your account"
        assert (
            "Connect your account to view your jobs."
            in message.blocks[0]["text"]["text"]
        )

    def test_new_job_variation(
        self, user_id: str, team_id: str, enterprise_id: str, channel_id: str
    ):
        message = LoginMessage(
            user_id, team_id, enterprise_id, channel_id, variation=LoginMessage.NEW_JOB
        )
        assert message.text == "Connect your account"
        assert (
            "Connect your account to submit a new translation job"
            in message.blocks[0]["text"]["text"]
        )

    def test_variation_overrides_connected_variation(
        self,
        user_id: str,
        team_id: str,
        enterprise_id: str,
        channel_id: str,
        ray_client: RayClient,
    ):
        """The `ray_client` argument should be ignored if the `variation` argument
        is a valid variation.
        """
        message = LoginMessage(
            user_id,
            team_id,
            enterprise_id,
            channel_id,
            ray_client=ray_client,
            variation=LoginMessage.GET_JOB,
        )
        assert message.text == "Connect your account"
        assert (
            "Connect your account to view your jobs."
            in message.blocks[0]["text"]["text"]
        )
        assert ray_client.username not in message.blocks[0]["text"]["text"]
        assert (
            "You can connect a different account by clicking this button"
            not in message.blocks[0]["text"]["text"]
        )


class TestVerifyCompleteMessage:
    def test_verify_complete_message(self):
        job_title = "Sample Job"
        lang_label = "English"

        # Create a VerifyCompleteMessage instance
        message = VerifyCompleteMessage(job_title, lang_label)

        # Assert the message blocks are formatted correctly
        assert len(message.blocks) == 1  # Should contain one block
        assert message.blocks[0]["type"] == "section"  # Block type
        assert message.blocks[0]["text"]["type"] == "mrkdwn"  # Text type

        # Check that the text matches the actual implementation
        expected_text = _(
            "Your request has been completed. Please download the file below"
        )
        assert message.blocks[0]["text"]["text"] == expected_text

        # Assert the message title is correct
        assert message.text == _("Human Translation")


class TestLogoutMessage:
    """Tests for LogoutMessage class."""

    def test_logout_message(self, ray_client):
        """Test logout message creation."""
        message = LogoutMessage(ray_client)
        assert message.text == "Disconnect your account"
        assert len(message.blocks) == 2
        assert message.blocks[1]["type"] == "actions"
        assert len(message.blocks[1]["elements"]) == 2
        assert message.blocks[1]["elements"][0]["action_id"] == "disconnect"

    def test_logout_message_sso(self, ray_client):
        """Test logout message with SSO client."""
        ray_client.sso = True
        message = LogoutMessage(ray_client)
        assert message.text == "Disconnect your account"
        assert ray_client.username in message.blocks[0]["text"]["text"]


class TestSuccessfulLogoutMessage:
    """Tests for SuccessfulLogoutMessage class."""

    def test_successful_logout_message(self, user_id):
        """Test successful logout message."""
        message = SuccessfulLogoutMessage(
            user_id, is_sso=False, ray_username="test.user"
        )
        assert message.text == "Your account is now disconnected."
        assert len(message.blocks) == 2
        assert user_id in message.blocks[0]["text"]["text"]

    def test_successful_logout_message_sso(self, user_id):
        """Test successful logout message with SSO."""
        message = SuccessfulLogoutMessage(
            user_id, is_sso=True, ray_username="test.user"
        )
        assert message.text == "Your account is now disconnected."
        assert "test.user" in message.blocks[0]["text"]["text"]

    def test_successful_logout_message_no_username(self, user_id):
        """Test successful logout message without username."""
        message = SuccessfulLogoutMessage(user_id, is_sso=False, ray_username=None)
        assert message.text == "Your account is now disconnected."
        assert user_id in message.blocks[0]["text"]["text"]


class TestInvalidCommandMessage:
    """Tests for InvalidCommandMessage class."""

    def test_invalid_command_message(self):
        """Test invalid command message."""
        message = InvalidCommandMessage()
        assert "Invalid command" in message.text
        assert "help" in message.text.lower()


class TestClientApprovedMessage:
    """Tests for ClientApprovedMessage class."""

    def test_client_approved_message(self):
        """Test client approved message."""
        message = ClientApprovedMessage("approved.user")
        assert "approved.user" in message.text
        assert "approved" in message.text.lower()


class TestClientAlreadyApprovedMessage:
    """Tests for ClientAlreadyApprovedMessage class."""

    def test_client_already_approved_message(self):
        """Test client already approved message."""
        message = ClientAlreadyApprovedMessage("approved.user")
        assert "approved.user" in message.text
        assert "already been approved" in message.text.lower()


class TestGetWorkspaceBlock:
    """Tests for get_workspace_block function."""

    def test_get_workspace_block_with_connection(self, team_id):
        """Test get_workspace_block with ray connection."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        block = get_workspace_block(ray_connection)

        assert block["type"] == "section"
        assert "Test Group" in block["text"]["text"]

    def test_get_workspace_block_without_connection(self):
        """Test get_workspace_block without ray connection."""
        block = get_workspace_block(None)

        assert block["type"] == "section"
        assert "not connected" in block["text"]["text"].lower()


class TestGetAccountBlocks:
    """Tests for get_account_blocks function."""

    def test_get_account_blocks_with_client(self, ray_client, user_id, team_id):
        """Test get_account_blocks with ray client."""
        blocks, text = get_account_blocks(
            ray_client, user_id, team_id, None, "C123", is_ibm=False
        )

        assert isinstance(blocks, list)
        assert isinstance(text, str)
        assert len(blocks) > 0

    def test_get_account_blocks_without_client(self, user_id, team_id):
        """Test get_account_blocks without ray client."""
        blocks, text = get_account_blocks(
            None, user_id, team_id, None, "C123", is_ibm=False
        )

        assert isinstance(blocks, list)
        assert isinstance(text, str)

    def test_get_account_blocks_ibm(self, ray_client, user_id, team_id):
        """Test get_account_blocks for IBM enterprise."""
        blocks, text = get_account_blocks(
            ray_client, user_id, team_id, "E123", "C123", is_ibm=True
        )

        assert isinstance(blocks, list)
        assert isinstance(text, str)


class TestOnboardingMessage:
    """Tests for OnboardingMessage class."""

    def test_onboarding_message_with_login_prompt(self, user_id, team_id, channel_id):
        """Test onboarding message with login prompt."""
        message = OnboardingMessage(
            user_id, team_id, None, channel_id, prompt_login=True
        )
        assert "Welcome" in message.text
        assert len(message.blocks) > 1
        # Should have login button when prompt_login is True
        assert any(block.get("type") == "actions" for block in message.blocks)

    def test_onboarding_message_without_login_prompt(
        self, user_id, team_id, channel_id
    ):
        """Test onboarding message without login prompt."""
        message = OnboardingMessage(
            user_id, team_id, None, channel_id, prompt_login=False
        )
        assert "Welcome" in message.text
        # Should not have login button when prompt_login is False
        assert not any(block.get("type") == "actions" for block in message.blocks)

    def test_onboarding_message_no_team_id(self, user_id, channel_id):
        """Test onboarding message without team_id."""
        message = OnboardingMessage(user_id, None, None, channel_id, prompt_login=True)
        assert "Welcome" in message.text
        # Should not have login button when team_id is None
        assert not any(block.get("type") == "actions" for block in message.blocks)


class TestWelcomeBackMessage:
    """Tests for WelcomeBackMessage class."""

    def test_welcome_back_message_with_connection(self, user_id, team_id):
        """Test welcome back message with ray connection."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
            enable_verify_in_slack=True,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        message = WelcomeBackMessage(user_id, ray_connection)

        assert "Welcome" in message.text
        assert len(message.blocks) > 0
        assert not _blocks_contain_action(message.blocks, "report_insights")

    def test_welcome_back_message_hides_quality_help_for_ibm(self, user_id, team_id):
        """IBM workspaces should keep human help without QE help."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
            enable_verify_in_slack=True,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        with patch("app.slack.templates.messages.is_ibm_enterprise", return_value=True):
            message = WelcomeBackMessage(user_id, ray_connection, enterprise_id="E123")

        assert not _blocks_contain_text(message.blocks, "Quality Evaluation")
        assert _blocks_contain_text(message.blocks, "Human Translation")

    def test_welcome_back_message_without_connection(self, user_id):
        """Test welcome back message without ray connection."""
        message = WelcomeBackMessage(user_id, None)

        assert "Welcome" in message.text
        assert len(message.blocks) > 0
        assert not _blocks_contain_action(message.blocks, "report_insights")

    def test_welcome_back_message_includes_media_translation_help(
        self, user_id, team_id
    ):
        """RAY-79731: welcome back message lists media translation help."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
            enable_verify_in_slack=True,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        message = WelcomeBackMessage(user_id, ray_connection)

        _assert_media_translation_help(message.blocks)


class TestSuccessfulLoginMessage:
    """Tests for SuccessfulLoginMessage class."""

    def test_successful_login_message(self, user_id, team_id):
        """Test successful login message."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        message = SuccessfulLoginMessage(user_id, "test.user", ray_connection)
        assert "Login was successful" in message.text
        assert len(message.blocks) > 0
        assert not _blocks_contain_action(message.blocks, "report_insights")

    def test_successful_login_message_hides_quality_help_for_ibm(
        self, user_id, team_id
    ):
        """IBM workspaces should keep human help without QE help."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
            enable_verify_in_slack=True,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        with patch("app.slack.templates.messages.is_ibm_enterprise", return_value=True):
            message = SuccessfulLoginMessage(
                user_id, "test.user", ray_connection, enterprise_id="E123"
            )

        assert not _blocks_contain_text(message.blocks, "Quality Evaluation")
        assert _blocks_contain_text(message.blocks, "Human Translation")

    def test_successful_login_message_includes_media_translation_help(
        self, user_id, team_id
    ):
        """RAY-79731: successful login message lists media translation help."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
            enable_verify_in_slack=True,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        message = SuccessfulLoginMessage(user_id, "test.user", ray_connection)

        _assert_media_translation_help(message.blocks)


class TestSlackPermissionsMessage:
    """Tests for SlackPermissionsMessage class."""

    def test_slack_permissions_message(self):
        """Test slack permissions message."""
        message = SlackPermissionsMessage("Test permission message")
        assert message.text == "Test permission message"
        assert len(message.blocks) == 2
        assert message.blocks[0]["text"]["text"] == "Test permission message"
        assert message.blocks[1]["type"] == "actions"

    def test_slack_permissions_message_auto_translate_variation(self):
        """Test auto translate variation of permissions message."""
        message = SlackPermissionsMessage.auto_translate_variation()
        assert "permissions" in message.text.lower()
        assert len(message.blocks) == 2


class TestInfoMessage:
    """Tests for InfoMessage class."""

    def test_info_message_with_client(self, ray_client, user_id, team_id, channel_id):
        """Test info message with ray client."""
        message = InfoMessage(
            ray_client, user_id, team_id, None, channel_id, is_ibm=False
        )
        assert isinstance(message.text, str)
        assert len(message.blocks) > 0

    def test_info_message_without_client(self, user_id, team_id, channel_id):
        """Test info message without ray client."""
        message = InfoMessage(None, user_id, team_id, None, channel_id, is_ibm=False)
        assert isinstance(message.text, str)
        assert len(message.blocks) > 0

    def test_info_message_ibm(self, ray_client, user_id, team_id, channel_id):
        """Test info message for IBM enterprise."""
        message = InfoMessage(
            ray_client, user_id, team_id, "E123", channel_id, is_ibm=True
        )
        assert isinstance(message.text, str)
        assert len(message.blocks) > 0


class TestConnectionInfoMessage:
    """Tests for ConnectionInfoMessage class."""

    def test_connection_info_message_with_connection(
        self, user_id, team_id, channel_id
    ):
        """Test connection info message with ray connection."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        message = ConnectionInfoMessage(
            ray_connection, user_id, team_id, None, channel_id
        )

        assert isinstance(message.text, str)
        assert len(message.blocks) > 0

    def test_connection_info_message_without_connection(
        self, user_id, team_id, channel_id
    ):
        """Test connection info message without ray connection."""
        message = ConnectionInfoMessage(None, user_id, team_id, None, channel_id)

        assert isinstance(message.text, str)
        assert len(message.blocks) > 0

    def test_connection_info_message_ibm(self, user_id, team_id, channel_id):
        """Test connection info message for IBM enterprise."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        message = ConnectionInfoMessage(
            ray_connection, user_id, team_id, "E123", channel_id, is_ibm=True
        )

        assert isinstance(message.text, str)
        assert len(message.blocks) > 0


class TestInvalidJobMessage:
    """Tests for InvalidJobMessage class."""

    def test_invalid_job_message(self):
        """Test invalid job message."""
        message = InvalidJobMessage("TJ123456")
        assert "TJ123456" in message.text.upper()
        assert "Cannot find" in message.text or "find" in message.text.lower()


class TestJobStatusNoIdMessage:
    """Tests for JobStatusNoIdMessage class."""

    def test_job_status_no_id_message(self):
        """Test job status no ID message."""
        message = JobStatusNoIdMessage()
        assert "status" in message.text.lower() or "reference" in message.text.lower()
        assert "TJ" in message.text or "reference" in message.text.lower()


class TestJobTargetsNoIdMessage:
    """Tests for JobTargetsNoIdMessage class."""

    def test_job_targets_no_id_message(self):
        """Test job targets no ID message."""
        message = JobTargetsNoIdMessage()
        assert isinstance(message.text, str)
        assert len(message.text) > 0


class TestInvalidMTResultMessage:
    """Tests for InvalidMTResultMessage class."""

    def test_invalid_mt_result_message(self):
        """Test invalid MT result message."""
        message = InvalidMTResultMessage()
        assert isinstance(message.text, str)
        assert len(message.text) > 0


class TestTranscriptionMessage:
    """Tests for TranscriptionMessage class."""

    def test_transcription_message(self):
        """Test transcription message."""
        message = TranscriptionMessage("test_video.mp4")
        assert (
            "test_video.mp4" in message.text or "transcription" in message.text.lower()
        )
        assert isinstance(message.text, str)


class TestHelpMessage:
    """Tests for HelpMessage class."""

    def test_help_message_with_connection(self, user_id, team_id):
        """Test help message with ray connection."""
        from app.auth.connector import RayContext

        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
            enable_verify_in_slack=True,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
            }
        )
        context["ray"] = ray_connection
        message = HelpMessage(context)
        assert "help" in message.text.lower() or "wave" in message.text.lower()
        assert len(message.blocks) > 0
        assert not _blocks_contain_action(message.blocks, "report_insights")

    def test_help_message_hides_quality_help_for_ibm(self, user_id, team_id):
        """IBM workspaces should keep human help without QE help."""
        from app.auth.connector import RayContext

        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
            enable_verify_in_slack=True,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "enterprise_id": "E123",
            }
        )
        context["ray"] = ray_connection
        with patch("app.slack.templates.messages.is_ibm_enterprise", return_value=True):
            message = HelpMessage(context)

        assert not _blocks_contain_text(message.blocks, "Quality Evaluation")
        assert _blocks_contain_text(message.blocks, "Human Translation")

    def test_help_message_without_connection(self, user_id, team_id):
        """Test help message without ray connection."""
        from app.auth.connector import RayContext

        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
            }
        )
        context["ray"] = None
        message = HelpMessage(context)
        assert "help" in message.text.lower() or "wave" in message.text.lower()
        assert len(message.blocks) > 0
        assert not _blocks_contain_action(message.blocks, "report_insights")

    def test_help_message_includes_media_translation_help(self, user_id, team_id):
        """RAY-79731: Help lists media translation help with doc link."""
        from app.auth.connector import RayContext

        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
            }
        )
        context["ray"] = None
        message = HelpMessage(context)
        _assert_media_translation_help(message.blocks)


class TestVideoOptionsMessage:
    """RAY-79726: Embed Subtitles option copy."""

    def test_embed_subtitles_description_uses_translated_text(self):
        message = VideoOptionsMessage(
            channel_id="C123",
            files=[
                {
                    "file_id": "F1",
                    "file_name": "clip.mp4",
                    "duration_ms": 1000,
                }
            ],
            show_embed_option=True,
        )
        assert "final translated text" not in json.dumps(message.blocks)
        assert _blocks_contain_text(
            message.blocks,
            "*Embed Subtitles* - Transcribe, translate, and automatically embed the translated text as subtitles into your media file.",
        )


class TestNewJobMessage:
    def test_new_job_message_hides_quality_evaluation_for_ibm(self):
        files = [{"id": "F123", "title": "sample.docx"}]

        message = NewJobMessage(
            "C123",
            "123.456",
            files,
            is_verify_enabled=True,
            is_ibm_enterprise=True,
        )

        assert _blocks_contain_text(message.blocks, "AI Translation")
        assert _blocks_contain_text(message.blocks, "Human Translation")
        assert not _blocks_contain_text(message.blocks, "Quality Evaluation")

    def test_new_job_message_keeps_quality_evaluation_for_non_ibm(self):
        files = [{"id": "F123", "title": "sample.docx"}]

        message = NewJobMessage(
            "C123",
            "123.456",
            files,
            is_verify_enabled=True,
            is_ibm_enterprise=False,
        )

        assert _blocks_contain_text(message.blocks, "Human Translation")
        assert _blocks_contain_text(message.blocks, "Quality Evaluation")


class TestJobCreationMessage:
    def test_job_creation_message_uses_neutral_copy(self):
        message = JobCreationMessage("TJ123456", False)

        assert "IBM" not in message.blocks[0]["text"]["text"]
        assert "Human translation" in message.blocks[0]["text"]["text"]


class TestCancelJobMessage:
    """Tests for CancelJobMessage class."""

    def test_cancel_job_message(self):
        """Test cancel job message."""
        message = CancelJobMessage("C123", "123456.789")
        assert message.text == "Cancel a translation job"
        assert len(message.blocks) == 2
        assert message.blocks[1]["type"] == "actions"
        assert message.blocks[1]["elements"][0]["action_id"] == "cancel_job"


class TestAutoTranslateSettingsChangedMessage:
    """Tests for AutoTranslateSettingsChangedMessage class."""

    def test_auto_translate_settings_changed_message(self, user_id):
        """Test auto translate settings changed message."""
        message = AutoTranslateSettingsChangedMessage(
            user_id, "C123", ["en", "fr"], "thread"
        )
        assert user_id in message.text
        assert "C123" in message.text
        assert "thread replies in real-time" in message.text
        assert isinstance(message.text, str)

    def test_auto_translate_settings_changed_message_messages_format(self, user_id):
        """Test auto translate settings changed message with message responses."""
        message = AutoTranslateSettingsChangedMessage(
            user_id, "C123", ["en", "fr"], "message"
        )
        assert user_id in message.text
        assert "C123" in message.text
        assert "messages in real-time" in message.text


class TestAutoTranslateSettingsDisabledMessage:
    """Tests for AutoTranslateSettingsDisabledMessage class."""

    def test_auto_translate_settings_disabled_message(self, user_id):
        """Test auto translate settings disabled message."""
        message = AutoTranslateSettingsDisabledMessage(user_id, "C123")
        assert user_id in message.text
        assert "C123" in message.text
        assert "disabled" in message.text.lower()


class TestRequiresMtTokenMessage:
    """Tests for RequiresMtTokenMessage class."""

    def test_requires_mt_token_message_no_tokens_single(self):
        """Test requires MT token message with no tokens and single required."""
        message = RequiresMtTokenMessage(0, 1)
        assert "tokens" in message.text.lower()
        assert len(message.blocks) > 0

    def test_requires_mt_token_message_no_tokens_multiple(self):
        """Test requires MT token message with no tokens and multiple required."""
        message = RequiresMtTokenMessage(0, 10)
        assert "tokens" in message.text.lower()
        assert "10" in message.text

    def test_requires_mt_token_message_insufficient_tokens(self):
        """Test requires MT token message with insufficient tokens."""
        message = RequiresMtTokenMessage(5, 10)
        assert "5" in message.text
        assert "10" in message.text
        assert "purchase" in message.text.lower()


class TestHelperMessages:
    def test_verify_helper_title_uses_exportable_emoji_placeholder(self):
        message = VerifyHelperMessage()

        assert message.text == ":books: Learn Quality Evaluation Help"

    def test_human_job_helper_title_uses_exportable_emoji_placeholder(self):
        message = HumanJobMessage()

        assert message.text == ":books: Learn Human Translation Help"


class TestDocMtMessage:
    """Tests for DocMtMessage class."""

    def test_doc_mt_message(self):
        """Test doc MT message."""
        message = DocMtMessage()
        assert "translation" in message.text.lower() or "failed" in message.text.lower()
        assert len(message.blocks) > 0


class TestEvaluateErrorMessage:
    """Tests for EvaluateErrorMessage class."""

    def test_evaluate_error_message(self):
        """Test evaluate error message."""
        message = EvaluateErrorMessage()
        assert "failed" in message.text.lower() or "evaluation" in message.text.lower()
        assert len(message.blocks) > 0


class TestDocParseErrorMessage:
    """Tests for DocParseErrorMessage empty-field handling.

    RAY-79527 follow-up: when the producer sends an empty `error_data`
    (e.g. cloud-verify-consumer's `_notify_app_source_on_pipeline_failure`
    which always sends `{}`), the handler in `app/routers/ray.py` calls us
    with empty strings. The template must render a clean generic message
    instead of leaking blanks like "with  is a valid ".
    """

    @staticmethod
    def _block_text(message: DocParseErrorMessage) -> str:
        return message.blocks[0]["text"]["text"]

    def test_renders_detailed_message_when_both_fields_present(self):
        message = DocParseErrorMessage(".xlf", "xliff")
        text = self._block_text(message)
        assert "with .xlf" in text
        assert "is a valid xliff" in text

    def test_falls_back_to_generic_when_ext_missing(self):
        message = DocParseErrorMessage("", "xliff")
        text = self._block_text(message)
        assert text == (
            "Error parsing file. Please ensure your file is in a supported format."
        )

    def test_falls_back_to_generic_when_file_type_missing(self):
        message = DocParseErrorMessage(".xlf", "")
        text = self._block_text(message)
        assert text == (
            "Error parsing file. Please ensure your file is in a supported format."
        )

    def test_falls_back_to_generic_when_both_missing(self):
        message = DocParseErrorMessage("", "")
        text = self._block_text(message)
        assert text == (
            "Error parsing file. Please ensure your file is in a supported format."
        )
        assert "{ext}" not in text
        assert "{file_type}" not in text
        assert "with  is a valid" not in text

    def test_falls_back_to_generic_when_fields_are_whitespace(self):
        """Whitespace-only values are treated as missing — they would
        render as awkward gaps in the detailed template."""
        message = DocParseErrorMessage("   ", "\t")
        text = self._block_text(message)
        assert text == (
            "Error parsing file. Please ensure your file is in a supported format."
        )


class TestDocInvalidPdfErrorMessage:
    """Tests for invalid-PDF messages using exportable local templates."""

    @staticmethod
    def _block_text(message: DocInvalidPdfErrorMessage) -> str:
        return message.blocks[0]["text"]["text"]

    def test_renders_payload_message_as_placeholder_detail(self):
        message = DocInvalidPdfErrorMessage("Unable to read PDF metadata")

        assert self._block_text(message) == (
            "Invalid PDF file: Unable to read PDF metadata"
        )

    def test_falls_back_to_exportable_generic_message(self):
        message = DocInvalidPdfErrorMessage("")

        assert self._block_text(message) == (
            "Invalid PDF file. Please check the file and try again."
        )


class TestEvaluateSuccessMessage:
    """Tests for EvaluateSuccessMessage class."""

    def test_evaluate_success_message_ibm(self):
        """Test evaluate success message for IBM enterprise."""
        from unittest.mock import patch

        job = {
            "uuid": "job-123",
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "filename": "test.txt",
                    "target_files": [],
                    "report": {"language_uuid": "source-uuid"},
                }
            ],
        }
        with patch("app.slack.templates.blocks.get_languages_sync", return_value=[]):
            message = EvaluateSuccessMessage(job, is_ibm_enterprise=True, tokens=None)
            assert (
                "evaluated" in message.text.lower()
                or "evaluation" in message.text.lower()
            )
            assert len(message.blocks) > 0

    def test_evaluate_success_message_with_tokens(self):
        """Test evaluate success message with tokens."""
        from unittest.mock import patch

        job = {
            "uuid": "job-123",
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "filename": "test.txt",
                    "target_files": [],
                    "report": {"language_uuid": "source-uuid"},
                }
            ],
        }
        with patch("app.slack.templates.blocks.get_languages_sync", return_value=[]):
            message = EvaluateSuccessMessage(job, is_ibm_enterprise=False, tokens=100)
            assert (
                "evaluated" in message.text.lower()
                or "evaluation" in message.text.lower()
            )
            # Check that tokens are mentioned in the blocks (not in text title)
            assert any(
                "100" in str(block) or "tokens" in str(block).lower()
                for block in message.blocks
            )
            assert len(message.blocks) > 0

    def test_evaluate_success_message_without_actions(self):
        """Test evaluate success message without actions."""
        from unittest.mock import patch

        job = {
            "uuid": "job-123",
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "filename": "test.txt",
                    "target_files": [],
                    "report": {"language_uuid": "source-uuid"},
                }
            ],
        }
        with patch("app.slack.templates.blocks.get_languages_sync", return_value=[]):
            message = EvaluateSuccessMessage(
                job, is_ibm_enterprise=False, tokens=None, actions=False
            )
            assert (
                "evaluated" in message.text.lower()
                or "evaluation" in message.text.lower()
            )
            assert len(message.blocks) > 0
            # Should not have action buttons when actions=False
            assert not any(block.get("type") == "actions" for block in message.blocks)


class TestMachineTranslationMessage:
    def test_short_text_no_splitting(self):
        """Test that short translations don't get split into multiple blocks."""
        source_text = "Hello world"
        mt_text = "Hola mundo"
        message = MachineTranslationMessage("es", "en", source_text, mt_text)

        # Should have 2 blocks: label block + translation block
        assert len(message.blocks) == 2
        assert message.blocks[0]["type"] == "section"
        assert "*Machine translation result:*" in message.blocks[0]["text"]["text"]
        assert message.blocks[1]["type"] == "section"
        # Closing bold applies only around translated text; language suffix is outside
        assert message.blocks[1]["text"]["text"] == "*Hola mundo (en-es)"

    def test_long_text_splitting(self):
        """Test that long translations get split into multiple blocks."""
        # Create text that exceeds 3000 characters - use lines to ensure proper splitting
        long_text = "A" * 1500 + "\n" + "B" * 1500 + "\n" + "C" * 1000
        source_text = "Source text"
        message = MachineTranslationMessage("es", "en", source_text, long_text)

        # Should have at least 3 blocks: label block + multiple translation blocks
        assert len(message.blocks) >= 3
        assert message.blocks[0]["type"] == "section"
        assert "*Machine translation result:*" in message.blocks[0]["text"]["text"]

        # First translation block should have language label
        assert message.blocks[1]["type"] == "section"
        assert "(en-es)" in message.blocks[1]["text"]["text"]

        # Verify no block exceeds 3000 characters
        for block in message.blocks[1:]:
            text_length = len(block["text"]["text"])
            assert text_length <= 3000, f"Block text length {text_length} exceeds 3000"

        # Verify all text is present (accounting for markdown formatting)
        combined_text = "".join(
            block["text"]["text"].replace("*", "").replace("(en-es)", "")
            for block in message.blocks[1:]
        )
        # Check that key parts of the original text are present
        assert "A" * 1500 in combined_text or "A" * 1000 in combined_text

    def test_language_label_on_first_chunk_only(self):
        """Test that language label appears only on the first chunk when splitting."""
        # Create text that will definitely be split
        long_text = "Line 1\n" * 500  # Multiple lines to ensure splitting
        source_text = "Source text"
        message = MachineTranslationMessage("es", "en", source_text, long_text)

        # First translation block should have language label
        first_translation_block = message.blocks[1]
        assert "(en-es)" in first_translation_block["text"]["text"]

        # Subsequent blocks should not have language label
        for block in message.blocks[2:]:
            assert "(en-es)" not in block["text"]["text"]


class TestAutoTranslationMessage:
    def test_short_translation_no_splitting(self):
        """Test that short translations don't get split."""
        source_text = "Hello world"
        translations = {"es": ["Hola mundo"]}
        message = AutoTranslationMessage(source_text, "en", translations)

        # Should have translation blocks + context block
        assert len(message.blocks) >= 2
        # Find the translation block (not the context block)
        translation_blocks = [
            b
            for b in message.blocks
            if b.get("type") == "section" and ">" in b["text"]["text"]
        ]
        assert len(translation_blocks) == 1
        assert "Hola mundo" in translation_blocks[0]["text"]["text"]

    def test_long_translation_splitting(self):
        """Test that long translations get split into multiple blocks."""
        # Create text that exceeds 3000 characters - use lines to ensure proper splitting
        long_translation = "A" * 1500 + "\n" + "B" * 1500 + "\n" + "C" * 1000
        source_text = "Source text"
        translations = {"es": [long_translation]}
        message = AutoTranslationMessage(source_text, "en", translations)

        # Should have multiple translation blocks + context block
        translation_blocks = [
            b
            for b in message.blocks
            if b.get("type") == "section" and ">" in b["text"]["text"]
        ]
        assert len(translation_blocks) >= 2

        # Verify no block exceeds 3000 characters
        for block in translation_blocks:
            text_length = len(block["text"]["text"])
            assert text_length <= 3000, f"Block text length {text_length} exceeds 3000"

    def test_multiple_languages(self):
        """Test that multiple language translations are handled correctly."""
        source_text = "Hello"
        translations = {
            "es": ["Hola"],
            "fr": ["Bonjour"],
            "de": ["Hallo"],
        }
        message = AutoTranslationMessage(source_text, "en", translations)

        # Should have blocks for each translation + context block
        translation_blocks = [
            b
            for b in message.blocks
            if b.get("type") == "section" and ">" in b["text"]["text"]
        ]
        assert len(translation_blocks) >= 3

        # Verify context block mentions all languages
        context_block = [b for b in message.blocks if b.get("type") == "context"][0]
        assert "Translated to" in context_block["elements"][0]["text"]


class TestSrtTranslateMessage:
    def test_message_structure(self):
        """Test that SrtTranslateMessage creates correct block structure."""
        task_uuid = "test-uuid-123"
        message = SrtTranslateMessage(task_uuid)

        # Input block (multi language select) then actions (submit)
        assert len(message.blocks) == 2

        assert message.blocks[0]["type"] == "input"
        assert message.blocks[0]["block_id"] == task_uuid
        assert message.blocks[0]["element"]["type"] == "multi_static_select"
        assert message.blocks[0]["element"]["action_id"] == "language_mt_options"

        # Second block should be actions block with submit button
        assert message.blocks[1]["type"] == "actions"
        assert len(message.blocks[1]["elements"]) == 1
        assert message.blocks[1]["elements"][0]["type"] == "button"
        assert message.blocks[1]["elements"][0]["action_id"] == "srt_translate"
        assert message.blocks[1]["elements"][0]["value"] == task_uuid
        assert message.blocks[1]["elements"][0]["style"] == "primary"


class TestMediaEmbedOptionMessage:
    def test_message_structure(self):
        """Test that MediaEmbedOptionMessage creates the embed CTA block."""
        action_value = '{"files":[{"file_id":"F123","file_name":"video.mp4"}]}'
        message = MediaEmbedOptionMessage(action_value)

        assert message.text == "Media embed option"
        assert len(message.blocks) == 1
        assert message.blocks[0]["type"] == "section"
        assert message.blocks[0]["accessory"]["type"] == "button"
        assert message.blocks[0]["accessory"]["action_id"] == "video_embed_subtitles"
        assert message.blocks[0]["accessory"]["value"] == action_value


class TestBatchAndFileListMessages:
    def test_batch_list_message_shows_empty_state_when_no_batches(self):
        job = SimpleNamespace(
            id="TJ123456",
            status="IN_PROGRESS",
            batches="[]",
            pagination=_pagination(),
        )

        message = BatchListMessage(job, "client-id")

        assert _blocks_contain_text(
            message.blocks,
            "No in-progress files are available for *TJ123456* right now.",
        )
        assert not _blocks_contain_text(message.blocks, "Show more files")

    def test_file_list_message_shows_empty_state_when_no_completed_files(self):
        job = SimpleNamespace(
            id="TJ123456",
            translated_file=[],
            sl=SimpleNamespace(name="EN-US"),
            f_pagination=_pagination(),
            pagination=_pagination(),
        )

        message = FileListMessage(job, "client-id")

        assert _blocks_contain_text(
            message.blocks,
            "No completed files are available for *TJ123456* right now.",
        )
        assert not _blocks_contain_text(message.blocks, "Download")


class TestInsightsRemoval:
    """Guard tests: verify Insights symbols were fully removed (RAY-79162)."""

    def test_insights_message_not_importable(self):
        """InsightsMessage should no longer exist in the messages module."""
        import app.slack.templates.messages as msg_mod

        assert not hasattr(msg_mod, "InsightsMessage")

    def test_report_insights_message_not_importable(self):
        """ReportInsightsMessage should no longer exist in the messages module."""
        import app.slack.templates.messages as msg_mod

        assert not hasattr(msg_mod, "ReportInsightsMessage")

    def test_login_message_no_insights_variation(self):
        """LoginMessage.INSIGHTS constant should no longer exist."""
        assert not hasattr(LoginMessage, "INSIGHTS")

    def test_is_min_languagecloud_plan_not_in_utils(self):
        """is_min_langugagecloud_plan should no longer exist in utils."""
        import app.ray.utils as utils_mod

        assert not hasattr(utils_mod, "is_min_langugagecloud_plan")
