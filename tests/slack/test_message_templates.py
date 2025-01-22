from app.auth.connector import RayClient
from app.slack.templates.messages import LoginMessage
from app.slack.templates.messages import VerifyCompleteMessage
from app.translate import _


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

        localized_lang_label = _(lang_label)

        # Create a VerifyCompleteMessage instance
        message = VerifyCompleteMessage(job_title, lang_label)

        # Assert the message blocks are formatted correctly
        assert len(message.blocks) == 1  # Should contain one block
        assert message.blocks[0]["type"] == "section"  # Block type
        assert message.blocks[0]["text"]["type"] == "mrkdwn"  # Text type

        # Check that the text includes the correct job title and language
        expected_text = (
            f"Quality Evaluation Job '{job_title}' human verification complete. "
            f"The file has been verified for language {localized_lang_label}."
        )
        assert message.blocks[0]["text"]["text"] == expected_text

        # Assert the message's title is correct
        # Since the 'title' is part of the first block, we should check for the title text there.
        assert message.blocks[0]["text"]["text"].startswith("Quality Evaluation Job")
