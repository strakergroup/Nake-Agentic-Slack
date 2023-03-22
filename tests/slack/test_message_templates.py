from app.auth.connector import RayClient
from app.slack.templates.messages import LoginMessage


class TestLoginMessage:
    def test_normal_variation(
        self, user_id: str, team_id: str, app_id: str, channel_id: str
    ):
        message = LoginMessage(user_id, team_id, app_id, channel_id)
        assert message.text == "Connect your RAY Cloud account"
        assert (
            "Click this button to connect your RAY Cloud account"
            in message.blocks[0]["text"]["text"]
        )

    def test_connected_variation(
        self,
        user_id: str,
        team_id: str,
        app_id: str,
        channel_id: str,
        ray_client: RayClient,
    ):
        message = LoginMessage(
            user_id, team_id, app_id, channel_id, ray_client=ray_client
        )
        assert message.text == "Connect your RAY Cloud account"
        assert (
            "Your connected RAY Cloud account is" in message.blocks[0]["text"]["text"]
        )
        assert ray_client.username in message.blocks[0]["text"]["text"]
        assert (
            "You can connect a different account by clicking this button"
            in message.blocks[0]["text"]["text"]
        )

    def test_get_job_variation(
        self, user_id: str, team_id: str, app_id: str, channel_id: str
    ):
        message = LoginMessage(
            user_id, team_id, app_id, channel_id, variation=LoginMessage.GET_JOB
        )
        assert message.text == "Connect your RAY Cloud account"
        assert (
            "Connect your RAY Cloud account to view your jobs."
            in message.blocks[0]["text"]["text"]
        )

    def test_new_job_variation(
        self, user_id: str, team_id: str, app_id: str, channel_id: str
    ):
        message = LoginMessage(
            user_id, team_id, app_id, channel_id, variation=LoginMessage.NEW_JOB
        )
        assert message.text == "Connect your RAY Cloud account"
        assert (
            "Connect your RAY Cloud account to submit a new translation job"
            in message.blocks[0]["text"]["text"]
        )

    def test_variation_overrides_connected_variation(
        self,
        user_id: str,
        team_id: str,
        app_id: str,
        channel_id: str,
        ray_client: RayClient,
    ):
        """The `ray_client` argument should be ignored if the `variation` argument
        is a valid variation.
        """
        message = LoginMessage(
            user_id,
            team_id,
            app_id,
            channel_id,
            ray_client=ray_client,
            variation=LoginMessage.GET_JOB,
        )
        assert message.text == "Connect your RAY Cloud account"
        assert (
            "Connect your RAY Cloud account to view your jobs."
            in message.blocks[0]["text"]["text"]
        )
        assert ray_client.username not in message.blocks[0]["text"]["text"]
        assert (
            "You can connect a different account by clicking this button"
            not in message.blocks[0]["text"]["text"]
        )
