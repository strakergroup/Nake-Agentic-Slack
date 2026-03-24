"""Generate Block Kit JSON for all Slack UI templates with mock data.

Instantiates every message class and view function from the app's
template modules, using realistic mock objects, and writes the
collected Block Kit payloads to ``output/blocks.json``.

Usage:
    python -m tools.ui_export.generate_blocks   (from repo root)
    # or
    cd tools/ui-export && python generate_blocks.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Ensure the repo root is on sys.path so app.* imports resolve
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Patch heavy/side-effecty modules *before* importing app code
# ---------------------------------------------------------------------------

# Patch database engines (they try to connect on import)
sys.modules.setdefault("app.database", MagicMock())

# Patch redis (it tries to connect on import)
_redis_mock = MagicMock()
_redis_mock.redis_conn = AsyncMock()
sys.modules.setdefault("app.redis", _redis_mock)

# Patch buglog notifier
sys.modules.setdefault("app.slack.buglog_notifier", MagicMock())

# Comprehensive mocking of third-party packages that aren't pip-installed
# or try to connect to external services on import.
_straker_auth = MagicMock()
for sub in (
    "straker_auth",
    "straker_auth.languagecloud",
    "straker_auth.languagecloud.jwt",
):
    sys.modules.setdefault(sub, _straker_auth)

_ray_logger = MagicMock()
for sub in ("ray_logger", "ray_logger.slack"):
    sys.modules.setdefault(sub, _ray_logger)

_straker_utils = MagicMock()
for sub in (
    "straker_utils",
    "straker_utils.sql",
    "straker_utils.sql.async_engine",
    "straker_utils.domain",
    "straker_utils.environment",
):
    sys.modules.setdefault(sub, _straker_utils)


class _Environment(str, Enum):
    production = "production"
    uat = "uat"
    local = "local"


sys.modules["straker_utils.environment"].Environment = _Environment

# Patch the domains object
_domains_mock = MagicMock()
_domains_mock.verify = "https://verify.example.com"
_domains_mock.languagecloud = "https://languagecloud.example.com"
_domains_mock.api = "https://api.example.com"

# Patch config module
_config_mock = MagicMock()
_config_mock.environment = "uat"
_config_mock.config = _config_mock
_config_mock.domains = _domains_mock
_config_mock.Environment = _Environment
sys.modules["app.config"] = _config_mock

# Patch ray_sdk
_ray_sdk_mock = MagicMock()
sys.modules.setdefault("ray_sdk", _ray_sdk_mock)
sys.modules.setdefault("ray_sdk.api", _ray_sdk_mock)
sys.modules.setdefault("ray_sdk.api.v3", _ray_sdk_mock)
sys.modules.setdefault("ray_sdk.api.v3.models", _ray_sdk_mock)
sys.modules.setdefault("ray_sdk.api.v3.file", MagicMock())

# Make is_valid_file_ext always return True
sys.modules["ray_sdk.api.v3.file"].is_valid_file_ext = lambda _: True

# Patch translation function to act as identity with variable interpolation


def _mock_translate(text, *args, **kwargs):
    """Identity translation that resolves f-string style variables."""
    import inspect

    frame = inspect.currentframe()
    caller_locals = {}
    if frame and frame.f_back:
        caller_locals = frame.f_back.f_locals
        # Check one more level up for class __init__ methods
        if frame.f_back.f_back:
            caller_locals = {**frame.f_back.f_back.f_locals, **caller_locals}
    try:
        return text.format(**caller_locals)
    except (KeyError, IndexError, AttributeError):
        return text


sys.modules.setdefault("app.translate", MagicMock())
sys.modules["app.translate"]._ = _mock_translate

# Don't mock sqlalchemy — the real package is needed for imports

# Mock watson module (tries to connect to IBM Watson on import)
sys.modules.setdefault("app.watson", MagicMock())
sys.modules.setdefault("app.watson.assistant", MagicMock())

# Mock API modules that try to connect on import
sys.modules.setdefault("app.api.language_cloud", MagicMock())
sys.modules.setdefault("app.api.verify", MagicMock())
sys.modules.setdefault("app.api.http_client", MagicMock())
sys.modules.setdefault("app.api.stream_proxy", MagicMock())
sys.modules.setdefault("app.api.verifyloop", MagicMock())

# Mock the slack listener modules (they import lots of heavy deps)
sys.modules.setdefault("app.slack.listeners", MagicMock())
sys.modules.setdefault("app.slack.listener_actions", MagicMock())
sys.modules.setdefault("app.slack.app", MagicMock())
sys.modules.setdefault("app.slack.web", MagicMock())

# Pre-import app modules to ensure submodules are registered before patching.
# This allows unittest.mock.patch to resolve dotted paths like
# "app.ray.settings.get_auto_translate_language_name".
from app.auth.connector import (  # noqa: E402
    RayClient,
    RayConnection,
    RaySuperGroup,
)
from app.ray.events.models import (  # noqa: E402
    ClientGroup,
    ClientSignupEvent,
    JobQuoteAcceptedEvent,
    JobQuoteCreatedEvent,
    Language,
    QuoteInfo,
    QuoteLangPrice,
)

# ---------------------------------------------------------------------------
# Mock Data Factories
# ---------------------------------------------------------------------------

USER_ID = "U0123456789"
TEAM_ID = "T0123456789"
ENTERPRISE_ID = "E0123456789"
CHANNEL_ID = "C0123456789"
CLIENT_UUID = "550e8400-e29b-41d4-a716-446655440000"
GROUP_UUID = "660e8400-e29b-41d4-a716-446655440001"
JOB_UUID = "770e8400-e29b-41d4-a716-446655440002"


def make_ray_client(sso: bool = False) -> RayClient:
    return RayClient(
        id=CLIENT_UUID,
        username="jane.doe@acme.com",
        user_group_id=GROUP_UUID,
        access_token="mock-access-token",
        slack_user_id=USER_ID,
        slack_team_id=TEAM_ID,
        slack_enterprise_id=ENTERPRISE_ID,
        slack_access_token=None,
        settings_id=1,
        id_token="mock-id-token",
        planname="Growth",
        sso=sso,
    )


def make_super_group(enable_verify: bool = True) -> RaySuperGroup:
    return RaySuperGroup(
        id=GROUP_UUID,
        name="Acme Translations",
        slack_team_id=TEAM_ID,
        verify_organization_uuid="org-uuid-123",
        slack_enterprise_id=ENTERPRISE_ID,
        enable_verify_in_slack=enable_verify,
    )


def make_ray_connection(
    with_client: bool = True, enable_verify: bool = True
) -> RayConnection:
    client = make_ray_client() if with_client else None
    return RayConnection(
        super_group=[make_super_group(enable_verify)],
        client=client,
    )


def make_job_mock() -> MagicMock:
    """Create a mock Job object matching ray_sdk.api.v3.models.Job."""
    job = MagicMock()
    job.id = "TJ123456"
    job.uuid = JOB_UUID
    job.reference = "Website Localisation Q1"
    job.status = "IN_PROGRESS"

    sl = MagicMock()
    sl.name = "English"
    sl.code = "en"
    sl.shortname = "EN"
    job.sl = sl

    tl1 = MagicMock()
    tl1.name = "French"
    tl1.code = "fr"
    tl1.shortname = "FR"
    tl2 = MagicMock()
    tl2.name = "Spanish"
    tl2.code = "es"
    tl2.shortname = "ES"
    tl3 = MagicMock()
    tl3.name = "German"
    tl3.code = "de"
    tl3.shortname = "DE"
    job.tl = [tl1, tl2, tl3]

    job.target_date = (datetime.now() + timedelta(days=7)).isoformat()
    job.created_date = datetime.now().isoformat()
    job.word_count = 12500

    pm = MagicMock()
    pm.first_name = "Alice"
    pm.last_name = "Smith"
    job.project_manager = pm

    job.translated_file = [
        {
            "file_name": "homepage.html",
            "lang": "French",
            "download_url": "#mock-download",
        },
        {
            "file_name": "homepage.html",
            "lang": "Spanish",
            "download_url": "#mock-download",
        },
    ]
    job.batches = json.dumps(
        [
            {
                "batch_label": "Batch 1",
                "source_lang": "en",
                "target_lang": "fr",
                "batch_status": "IN_PROGRESS",
                "generated_file": "",
            }
        ]
    )

    pagination = MagicMock()
    pagination.page = 1
    pagination.total_pages = 3
    pagination.rows_per_page = 5
    pagination.total = 12
    job.pagination = pagination

    f_pagination = MagicMock()
    f_pagination.page = 1
    f_pagination.total_pages = 2
    f_pagination.rows_per_page = 5
    f_pagination.total = 8
    job.f_pagination = f_pagination

    return job


def make_quote_mock() -> MagicMock:
    """Create a mock Quote matching ray_sdk.api.v3.models.Quote."""
    quote = MagicMock()
    quote.uuid = JOB_UUID
    quote.id = "TJ123456"
    quote.client_id = CLIENT_UUID
    quote.client_reference = "Website Localisation Q1"
    quote.status = "PENDING_QUOTES"
    quote.service = "Translation"
    quote.turnaround_days = 5.0

    sl = MagicMock()
    sl.name = "English"
    sl.code = "en"
    sl.label = "English"
    quote.sl = sl

    tl1 = MagicMock()
    tl1.name = "French"
    tl1.code = "fr"
    tl1.label = "French"
    tl2 = MagicMock()
    tl2.name = "Spanish"
    tl2.code = "es"
    tl2.label = "Spanish"
    quote.tl = [tl1, tl2]

    q = MagicMock()
    q.currency = "USD"
    q.quote = 1250.00
    q.quote_nett = 1100.00
    q.quote_detail_url = "#mock-detail"
    q.quote_accept_url = "#mock-accept"
    q.quote_cancel_url = "#mock-cancel"
    q.tl = {
        "fr": MagicMock(price=650.00),
        "es": MagicMock(price=600.00),
    }
    quote.quote = q

    return quote


def make_job_quote_created_event() -> JobQuoteCreatedEvent:
    return JobQuoteCreatedEvent(
        uuid=JOB_UUID,
        id="TJ123456",
        client_reference="Website Localisation Q1",
        client_id=CLIENT_UUID,
        status="PENDING_QUOTES",
        sl=Language(code="en", label="English"),
        tl=[Language(code="fr", label="French"), Language(code="es", label="Spanish")],
        service="Translation",
        turnaround_days=5.0,
        quote=QuoteInfo(
            currency="USD",
            quote=1250.00,
            quote_nett=1100.00,
            quote_detail_url="#mock-detail",
            quote_accept_url="#mock-accept",
            quote_cancel_url="#mock-cancel",
            tl={"fr": QuoteLangPrice(price=650.00), "es": QuoteLangPrice(price=600.00)},
        ),
    )


def make_job_quote_accepted_event() -> JobQuoteAcceptedEvent:
    return JobQuoteAcceptedEvent(
        uuid=JOB_UUID,
        target_date=datetime.now() + timedelta(days=7),
        id="TJ123456",
        client_id=CLIENT_UUID,
    )


def make_client_signup_event() -> ClientSignupEvent:
    return ClientSignupEvent(
        client_id=CLIENT_UUID,
        username="john.smith@acme.com",
        email="john.smith@acme.com",
        first_name="John",
        last_name="Smith",
        groups=[
            ClientGroup(uuid="g1-uuid", label="Marketing"),
            ClientGroup(uuid="g2-uuid", label="Engineering"),
        ],
    )


def make_evaluate_job() -> dict[str, Any]:
    return {
        "uuid": JOB_UUID,
        "workflow_uuid": "wf-uuid-123",
        "target_languages": [
            {"uuid": "lang-fr-uuid", "name": "French"},
            {"uuid": "lang-es-uuid", "name": "Spanish"},
        ],
        "source_files": [
            {
                "file_uuid": "file-uuid-001",
                "filename": "marketing-copy.docx",
                "target_files": [
                    {
                        "language_uuid": "lang-fr-uuid",
                        "target_file_uuid": "tf-001",
                        "human_job_status": "",
                    },
                    {
                        "language_uuid": "lang-es-uuid",
                        "target_file_uuid": "tf-002",
                        "human_job_status": "",
                    },
                ],
                "report": {
                    "language_uuid": "lang-en-uuid",
                    "evaluation_reports": [
                        {
                            "target_language": "lang-fr-uuid",
                            "count": {
                                "bad": 2,
                                "good": 45,
                                "best": 30,
                                "acceptable": 18,
                                "translation_memory": 5,
                            },
                            "score": 82,
                        },
                        {
                            "target_language": "lang-es-uuid",
                            "count": {
                                "bad": 1,
                                "good": 50,
                                "best": 25,
                                "acceptable": 20,
                                "translation_memory": 4,
                            },
                            "score": 87,
                        },
                    ],
                },
            }
        ],
    }


def make_costs() -> list[dict[str, Any]]:
    return [
        {
            "file_uuid": "file-uuid-001",
            "language_uuid": "lang-fr-uuid",
            "service_list": [{"estimated_cost": 45.75, "time_estimate_days": 3}],
        },
        {
            "file_uuid": "file-uuid-001",
            "language_uuid": "lang-es-uuid",
            "service_list": [{"estimated_cost": 38.50, "time_estimate_days": 2}],
        },
    ]


def make_cancel_job_detail() -> dict[str, Any]:
    sourcelang = MagicMock()
    sourcelang.label = "English"
    tl1 = MagicMock()
    tl1.label = "French"
    tl2 = MagicMock()
    tl2.label = "Spanish"
    return {
        "job_id": "TJ123456",
        "status": "IN_PROGRESS",
        "sourcelang": sourcelang,
        "targetlang": [tl1, tl2],
    }


def make_fact_check_data() -> dict[str, Any]:
    return {
        "file_name": "product-claims.pdf",
        "job_uuid": "fc-job-uuid-001",
        "fact_check_result": {
            "total_claims": 14,
            "claims": [{"claim": "Our product is #1", "verdict": "Needs Review"}],
        },
    }


FILE_INFO = [
    {"id": "F001", "title": "homepage.html", "initial": True},
    {"id": "F002", "title": "about-us.docx"},
    {"id": "F003", "title": "pricing.xlsx"},
]

MOCK_LANGUAGE_OPTIONS = [
    {"text": {"type": "plain_text", "text": "French"}, "value": "fr"},
    {"text": {"type": "plain_text", "text": "Spanish"}, "value": "es"},
    {"text": {"type": "plain_text", "text": "German"}, "value": "de"},
    {"text": {"type": "plain_text", "text": "Japanese"}, "value": "ja"},
    {"text": {"type": "plain_text", "text": "Chinese (Simplified)"}, "value": "zh-CN"},
    {"text": {"type": "plain_text", "text": "Portuguese (Brazil)"}, "value": "pt-BR"},
]

MOCK_DISPLAY_FORMAT_OPTIONS = [
    {"text": {"type": "plain_text", "text": "Thread replies"}, "value": "thread"},
    {"text": {"type": "plain_text", "text": "Direct messages"}, "value": "message"},
]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def serialize_blocks(blocks: list) -> list[dict[str, Any]]:
    """Recursively convert SDK block objects to plain dicts."""
    result = []
    for block in blocks:
        if hasattr(block, "to_dict"):
            result.append(block.to_dict())
        elif isinstance(block, dict):
            result.append(block)
        else:
            result.append(
                {"type": "section", "text": {"type": "plain_text", "text": str(block)}}
            )
    return result


def safe_extract(obj: Any) -> dict[str, Any] | None:
    """Extract blocks from a message/view object safely."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        blocks = obj.get("blocks", [])
        return {
            "type": obj.get("type", "message"),
            "title": _extract_text(obj.get("title")),
            "blocks": serialize_blocks(blocks),
        }
    if hasattr(obj, "blocks"):
        return {
            "type": "message",
            "blocks": serialize_blocks(obj.blocks),
        }
    if hasattr(obj, "text"):
        return {
            "type": "text_only",
            "text": obj.text,
            "blocks": [],
        }
    return None


def _extract_text(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return obj.get("text", "")
    return str(obj)


# ---------------------------------------------------------------------------
# Build all templates
# ---------------------------------------------------------------------------


def build_all_messages() -> list[dict[str, Any]]:
    """Instantiate all message classes and return their block payloads."""
    entries: list[dict[str, Any]] = []

    def add(name: str, category: str, obj: Any):
        data = safe_extract(obj)
        if data:
            entries.append({"name": name, "category": category, **data})

    ray_client = make_ray_client()
    ray_client_sso = make_ray_client(sso=True)
    ray_connection = make_ray_connection()
    ray_connection_verify = make_ray_connection(enable_verify=True)
    job = make_job_mock()
    quote = make_quote_mock()

    # Patch functions where they are *imported* (in the template modules),
    # not where they are defined.
    MSG = "app.slack.templates.messages"
    BLK = "app.slack.templates.blocks"
    with (
        patch(
            f"{MSG}.get_language_cloud_connect_url", return_value="#mock-connect-url"
        ),
        patch(f"{MSG}.get_job_url", return_value="#mock-job-url"),
        patch(f"{MSG}.format_job_due_date_slack", return_value="Feb 28, 2026"),
        patch(f"{MSG}.format_datetime_slack", return_value="Feb 28, 2026 at 3:00 PM"),
        patch(
            f"{MSG}.format_job_status", return_value=":large_blue_circle: In Progress"
        ),
        patch(f"{MSG}.is_ibm_enterprise", return_value=False),
        patch(f"{MSG}.is_min_langugagecloud_plan", return_value=True),
        patch(
            f"{MSG}.get_auto_translate_language_options",
            return_value=MOCK_LANGUAGE_OPTIONS,
        ),
        patch(
            f"{MSG}.get_auto_translate_language_name",
            side_effect=lambda code: {
                "en": "English",
                "fr": "French",
                "es": "Spanish",
                "de": "German",
                "ja": "Japanese",
            }.get(code, code),
        ),
        patch(f"{MSG}.config", _config_mock),
        patch(f"{MSG}.domains", _domains_mock),
        patch(
            f"{BLK}.get_language_cloud_connect_url", return_value="#mock-connect-url"
        ),
        patch(f"{BLK}.get_job_url", return_value="#mock-job-url"),
        patch(f"{BLK}.is_ibm_enterprise", return_value=False),
        patch(f"{BLK}.format_currency", side_effect=lambda v, c: f"${v:,.2f}"),
        patch(f"{BLK}.format_currency_symbol", return_value="USD"),
        patch(f"{BLK}.domains", _domains_mock),
        patch(
            f"{BLK}.get_languages_sync",
            return_value=[
                {"uuid": "lang-en-uuid", "name": "English"},
                {"uuid": "lang-fr-uuid", "name": "French"},
                {"uuid": "lang-es-uuid", "name": "Spanish"},
            ],
        ),
    ):
        from app.slack.templates.messages import (
            AIHelperMessage,
            AutoTranslateSettingsChangedMessage,
            AutoTranslateSettingsDisabledMessage,
            AutoTranslationMessage,
            BatchListMessage,
            CancelJobMessage,
            CancelTJMessage,
            ClientAlreadyApprovedMessage,
            ClientApprovedEventMessage,
            ClientApprovedMessage,
            ClientSignupEventAdminMessage,
            ClientSignupEventMessage,
            ConnectionInfoMessage,
            DocComplexityErrorMessage,
            DocInvalidPdfErrorMessage,
            DocMtMessage,
            DocParseErrorMessage,
            DocumentMTJobMessage,
            EvaluateErrorMessage,
            EvaluateSuccessMessage,
            FactCheckResultMessage,
            FileListMessage,
            FileTooLargeMessage,
            FileTranslatedMessage,
            HelpMessage,
            HumanJobMessage,
            HumanJobQuoteMessage,
            ImageToMarkdownMessage,
            InfoMessage,
            InsightsMessage,
            InvalidCommandMessage,
            InvalidJobMessage,
            InvalidMTResultMessage,
            JobCancelledEventMessage,
            JobCompletedEventMessage,
            JobCreationMessage,
            JobDelayMessage,
            JobDetailsMessage,
            JobListMessage,
            JobQuoteAcceptedEventMessage,
            JobQuoteCancelledEventMessage,
            JobQuotedEventMessage,
            JobQuotedMessage,
            JobStatusChangedEventMessage,
            JobStatusMessage,
            JobStatusNoIdMessage,
            JobSubmitMessage,
            JobSummaryMessage,
            JobTargetLangMessage,
            JobTargetsNoIdMessage,
            JobTranscribedEventMessage,
            LoginMessage,
            LogoutMessage,
            MachineTranslationMessage,
            NewJobMessage,
            OnboardingMessage,
            QuoteMessage,
            ReportInsightsMessage,
            RequiresMtTokenAdminMessage,
            RequiresMtTokenMessage,
            SlackPermissionsMessage,
            SrtTranslateMessage,
            SsoConnectionInfoMessage,
            SuccessfulLoginMessage,
            SuccessfulLogoutMessage,
            TranscriptionMessage,
            VerifyCompleteMessage,
            VerifyHelperMessage,
            VideoOptionsMessage,
            WelcomeBackMessage,
        )
        from app.slack.templates.models import NewJobForm, RayLanguage, SlackFile

        # Build a mock RayContext
        mock_context = MagicMock()
        mock_context.__getitem__ = lambda self, key: {
            "user_id": USER_ID,
            "team_id": TEAM_ID,
            "channel_id": CHANNEL_ID,
        }.get(key, None)
        mock_context.get = lambda key, default=None: {
            "user_id": USER_ID,
            "team_id": TEAM_ID,
            "channel_id": CHANNEL_ID,
            "ray": ray_connection,
        }.get(key, default)
        mock_context.enterprise_id = ENTERPRISE_ID
        mock_context.ray = ray_connection

        # ---- Auth & Connection ----
        add(
            "OnboardingMessage",
            "Auth",
            OnboardingMessage(USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID),
        )
        add(
            "OnboardingMessage (no login)",
            "Auth",
            OnboardingMessage(
                USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, prompt_login=False
            ),
        )
        add(
            "LoginMessage (default)",
            "Auth",
            LoginMessage(USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID),
        )
        add(
            "LoginMessage (get_job)",
            "Auth",
            LoginMessage(
                USER_ID,
                TEAM_ID,
                ENTERPRISE_ID,
                CHANNEL_ID,
                variation=LoginMessage.GET_JOB,
            ),
        )
        add(
            "LoginMessage (new_job)",
            "Auth",
            LoginMessage(
                USER_ID,
                TEAM_ID,
                ENTERPRISE_ID,
                CHANNEL_ID,
                variation=LoginMessage.NEW_JOB,
            ),
        )
        add(
            "LoginMessage (insights)",
            "Auth",
            LoginMessage(
                USER_ID,
                TEAM_ID,
                ENTERPRISE_ID,
                CHANNEL_ID,
                variation=LoginMessage.INSIGHTS,
            ),
        )
        add(
            "LoginMessage (cancel_job)",
            "Auth",
            LoginMessage(
                USER_ID,
                TEAM_ID,
                ENTERPRISE_ID,
                CHANNEL_ID,
                variation=LoginMessage.CANCEL_JOB,
            ),
        )
        add(
            "LoginMessage (quality_eval)",
            "Auth",
            LoginMessage(
                USER_ID,
                TEAM_ID,
                ENTERPRISE_ID,
                CHANNEL_ID,
                variation=LoginMessage.QUALITY_EVALUATION,
            ),
        )
        add(
            "LoginMessage (human_trans)",
            "Auth",
            LoginMessage(
                USER_ID,
                TEAM_ID,
                ENTERPRISE_ID,
                CHANNEL_ID,
                variation=LoginMessage.HUMAN_TRANSLATION,
            ),
        )
        add(
            "LoginMessage (connected)",
            "Auth",
            LoginMessage(
                USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, ray_client=ray_client
            ),
        )
        add(
            "WelcomeBackMessage",
            "Auth",
            WelcomeBackMessage(USER_ID, ray_connection_verify),
        )
        add(
            "SuccessfulLoginMessage",
            "Auth",
            SuccessfulLoginMessage(USER_ID, "jane.doe@acme.com", ray_connection_verify),
        )
        add("LogoutMessage", "Auth", LogoutMessage(ray_client))
        add("LogoutMessage (SSO)", "Auth", LogoutMessage(ray_client_sso))
        add(
            "SuccessfulLogoutMessage",
            "Auth",
            SuccessfulLogoutMessage(USER_ID, ray_username="jane.doe@acme.com"),
        )
        add(
            "SuccessfulLogoutMessage (SSO)",
            "Auth",
            SuccessfulLogoutMessage(
                USER_ID, is_sso=True, ray_username="jane.doe@acme.com"
            ),
        )
        add(
            "SlackPermissionsMessage",
            "Auth",
            SlackPermissionsMessage(
                "Please grant additional permissions to use this feature."
            ),
        )
        add(
            "InfoMessage",
            "Auth",
            InfoMessage(ray_client, USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, False),
        )
        add(
            "InfoMessage (IBM)",
            "Auth",
            InfoMessage(ray_client, USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, True),
        )
        add(
            "ConnectionInfoMessage",
            "Auth",
            ConnectionInfoMessage(
                ray_connection, USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID
            ),
        )
        add(
            "ConnectionInfoMessage (IBM)",
            "Auth",
            ConnectionInfoMessage(
                ray_connection, USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, is_ibm=True
            ),
        )
        add(
            "SsoConnectionInfoMessage", "Auth", SsoConnectionInfoMessage(ray_connection)
        )

        # ---- Job Status ----
        add("JobStatusMessage", "Jobs", JobStatusMessage(job, CLIENT_UUID, False))
        add(
            "JobStatusMessage (IBM)",
            "Jobs",
            JobStatusMessage(job, CLIENT_UUID, True),
        )
        add("JobDetailsMessage", "Jobs", JobDetailsMessage(job, CLIENT_UUID, False))
        add(
            "JobDetailsMessage (IBM)",
            "Jobs",
            JobDetailsMessage(job, CLIENT_UUID, True),
        )
        add("InvalidJobMessage", "Jobs", InvalidJobMessage("TJ999999"))
        add("JobStatusNoIdMessage", "Jobs", JobStatusNoIdMessage())
        add(
            "JobSummaryMessage",
            "Jobs",
            JobSummaryMessage(
                in_progress=8,
                in_progress_count_24=3,
                in_progress_due=2,
                completed=15,
                validation=4,
                pending_quotes=2,
                order_now=1,
            ),
        )
        add(
            "JobSummaryMessage (all jobs)",
            "Jobs",
            JobSummaryMessage(
                in_progress=8,
                in_progress_count_24=3,
                in_progress_due=2,
                completed=15,
                validation=4,
                pending_quotes=2,
                order_now=1,
                all_jobs=True,
            ),
        )

        list_pagination = MagicMock()
        list_pagination.page = 1
        list_pagination.total_pages = 3
        list_pagination.rows_per_page = 5
        list_pagination.total = 12
        add(
            "JobListMessage",
            "Jobs",
            JobListMessage(
                "in_progress",
                "In Progress Jobs",
                [job],
                list_pagination,
            ),
        )
        add(
            "NewJobMessage",
            "Jobs",
            NewJobMessage(CHANNEL_ID, "1234567890.123456", FILE_INFO),
        )
        add(
            "NewJobMessage (verify)",
            "Jobs",
            NewJobMessage(
                CHANNEL_ID, "1234567890.123456", FILE_INFO, is_verify_enabled=True
            ),
        )

        new_job_form = NewJobForm(
            files=[SlackFile(id="F001", title="homepage.html")],
            reference="Website Q1",
            source_lang=RayLanguage(code="en", name="English"),
            target_langs=[
                RayLanguage(code="fr", name="French"),
                RayLanguage(code="es", name="Spanish"),
            ],
            service="Translation",
            timeframe="Standard",
        )
        add("JobSubmitMessage", "Jobs", JobSubmitMessage(new_job_form))
        add("JobCreationMessage", "Jobs", JobCreationMessage("TJ123456"))
        add(
            "JobCreationMessage (auto quote)",
            "Jobs",
            JobCreationMessage("TJ123456", is_auto_quote=True),
        )

        add(
            "FileTranslatedMessage",
            "Jobs",
            FileTranslatedMessage(
                "TJ123456",
                "homepage.html",
                "English",
                [
                    {"tl": "French", "download_url": "#mock"},
                    {"tl": "Spanish", "download_url": "#mock"},
                ],
            ),
        )
        add("BatchListMessage", "Jobs", BatchListMessage(job, CLIENT_UUID))
        add("FileListMessage", "Jobs", FileListMessage(job, CLIENT_UUID))
        add("JobTargetsNoIdMessage", "Jobs", JobTargetsNoIdMessage())

        job_pending = make_job_mock()
        job_pending.status = "PENDING_QUOTES"
        add(
            "JobTargetLangMessage (pending)",
            "Jobs",
            JobTargetLangMessage(job_pending, CLIENT_UUID),
        )

        add(
            "CancelJobMessage",
            "Jobs",
            CancelJobMessage(CHANNEL_ID, "1234567890.123456"),
        )
        add("CancelTJMessage", "Jobs", CancelTJMessage(make_cancel_job_detail()))
        add("JobDelayMessage", "Jobs", JobDelayMessage())

        # ---- Quotes ----
        add("QuoteMessage", "Quotes", QuoteMessage())
        add("JobQuotedMessage", "Quotes", JobQuotedMessage(quote, False))
        add("JobQuotedMessage (IBM)", "Quotes", JobQuotedMessage(quote, True))

        event = make_job_quote_created_event()
        add("JobQuotedEventMessage", "Quotes", JobQuotedEventMessage(event, False))
        add(
            "JobQuotedEventMessage (IBM)",
            "Quotes",
            JobQuotedEventMessage(event, True),
        )
        add(
            "JobQuoteAcceptedEventMessage",
            "Quotes",
            JobQuoteAcceptedEventMessage(make_job_quote_accepted_event(), False),
        )
        add(
            "JobQuoteAcceptedEventMessage (IBM)",
            "Quotes",
            JobQuoteAcceptedEventMessage(make_job_quote_accepted_event(), True),
        )
        add(
            "JobQuoteCancelledEventMessage",
            "Quotes",
            JobQuoteCancelledEventMessage(CLIENT_UUID, JOB_UUID, "TJ123456", False),
        )
        add(
            "JobQuoteCancelledEventMessage (IBM)",
            "Quotes",
            JobQuoteCancelledEventMessage(CLIENT_UUID, JOB_UUID, "TJ123456", True),
        )

        eval_job = make_evaluate_job()
        costs = make_costs()
        add("HumanJobQuoteMessage", "Quotes", HumanJobQuoteMessage(eval_job, costs))

        # ---- Events ----
        signup = make_client_signup_event()
        add("ClientSignupEventMessage", "Events", ClientSignupEventMessage(signup))
        add(
            "ClientSignupEventAdminMessage",
            "Events",
            ClientSignupEventAdminMessage(signup, signup.groups),
        )
        add(
            "ClientApprovedEventMessage",
            "Events",
            ClientApprovedEventMessage(["Marketing", "Engineering"]),
        )
        add(
            "ClientApprovedMessage",
            "Events",
            ClientApprovedMessage("john.smith@acme.com"),
        )
        add(
            "ClientAlreadyApprovedMessage",
            "Events",
            ClientAlreadyApprovedMessage("john.smith@acme.com"),
        )
        add(
            "JobStatusChangedEventMessage",
            "Events",
            JobStatusChangedEventMessage(
                CLIENT_UUID, JOB_UUID, "TJ123456", "COMPLETED", False
            ),
        )
        add(
            "JobStatusChangedEventMessage (IBM)",
            "Events",
            JobStatusChangedEventMessage(
                CLIENT_UUID, JOB_UUID, "TJ123456", "COMPLETED", True
            ),
        )
        add(
            "JobCompletedEventMessage",
            "Events",
            JobCompletedEventMessage(
                CLIENT_UUID,
                JOB_UUID,
                "TJ123456",
                ["French", "Spanish", "German"],
                False,
            ),
        )
        add(
            "JobCompletedEventMessage (IBM)",
            "Events",
            JobCompletedEventMessage(
                CLIENT_UUID,
                JOB_UUID,
                "TJ123456",
                ["French", "Spanish", "German"],
                True,
            ),
        )
        add(
            "JobCancelledEventMessage",
            "Events",
            JobCancelledEventMessage(CLIENT_UUID, JOB_UUID, "TJ123456"),
        )

        # ---- Translation ----
        add(
            "AutoTranslationMessage",
            "Translation",
            AutoTranslationMessage(
                source_text="Hello, how are you today?",
                source_language="en",
                translations={
                    "fr": ["Bonjour, comment allez-vous aujourd'hui?"],
                    "es": ["Hola, como estas hoy?"],
                },
            ),
        )
        add(
            "MachineTranslationMessage",
            "Translation",
            MachineTranslationMessage("fr", "en", "Hello world", "Bonjour le monde"),
        )
        add("SrtTranslateMessage", "Translation", SrtTranslateMessage("task-uuid-001"))
        add(
            "DocumentMTJobMessage",
            "Translation",
            DocumentMTJobMessage("output-file-001"),
        )
        add(
            "AutoTranslateSettingsChangedMessage",
            "Translation",
            AutoTranslateSettingsChangedMessage(
                USER_ID, CHANNEL_ID, ["fr", "es"], "thread"
            ),
        )
        add(
            "AutoTranslateSettingsDisabledMessage",
            "Translation",
            AutoTranslateSettingsDisabledMessage(USER_ID, CHANNEL_ID),
        )
        add(
            "ImageToMarkdownMessage",
            "Translation",
            ImageToMarkdownMessage(
                "# Document Title\n\nThis is a **translated** document with some content.\n\n- Item 1\n- Item 2",
                "scan.png",
            ),
        )

        # ---- Video / Transcription ----
        video_files = [
            {
                "file_id": "VF001",
                "file_name": "meeting-recording.mp4",
                "duration_ms": 180000,
            },
            {
                "file_id": "VF002",
                "file_name": "product-demo.mp4",
                "duration_ms": 300000,
            },
        ]
        add(
            "VideoOptionsMessage", "Video", VideoOptionsMessage(CHANNEL_ID, video_files)
        )
        add(
            "VideoOptionsMessage (with tokens)",
            "Video",
            VideoOptionsMessage(CHANNEL_ID, video_files, tokens=5000),
        )
        add(
            "VideoOptionsMessage (IBM)",
            "Video",
            VideoOptionsMessage(
                CHANNEL_ID, video_files, is_ibm_enterprise=True, tokens=5000
            ),
        )
        add(
            "JobTranscribedEventMessage",
            "Video",
            JobTranscribedEventMessage("meeting-recording.mp4"),
        )
        add(
            "JobTranscribedEventMessage (tokens)",
            "Video",
            JobTranscribedEventMessage("meeting-recording.mp4", tokens_used=1250),
        )
        add(
            "JobTranscribedEventMessage (IBM, tokens)",
            "Video",
            JobTranscribedEventMessage(
                "meeting-recording.mp4", is_ibm_enterprise=True, tokens_used=1250
            ),
        )
        add(
            "TranscriptionMessage",
            "Video",
            TranscriptionMessage("meeting-recording.mp4"),
        )

        # ---- Quality Evaluation ----
        add(
            "EvaluateSuccessMessage",
            "Quality",
            EvaluateSuccessMessage(eval_job, False, tokens=500),
        )
        add(
            "EvaluateSuccessMessage (IBM)",
            "Quality",
            EvaluateSuccessMessage(eval_job, True, tokens=500),
        )
        add(
            "EvaluateSuccessMessage (no actions)",
            "Quality",
            EvaluateSuccessMessage(eval_job, False, actions=False),
        )
        add("EvaluateErrorMessage", "Quality", EvaluateErrorMessage())
        add(
            "VerifyCompleteMessage",
            "Quality",
            VerifyCompleteMessage("Website Q1", "French"),
        )

        # ---- Fact Check ----
        add(
            "FactCheckResultMessage",
            "FactCheck",
            FactCheckResultMessage(make_fact_check_data()),
        )
        add(
            "FactCheckResultMessage (error)",
            "FactCheck",
            FactCheckResultMessage(
                {"file_name": "doc.pdf", "job_uuid": "x", "fact_check_result": None}
            ),
        )

        # ---- Help & Info ----
        add("HelpMessage", "Help", HelpMessage(mock_context))
        add(
            "InsightsMessage",
            "Help",
            InsightsMessage(
                "You have completed 15 jobs in the last 30 days with an average turnaround of 3.2 days."
            ),
        )
        add("ReportInsightsMessage", "Help", ReportInsightsMessage("Growth"))
        add("AIHelperMessage", "Help", AIHelperMessage())
        add("VerifyHelperMessage", "Help", VerifyHelperMessage())
        add("HumanJobMessage", "Help", HumanJobMessage())

        # ---- Errors ----
        add("InvalidCommandMessage", "Errors", InvalidCommandMessage())
        add("InvalidMTResultMessage", "Errors", InvalidMTResultMessage())
        add("DocMtMessage", "Errors", DocMtMessage())
        add(
            "DocParseErrorMessage",
            "Errors",
            DocParseErrorMessage(".xlsx", "spreadsheet"),
        )
        add("DocComplexityErrorMessage", "Errors", DocComplexityErrorMessage(".xlsx"))
        add(
            "DocInvalidPdfErrorMessage",
            "Errors",
            DocInvalidPdfErrorMessage(
                "The PDF is password-protected and cannot be processed."
            ),
        )
        add(
            "FileTooLargeMessage",
            "Errors",
            FileTooLargeMessage("huge-video.mp4", 52_428_800),
        )

        # ---- Tokens ----
        add(
            "RequiresMtTokenMessage (no tokens)",
            "Tokens",
            RequiresMtTokenMessage(0, 500),
        )
        add(
            "RequiresMtTokenMessage (low tokens)",
            "Tokens",
            RequiresMtTokenMessage(100, 500),
        )
        add(
            "RequiresMtTokenAdminMessage", "Tokens", RequiresMtTokenAdminMessage(0, 500)
        )

    # ---- IBM-specific LoginMessage variants ----
    # LoginMessage calls is_ibm_enterprise() internally; separate patch context
    # to avoid exceeding Python's static nesting limit.
    MSG = "app.slack.templates.messages"
    BLK = "app.slack.templates.blocks"
    with (
        patch(
            f"{MSG}.get_language_cloud_connect_url", return_value="#mock-connect-url"
        ),
        patch(f"{MSG}.is_ibm_enterprise", return_value=True),
        patch(f"{MSG}.config", _config_mock),
        patch(f"{MSG}.domains", _domains_mock),
        patch(f"{BLK}.is_ibm_enterprise", return_value=True),
    ):
        from app.slack.templates.messages import LoginMessage

        def add(name: str, category: str, obj: Any):
            data = safe_extract(obj)
            if data:
                entries.append({"name": name, "category": category, **data})

        add(
            "LoginMessage (IBM, default)",
            "Auth",
            LoginMessage(USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID),
        )
        add(
            "LoginMessage (IBM, get_job)",
            "Auth",
            LoginMessage(
                USER_ID,
                TEAM_ID,
                ENTERPRISE_ID,
                CHANNEL_ID,
                variation=LoginMessage.GET_JOB,
            ),
        )
        add(
            "LoginMessage (IBM, new_job)",
            "Auth",
            LoginMessage(
                USER_ID,
                TEAM_ID,
                ENTERPRISE_ID,
                CHANNEL_ID,
                variation=LoginMessage.NEW_JOB,
            ),
        )

    return entries


_HOME_MESSAGE_URL = f"slack://app?team={TEAM_ID}&id=A_MOCK_APP_ID&tab=messages"


def _build_home_blocks(
    auth_blocks: list[dict[str, Any]],
    connected: bool,
    is_ibm: bool,
) -> list[dict[str, Any]]:
    """Build home view blocks matching the real home_view function."""
    translation_settings_blocks: list[dict[str, Any]] = [
        {"type": "divider"},
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Translate Channels"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Transform your messages instantly so that everyone in your Slack channel can effortlessly understand and engage in conversations, regardless of their language preferences.",
            },
        },
    ]
    if connected:
        translation_settings_blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": ":speech_balloon: Translation settings",
                        },
                        "action_id": "settings_auto_translate",
                    },
                ],
            }
        )

    footer_elements = [
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "emoji": True,
                "text": ":question: Help Centre",
            },
            "action_id": "link_2",
            "url": "https://help.straker.ai/en/docs/workplace-apps#straker-translate-app-for-slack",
        },
    ]
    if not is_ibm:
        footer_elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "emoji": True,
                    "text": "Visit Straker Verify",
                },
                "action_id": "link_1",
                "url": _domains_mock.verify,
            },
        )

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":wave: Welcome to Straker Translate!",
            },
        },
        *auth_blocks,
        {"type": "divider"},
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Get Started"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Here are some things to get you started. Also make sure you check out our Help Centre and use our built in chatbot within our app to guide you through the translation process.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": ":sunny: Daily Summary",
                    },
                    "action_id": "daily_summary",
                    "url": _HOME_MESSAGE_URL,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": ":bar_chart: Insights",
                    },
                    "action_id": "report_insights",
                    "url": _HOME_MESSAGE_URL,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": ":question: AI Translate Help",
                    },
                    "action_id": "ai_translate_help",
                    "url": _HOME_MESSAGE_URL,
                },
            ],
        },
        *translation_settings_blocks,
        {"type": "divider"},
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Give us your feedback"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Straker Community is a place for Straker users to provide feedback, and help each other get the most out of our platform. It's also a place for us to talk about the latest and greatest Verify and Enterprise features, provide updates, and engage with customers like you!",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Learn More",
                        "emoji": False,
                    },
                    "action_id": "link_0",
                    "url": "https://help.straker.ai/en/docs/straker-translate-functions",
                },
            ],
        },
        {"type": "divider"},
        {"type": "actions", "elements": footer_elements},
    ]


def build_all_views() -> list[dict[str, Any]]:
    """Call all view builder functions and return their block payloads."""
    entries: list[dict[str, Any]] = []

    def add(name: str, category: str, obj: Any):
        data = safe_extract(obj)
        if data:
            entries.append({"name": name, "category": category, **data})

    VW = "app.slack.templates.views"
    BLK = "app.slack.templates.blocks"
    with (
        patch(
            f"{VW}.get_auto_translate_language_options",
            return_value=MOCK_LANGUAGE_OPTIONS,
        ),
        patch(
            f"{VW}.filter_auto_translate_language_options",
            return_value=MOCK_LANGUAGE_OPTIONS[:2],
        ),
        patch(
            f"{VW}.translation_display_format_options",
            return_value=MOCK_DISPLAY_FORMAT_OPTIONS,
        ),
        patch(
            f"{VW}.map_translation_display_format_option",
            return_value=MOCK_DISPLAY_FORMAT_OPTIONS[0],
        ),
        patch(
            f"{VW}.map_file_options",
            return_value=(
                [
                    {
                        "text": {"type": "plain_text", "text": f["title"]},
                        "value": f["id"],
                    }
                    for f in FILE_INFO
                ],
                [
                    {
                        "text": {"type": "plain_text", "text": FILE_INFO[0]["title"]},
                        "value": FILE_INFO[0]["id"],
                    }
                ],
            ),
        ),
        patch(f"{VW}.is_ibm_enterprise", return_value=False),
        patch(f"{VW}.domains", _domains_mock),
        patch(
            f"{VW}.get_auto_translate_language_name",
            side_effect=lambda code: {
                "en": "English",
                "fr": "French",
                "es": "Spanish",
                "de": "German",
            }.get(code, code),
        ),
        patch(
            f"{BLK}.get_language_cloud_connect_url", return_value="#mock-connect-url"
        ),
        patch(f"{BLK}.get_job_url", return_value="#mock-job-url"),
        patch(f"{BLK}.is_ibm_enterprise", return_value=False),
        patch(f"{BLK}.format_currency", side_effect=lambda v, c: f"${v:,.2f}"),
        patch(f"{BLK}.format_currency_symbol", return_value="USD"),
        patch(f"{BLK}.domains", _domains_mock),
        patch(
            f"{BLK}.get_languages_sync",
            return_value=[
                {"uuid": "lang-en-uuid", "name": "English"},
                {"uuid": "lang-fr-uuid", "name": "French"},
                {"uuid": "lang-es-uuid", "name": "Spanish"},
            ],
        ),
    ):
        from app.slack.templates.views import (
            cancel_job_modal,
            document_mt_job_modal,
            fact_check_human_verification_modal,
            fact_check_human_verification_thank_you_modal,
            fact_check_job_modal,
            human_job_modal,
            job_search_modal,
            loading_modal,
            srt_translate_modal,
            sso_form_modal,
            translation_settings_view,
            translation_settings_view_error,
            verify_job_modal,
            verify_quote_summary_modal,
            video_embed_subtitles_modal,
            video_transcribe_translate_modal,
        )

        eval_job = make_evaluate_job()
        costs = make_costs()
        video_files = [
            {
                "file_id": "VF001",
                "file_name": "meeting-recording.mp4",
                "duration_ms": 180000,
            },
        ]

        # Sync view functions
        add("job_search_modal", "Modals", job_search_modal("jane.doe@acme.com"))
        add(
            "human_job_modal (human)",
            "Modals",
            human_job_modal(CHANNEL_ID, FILE_INFO, False, "human"),
        )
        add(
            "human_job_modal (human, IBM)",
            "Modals",
            human_job_modal(CHANNEL_ID, FILE_INFO, True, "human"),
        )
        add(
            "human_job_modal (evaluate)",
            "Modals",
            human_job_modal(CHANNEL_ID, FILE_INFO, False, "evaluate"),
        )
        add(
            "human_job_modal (evaluate, IBM)",
            "Modals",
            human_job_modal(CHANNEL_ID, FILE_INFO, True, "evaluate"),
        )
        add("sso_form_modal", "Modals", sso_form_modal())
        add("cancel_job_modal", "Modals", cancel_job_modal("jane.doe@acme.com"))
        add(
            "translation_settings_view",
            "Modals",
            translation_settings_view(team_id=TEAM_ID),
        )
        add(
            "translation_settings_view (with initial)",
            "Modals",
            translation_settings_view(
                initial_channels=[CHANNEL_ID],
                initial_langs=["fr", "es"],
                display_format="thread",
                team_id=TEAM_ID,
            ),
        )
        add(
            "translation_settings_view_error",
            "Modals",
            translation_settings_view_error(
                "Channel not found or bot not added to channel."
            ),
        )
        add(
            "verify_job_modal",
            "Modals",
            verify_job_modal(eval_job, costs, "1234567890.123456"),
        )
        add(
            "verify_quote_summary_modal",
            "Modals",
            verify_quote_summary_modal(eval_job, costs, "1234567890.123456"),
        )
        add(
            "document_mt_job_modal",
            "Modals",
            document_mt_job_modal(CHANNEL_ID, FILE_INFO),
        )
        add(
            "document_mt_job_modal (with images)",
            "Modals",
            document_mt_job_modal(CHANNEL_ID, FILE_INFO, has_images=True),
        )
        add(
            "fact_check_job_modal",
            "Modals",
            fact_check_job_modal(CHANNEL_ID, FILE_INFO),
        )
        add(
            "fact_check_human_verification_modal",
            "Modals",
            fact_check_human_verification_modal("fc-uuid", "claims.pdf", 14, "English"),
        )
        add(
            "fact_check_human_verification_thank_you_modal",
            "Modals",
            fact_check_human_verification_thank_you_modal(),
        )
        add("loading_modal", "Modals", loading_modal())
        add(
            "srt_translate_modal",
            "Modals",
            srt_translate_modal("task-uuid-001", CHANNEL_ID),
        )
        add(
            "video_transcribe_translate_modal",
            "Modals",
            video_transcribe_translate_modal(CHANNEL_ID, video_files),
        )
        add(
            "video_embed_subtitles_modal",
            "Modals",
            video_embed_subtitles_modal(CHANNEL_ID, video_files),
        )

        # home_view non-IBM variants (connected and not connected)
        from app.slack.templates.blocks import home_auth_blocks

        ray_connection = make_ray_connection()
        auth_blocks_connected = home_auth_blocks(
            USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, ray_connection
        )
        add(
            "home_view (connected)",
            "Home",
            {
                "type": "home",
                "blocks": _build_home_blocks(auth_blocks_connected, True, False),
            },
        )

        auth_blocks_not_connected = home_auth_blocks(
            USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, None
        )
        add(
            "home_view (not connected)",
            "Home",
            {
                "type": "home",
                "blocks": _build_home_blocks(auth_blocks_not_connected, False, False),
            },
        )

    # IBM home view variants — separate patch context to avoid nesting limit
    BLK = "app.slack.templates.blocks"
    with (
        patch(
            f"{BLK}.get_language_cloud_connect_url", return_value="#mock-connect-url"
        ),
        patch(f"{BLK}.is_ibm_enterprise", return_value=True),
        patch(f"{BLK}.domains", _domains_mock),
    ):
        from app.slack.templates.blocks import home_auth_blocks

        ray_connection = make_ray_connection()
        ibm_auth_connected = home_auth_blocks(
            USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, ray_connection
        )
        entries.append(
            {
                "name": "home_view (IBM, connected)",
                "category": "Home",
                **safe_extract(
                    {
                        "type": "home",
                        "blocks": _build_home_blocks(ibm_auth_connected, True, True),
                    }
                ),
            }
        )

        ibm_auth_not_connected = home_auth_blocks(
            USER_ID, TEAM_ID, ENTERPRISE_ID, CHANNEL_ID, None
        )
        entries.append(
            {
                "name": "home_view (IBM, not connected)",
                "category": "Home",
                **safe_extract(
                    {
                        "type": "home",
                        "blocks": _build_home_blocks(
                            ibm_auth_not_connected, False, True
                        ),
                    }
                ),
            }
        )

    return entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("Generating Block Kit JSON for all templates...")

    messages = build_all_messages()
    views = build_all_views()

    output = {
        "generated_at": datetime.now().isoformat(),
        "messages": messages,
        "views": views,
        "stats": {
            "total_messages": len(messages),
            "total_views": len(views),
            "total": len(messages) + len(views),
        },
    }

    output_path = Path(__file__).parent / "output" / "blocks.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, default=str))

    print(f"Generated {output['stats']['total']} templates -> {output_path}")
    print(f"  Messages: {output['stats']['total_messages']}")
    print(f"  Views:    {output['stats']['total_views']}")


if __name__ == "__main__":
    main()
