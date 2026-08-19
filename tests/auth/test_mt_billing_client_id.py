from unittest.mock import patch

from app.auth.connector import (
    IbmWorkspaceMtWalletEmpty,
    RayConnection,
    RaySuperGroup,
    mt_billing_client_id,
    mt_bills_workspace_org,
    suppress_ibm_mt_token_prompt,
)


def _super_group() -> RaySuperGroup:
    return RaySuperGroup(
        id="sg-1",
        name="IBM Slack App",
        slack_team_id="T1",
        verify_organization_uuid="workspace-org",
        slack_enterprise_id="E27SFGS2W",
    )


def test_workspace_org_bills_org_even_with_crm_member(ray_client):
    ray = RayConnection(super_group=[_super_group()], client=ray_client)
    assert mt_billing_client_id(ray) == "workspace-org"
    assert mt_bills_workspace_org(ray) is True


def test_member_jwt_only_when_workspace_has_no_org(ray_client):
    ray = RayConnection(super_group=[], client=ray_client)
    assert mt_billing_client_id(ray) == ray_client.id
    assert mt_bills_workspace_org(ray) is False


def test_org_bills_when_member_is_missing():
    ray = RayConnection(super_group=[_super_group()], client=None)
    assert mt_billing_client_id(ray) == "workspace-org"
    assert mt_bills_workspace_org(ray) is True


@patch("app.ray.utils.is_ibm_customer_enterprise", return_value=True)
def test_suppress_ibm_mt_token_prompt_alerts_google_chat(mock_ibm):
    with patch("app.auth.connector.notify_exception") as mock_notify:
        assert (
            suppress_ibm_mt_token_prompt(
                "E27SFGS2W",
                organization_uuid="workspace-org",
                balance=0,
                required=12,
            )
            is True
        )

    mock_ibm.assert_called_once_with("E27SFGS2W")
    mock_notify.assert_called_once()
    exc = mock_notify.call_args.args[0]
    assert isinstance(exc, IbmWorkspaceMtWalletEmpty)
    assert exc.organization_uuid == "workspace-org"
    assert exc.balance == 0
    assert exc.required == 12


@patch("app.ray.utils.is_ibm_customer_enterprise", return_value=False)
def test_suppress_ibm_mt_token_prompt_skips_non_ibm(mock_ibm):
    with patch("app.auth.connector.notify_exception") as mock_notify:
        assert (
            suppress_ibm_mt_token_prompt(
                "E_OTHER",
                organization_uuid="workspace-org",
                balance=0,
                required=12,
            )
            is False
        )

    mock_notify.assert_not_called()
