from app.auth.connector import RayClient, RayConnection, RaySuperGroup
from app.slack.templates.messages import (
    AutoTranslateSettingsChangedMessage,
    AutoTranslateSettingsDisabledMessage,
    AutoTranslationMessage,
    CancelJobMessage,
    ClientAlreadyApprovedMessage,
    ClientApprovedMessage,
    ConnectionInfoMessage,
    DocMtMessage,
    EvaluateErrorMessage,
    EvaluateSuccessMessage,
    HelpMessage,
    InfoMessage,
    InvalidCommandMessage,
    InvalidJobMessage,
    InvalidMTResultMessage,
    JobStatusNoIdMessage,
    JobTargetsNoIdMessage,
    LoginMessage,
    LogoutMessage,
    MachineTranslationMessage,
    MediaEmbedOptionMessage,
    OnboardingMessage,
    RequiresMtTokenMessage,
    SlackPermissionsMessage,
    SrtTranslateMessage,
    SuccessfulLoginMessage,
    SuccessfulLogoutMessage,
    TranscriptionMessage,
    VerifyCompleteMessage,
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

    def test_welcome_back_message_without_connection(self, user_id):
        """Test welcome back message without ray connection."""
        message = WelcomeBackMessage(user_id, None)

        assert "Welcome" in message.text
        assert len(message.blocks) > 0
        assert not _blocks_contain_action(message.blocks, "report_insights")


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
        assert isinstance(message.text, str)


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
