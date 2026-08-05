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

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

APP_TRANSLATION_SOURCES = {"app", "db", "database"}


def _requested_translation_source() -> str:
    """Read translation source early, before import-time mocks are installed."""
    for index, arg in enumerate(sys.argv[1:]):
        if arg == "--translation-source" and index + 2 < len(sys.argv):
            return sys.argv[index + 2].strip().lower()
        if arg.startswith("--translation-source="):
            return arg.split("=", 1)[1].strip().lower()
    env_value = os.environ.get("UI_EXPORT_TRANSLATION_SOURCE")
    if env_value:
        return env_value.strip().lower()
    return "catalog"


TRANSLATION_SOURCE = _requested_translation_source()
USE_APP_TRANSLATOR = TRANSLATION_SOURCE in APP_TRANSLATION_SOURCES

# ---------------------------------------------------------------------------
# Ensure the repo root is on sys.path so app.* imports resolve
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Patch heavy/side-effecty modules *before* importing app code
# ---------------------------------------------------------------------------

# Patch database engines unless the real app translator was explicitly requested.
if not USE_APP_TRANSLATOR:
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


class _Environment(str, Enum):
    production = "production"
    uat = "uat"
    local = "local"


if not USE_APP_TRANSLATOR:
    _straker_utils = MagicMock()
    # Parent must look like a package so nested imports (e.g. redis.asyncio) resolve.
    _straker_utils.__path__ = []
    for sub in (
        "straker_utils",
        "straker_utils.sql",
        "straker_utils.sql.async_engine",
        "straker_utils.domain",
        "straker_utils.environment",
        "straker_utils.redis",
        "straker_utils.redis.asyncio",
    ):
        sys.modules.setdefault(sub, _straker_utils)
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

# Patch translation function to use a selectable export language. By default
# this stays offline and deterministic using JSON catalogs; opt into the app's
# real DB-backed translator with --translation-source app.

DEFAULT_LANGUAGE = "en"
PLACEHOLDER_PATTERN = re.compile(r":\w+:|\{.*?\}")
_active_language = DEFAULT_LANGUAGE
_translation_catalog: dict[str, str] = {}


def parse_languages(value: str | None) -> list[str]:
    """Parse comma-separated language codes, preserving order."""
    if not value:
        return [DEFAULT_LANGUAGE]
    languages = []
    seen = set()
    for language in value.split(","):
        language = language.strip()
        if not language or language in seen:
            continue
        languages.append(language)
        seen.add(language)
    return languages or [DEFAULT_LANGUAGE]


def load_translation_catalog(path: Path | None, language: str) -> dict[str, str]:
    """Load optional UI export translations for a language.

    Supported JSON shapes:
      {"fr": {"Hello": "Bonjour"}}
      {"Hello": "Bonjour"}
    """
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("translation catalog must be a JSON object")
    language_data = data.get(language, data)
    if not isinstance(language_data, dict):
        raise ValueError(f"translation catalog for {language} must be a JSON object")
    return {
        str(source): str(translation)
        for source, translation in language_data.items()
        if isinstance(source, str) and isinstance(translation, str)
    }


def configure_translation(language: str, catalog: dict[str, str] | None = None) -> None:
    """Set the language used by the export translation function."""
    global _active_language, _translation_catalog
    _active_language = language
    _translation_catalog = catalog or {}
    if USE_APP_TRANSLATOR:
        translator_var.set(Translator(language))
    install_template_translation_function()


def install_template_translation_function() -> None:
    """Keep already-imported Slack template modules on the selected translator."""
    translate_function = (
        sys.modules["app.translate"]._ if USE_APP_TRANSLATOR else _mock_translate
    )
    for module_name in (
        "app.translate",
        "app.slack.templates.blocks",
        "app.slack.templates.messages",
        "app.slack.templates.views",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            module._ = translate_function


def _tag_placeholders(text: str) -> tuple[str, dict[str, tuple[str, str, str]]]:
    replacements = {}

    def replace(match: re.Match[str]) -> str:
        index = len(replacements) + 1
        tag = f'<x id="{index}"/>'
        unquoted_tag = f"<x id={index}/>"
        legacy_tag = f"<x id={index}>"
        replacements[match.group()] = (tag, unquoted_tag, legacy_tag)
        return tag

    return PLACEHOLDER_PATTERN.sub(replace, text), replacements


def _translate_catalog_text(text: str) -> str:
    if _active_language.lower().startswith(("en", "gb", "us")):
        return text

    tagged_text, replacements = _tag_placeholders(text)
    translation = _translation_catalog.get(tagged_text) or _translation_catalog.get(
        text
    )
    if translation is None:
        unquoted_tagged_text = tagged_text
        legacy_tagged_text = tagged_text
        for tag, unquoted_tag, legacy_tag in replacements.values():
            unquoted_tagged_text = unquoted_tagged_text.replace(tag, unquoted_tag)
            legacy_tagged_text = legacy_tagged_text.replace(tag, legacy_tag)
        translation = _translation_catalog.get(
            unquoted_tagged_text
        ) or _translation_catalog.get(legacy_tagged_text)
        if translation is None:
            return text

    for original, (tag, unquoted_tag, legacy_tag) in replacements.items():
        translation = translation.replace(tag, original)
        translation = translation.replace(unquoted_tag, original)
        translation = translation.replace(legacy_tag, original)
    return translation


def _mock_translate(text, *args, **kwargs):
    """Translate via the selected catalog and resolve f-string style variables."""
    import inspect

    frame = inspect.currentframe()
    caller_locals = {}
    if frame and frame.f_back:
        caller_locals = frame.f_back.f_locals
        # Check one more level up for class __init__ methods
        if frame.f_back.f_back:
            caller_locals = {**frame.f_back.f_back.f_locals, **caller_locals}
    translated_text = _translate_catalog_text(text)
    try:
        return translated_text.format(**caller_locals)
    except (KeyError, IndexError, AttributeError):
        return translated_text


if USE_APP_TRANSLATOR:
    from app.translate import Translator, translator_var  # noqa: E402
else:
    sys.modules.setdefault("app.translate", MagicMock())
    sys.modules["app.translate"]._ = _mock_translate

# Don't mock sqlalchemy — the real package is needed for imports

# Mock watson module (tries to connect to IBM Watson on import)
sys.modules.setdefault("app.watson", MagicMock())
sys.modules.setdefault("app.watson.assistant", MagicMock())

# Mock API modules that try to connect on import
sys.modules.setdefault("app.api.language_cloud", MagicMock())
sys.modules.setdefault("app.api.verify", MagicMock())
sys.modules.setdefault("app.api.stream_proxy", MagicMock())
sys.modules.setdefault("app.api.verifyloop", MagicMock())

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


def make_document_mt_quote_session(*, accepted: bool = False) -> dict[str, Any]:
    return {
        "quote_id": JOB_UUID,
        "channel_id": CHANNEL_ID,
        "user_id": USER_ID,
        "team_id": TEAM_ID,
        "enterprise_id": ENTERPRISE_ID,
        "status": "accepted" if accepted else "quoted",
        "files": [
            {"id": "F001", "title": "strategy-overview.docx", "size": 48210},
            {"id": "F002", "title": "legal-appendix.pdf", "size": 138912},
        ],
        "source_language": "en",
        "target_languages": ["fr", "es"],
        "quote": {
            "quote_id": JOB_UUID,
            "client_id": CLIENT_UUID,
            "channel_id": CHANNEL_ID,
            "currency": "USD",
            "total_tokens": 1420,
            "pdf_conversion_tokens": 100,
            "total_cost_usd": 28.40,
            "preflight_task_uuid": JOB_UUID,
            "files": [
                {
                    "file_id": "F001",
                    "file_name": "strategy-overview.docx",
                    "character_count": 310_000,
                    "pdf_conversion_page_count": None,
                    "pdf_conversion_tokens": 0,
                    "target_languages": [
                        {"target_language": "fr", "tokens": 0, "cost_usd": 0.0},
                        {"target_language": "es", "tokens": 0, "cost_usd": 0.0},
                    ],
                },
                {
                    "file_id": "F002",
                    "file_name": "legal-appendix.pdf",
                    "character_count": 350_000,
                    "pdf_conversion_page_count": 4,
                    "pdf_conversion_tokens": 100,
                    "target_languages": [
                        {"target_language": "fr", "tokens": 0, "cost_usd": 0.0},
                        {"target_language": "es", "tokens": 0, "cost_usd": 0.0},
                    ],
                },
            ],
        },
    }


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
            "service_list": [
                {
                    "estimated_cost": 45.75,
                    "time_estimate_days": 3,
                    "quality_discount": {
                        "score": 0.91,
                        "tier": "good",
                        "word_discount_rate": 0.3,
                        "base_estimated_cost": 65.36,
                        "discounted_estimated_cost": 45.75,
                        "final_estimated_cost": 45.75,
                        "savings": 19.61,
                        "pricing_cap_applied": False,
                    },
                }
            ],
        },
        {
            "file_uuid": "file-uuid-001",
            "language_uuid": "lang-es-uuid",
            "service_list": [
                {
                    "estimated_cost": 38.50,
                    "time_estimate_days": 2,
                    "quality_discount": {
                        "score": 0.82,
                        "tier": "acceptable",
                        "word_discount_rate": 0.2,
                        "base_estimated_cost": 48.13,
                        "discounted_estimated_cost": 38.50,
                        "final_estimated_cost": 38.50,
                        "savings": 9.63,
                        "pricing_cap_applied": False,
                    },
                }
            ],
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
    {
        "text": {"type": "plain_text", "text": "Spanish (Latin America)"},
        "value": "es-419",
    },
]

MOCK_DISPLAY_FORMAT_OPTIONS = [
    {"text": {"type": "plain_text", "text": "Thread replies"}, "value": "thread"},
    {"text": {"type": "plain_text", "text": "Direct messages"}, "value": "message"},
]


def mock_format_currency(value: float, currency: str) -> str:
    """Mirror the runtime USD display shape without depending on Babel in catalog tests."""
    normalized_currency = currency.split("_", 1)[0]
    if normalized_currency == "USD":
        return f"USD {value:,.2f}"
    return f"{normalized_currency} {value:,.2f}"


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
        patch(f"{BLK}.format_currency", side_effect=mock_format_currency),
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
            EvaluateAiOnlyCompleteMessage,
            EvaluateErrorMessage,
            EvaluateSuccessMessage,
            EvaluationCreditsQuoteMessage,
            FileListMessage,
            FileTooLargeMessage,
            FileTranslatedMessage,
            HelpMessage,
            HumanJobMessage,
            InfoMessage,
            InvalidCommandMessage,
            InvalidJobMessage,
            InvalidMTResultMessage,
            JobCancelledEventMessage,
            JobCompletedEventMessage,
            JobCreationMessage,
            JobDelayMessage,
            JobDetailsMessage,
            JobFileListEmptyMessage,
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
            MediaEmbeddingPartialMessage,
            MediaTranslationPartialMessage,
            MissingSlackFilesMessage,
            NewJobMessage,
            OnboardingMessage,
            QuoteMessage,
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
        add(
            "NewJobMessage (IBM)",
            "Jobs",
            NewJobMessage(
                CHANNEL_ID,
                "1234567890.123456",
                FILE_INFO,
                is_ibm_enterprise=True,
            ),
        )
        add(
            "NewJobMessage (IBM, verify)",
            "Jobs",
            NewJobMessage(
                CHANNEL_ID,
                "1234567890.123456",
                FILE_INFO,
                is_verify_enabled=True,
                is_ibm_enterprise=True,
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
        # Match prod combined pre-QE quote: QE fee embedded in each language
        # line (no separate "Quality Evaluation: USD …" rows).
        from app.slack.evaluation_combined_quotes import (
            PRE_QE_QUOTE_DISPLAY,
            combined_human_job_quote_message,
            standalone_ht_quote_message,
        )

        add(
            "HumanJobQuoteMessage",
            "Quotes",
            combined_human_job_quote_message(
                eval_job,
                costs,
                # 800 tokens * $0.02 = $16 total QE → $8 per language target
                qe_token_cost=800,
                download_translations_job_uuid=JOB_UUID,
                **PRE_QE_QUOTE_DISPLAY,
            ),
        )
        add(
            "HumanJobQuoteMessage (standalone HT)",
            "Quotes",
            standalone_ht_quote_message(eval_job, costs),
        )
        add(
            "EvaluationCreditsQuoteMessage (IBM HT AI quote with PDF)",
            "Quotes",
            EvaluationCreditsQuoteMessage(
                service_label="AI Translation",
                token_cost=1250,
                job_uuid=JOB_UUID,
                accept_action_id="evaluation_ai_quote_accept",
                adjust_action_id="evaluation_ai_quote_adjust",
                pdf_page_count=4,
                pdf_tokens=100,
                is_ibm=True,
                language_costs=[
                    {
                        "file_uuid": "file-uuid-001",
                        "file_label": "marketing-copy.docx",
                        "value": "lang-fr-uuid",
                        "label": "French",
                        "token": 750,
                    },
                    {
                        "file_uuid": "file-uuid-001",
                        "file_label": "marketing-copy.docx",
                        "value": "lang-es-uuid",
                        "label": "Spanish",
                        "token": 500,
                    },
                ],
            ),
        )
        add(
            "EvaluationCreditsQuoteMessage (IBM HT PDF prequote estimate)",
            "Quotes",
            EvaluationCreditsQuoteMessage(
                service_label="AI Translation",
                token_cost=1350,
                job_uuid=JOB_UUID,
                accept_action_id="evaluation_pdf_prequote_accept",
                adjust_action_id="evaluation_ai_quote_adjust",
                pdf_page_count=4,
                pdf_tokens=100,
                is_ibm=True,
                language_costs=[
                    {
                        "file_uuid": "file-uuid-001",
                        "file_label": "brief.pdf",
                        "value": "lang-fr-uuid",
                        "label": "French",
                        "token": 850,
                    },
                    {
                        "file_uuid": "file-uuid-001",
                        "file_label": "brief.pdf",
                        "value": "lang-es-uuid",
                        "label": "Spanish",
                        "token": 500,
                    },
                ],
            ),
        )
        from app.slack.media_quotes import (
            ACTION_MEDIA_QUOTE_ACCEPT,
            ACTION_MEDIA_QUOTE_CANCEL,
            ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
            ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
            PIPELINE_TRANSCRIBE_TRANSLATE,
            STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
            STAGE_AWAITING_TRANSLATION_ACCEPT,
            media_quote_blocks,
        )

        add(
            "MediaQuoteMessage (Quote1 transcription before AI Translate)",
            "Quotes",
            {
                "type": "message",
                "blocks": media_quote_blocks(
                    {
                        "quote_id": JOB_UUID,
                        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
                        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
                        "file_name": "product-demo.mp4",
                        "line_items": [{"label": "Transcription", "tokens": 100}],
                        "total_tokens": 100,
                    },
                    accept_action_id=ACTION_MEDIA_QUOTE_ACCEPT,
                    cancel_action_id=ACTION_MEDIA_QUOTE_CANCEL,
                ),
            },
        )
        add(
            "MediaQuoteMessage (Quote2 AI Translation after transcription)",
            "Quotes",
            {
                "type": "message",
                "blocks": media_quote_blocks(
                    {
                        "quote_id": JOB_UUID,
                        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
                        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
                        "file_name": "product-demo.mp4",
                        "line_items": [{"label": "AI Translation", "tokens": 50}],
                        "total_tokens": 50,
                    },
                    accept_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
                    cancel_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
                ),
            },
        )

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
        add(
            "MediaTranslationPartialMessage",
            "Video",
            MediaTranslationPartialMessage(["French", "Spanish"]),
        )
        add(
            "MediaEmbeddingPartialMessage",
            "Video",
            MediaEmbeddingPartialMessage(["German", "Japanese"]),
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
            "EvaluateAiOnlyCompleteMessage",
            "Quality",
            EvaluateAiOnlyCompleteMessage(eval_job),
        )
        add(
            "VerifyCompleteMessage",
            "Quality",
            VerifyCompleteMessage("Website Q1", "French"),
        )

        # ---- Help & Info ----
        add("HelpMessage", "Help", HelpMessage(mock_context))
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
        add(
            "MissingSlackFilesMessage (single)",
            "Errors",
            MissingSlackFilesMessage(
                [{"id": "F001", "title": "homepage.html"}],
            ),
        )
        add(
            "MissingSlackFilesMessage (multiple)",
            "Errors",
            MissingSlackFilesMessage(
                [
                    {"id": "F001", "title": "homepage.html"},
                    {"id": "F002", "title": "about-us.docx"},
                ],
            ),
        )
        add(
            "JobFileListEmptyMessage (in-progress)",
            "Errors",
            JobFileListEmptyMessage("TJ123456", "in-progress"),
        )
        add(
            "JobFileListEmptyMessage (completed)",
            "Errors",
            JobFileListEmptyMessage("TJ123456", "completed"),
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

    # ---- IBM-specific message variants ----
    # These templates call is_ibm_enterprise() internally; separate patch
    # context to avoid exceeding Python's static nesting limit.
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
        from app.slack.templates.messages import (
            DocumentMtQuoteMessage,
            HelpMessage,
            LoginMessage,
            SuccessfulLoginMessage,
            WelcomeBackMessage,
        )

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
        add(
            "DocumentMtQuoteMessage (IBM AI Translate direct quote)",
            "Quotes",
            DocumentMtQuoteMessage(make_document_mt_quote_session()),
        )
        add(
            "DocumentMtQuoteMessage (IBM AI Translate accepted)",
            "Quotes",
            DocumentMtQuoteMessage(
                make_document_mt_quote_session(accepted=True),
                actions=False,
            ),
        )
        ray_connection_ibm_admin = make_ray_connection(enable_verify=True)
        ray_connection_ibm_non_admin = make_ray_connection(enable_verify=False)

        add(
            "WelcomeBackMessage (IBM, admin)",
            "Auth",
            WelcomeBackMessage(
                USER_ID, ray_connection_ibm_admin, enterprise_id=ENTERPRISE_ID
            ),
        )
        add(
            "WelcomeBackMessage (IBM, non-admin)",
            "Auth",
            WelcomeBackMessage(
                USER_ID, ray_connection_ibm_non_admin, enterprise_id=ENTERPRISE_ID
            ),
        )
        add(
            "SuccessfulLoginMessage (IBM, admin)",
            "Auth",
            SuccessfulLoginMessage(
                USER_ID,
                "jane.doe@acme.com",
                ray_connection_ibm_admin,
                enterprise_id=ENTERPRISE_ID,
            ),
        )
        add(
            "SuccessfulLoginMessage (IBM, non-admin)",
            "Auth",
            SuccessfulLoginMessage(
                USER_ID,
                "jane.doe@acme.com",
                ray_connection_ibm_non_admin,
                enterprise_id=ENTERPRISE_ID,
            ),
        )

        def make_mock_ibm_context(ray_connection: RayConnection) -> MagicMock:
            mock_ibm_context = MagicMock()
            mock_ibm_context.__getitem__ = lambda self, key: {
                "user_id": USER_ID,
                "team_id": TEAM_ID,
                "channel_id": CHANNEL_ID,
                "enterprise_id": ENTERPRISE_ID,
            }.get(key, None)
            mock_ibm_context.get = lambda key, default=None: {
                "user_id": USER_ID,
                "team_id": TEAM_ID,
                "channel_id": CHANNEL_ID,
                "enterprise_id": ENTERPRISE_ID,
                "ray": ray_connection,
            }.get(key, default)
            mock_ibm_context.enterprise_id = ENTERPRISE_ID
            mock_ibm_context.ray = ray_connection
            return mock_ibm_context

        add(
            "HelpMessage (IBM, admin)",
            "Help",
            HelpMessage(make_mock_ibm_context(ray_connection_ibm_admin)),
        )
        add(
            "HelpMessage (IBM, non-admin)",
            "Help",
            HelpMessage(make_mock_ibm_context(ray_connection_ibm_non_admin)),
        )

    return entries


def make_home_context(enterprise_id: str | None = ENTERPRISE_ID) -> MagicMock:
    """Build the minimal Slack context needed by the real Home tab view."""
    context_data = {
        "user_id": USER_ID,
        "team_id": TEAM_ID,
        "channel_id": CHANNEL_ID,
        "enterprise_id": enterprise_id,
    }
    context = MagicMock()
    context.__getitem__.side_effect = context_data.__getitem__
    context.get.side_effect = context_data.get
    context.client = MagicMock()
    context.team_id = TEAM_ID
    context.enterprise_id = enterprise_id
    return context


def build_home_view(
    ray_connection: RayConnection | None,
    *,
    is_ibm: bool,
    is_admin: bool = False,
) -> dict[str, Any]:
    """Call the real async home_view with mocked async dependencies."""
    from app.slack.templates.views import home_view

    context = make_home_context(ENTERPRISE_ID if is_ibm else None)
    with (
        patch(
            "app.slack.templates.views.is_slack_team_admin",
            AsyncMock(return_value=is_admin),
        ),
        patch("app.slack.templates.views.get_pagination", AsyncMock(return_value=1)),
        patch(
            "app.slack.templates.views.get_full_group_translation_settings",
            AsyncMock(return_value=[]),
        ),
        patch("app.slack.templates.views.is_ibm_enterprise", return_value=is_ibm),
        patch("app.slack.templates.blocks.is_ibm_enterprise", return_value=is_ibm),
    ):
        view = home_view(context, "A_MOCK_APP_ID", ray_connection)
        return asyncio.run(view)


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
        patch(f"{BLK}.format_currency", side_effect=mock_format_currency),
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
            evaluation_ai_quote_adjust_modal,
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
            "evaluation_ai_quote_adjust_modal",
            "Modals",
            evaluation_ai_quote_adjust_modal(
                quote_id=JOB_UUID,
                quote_kind="evaluate",
                language_costs=[
                    {
                        "file_uuid": "file-uuid-001",
                        "file_label": "marketing-copy.docx",
                        "value": "lang-fr-uuid",
                        "label": "French",
                        "token": 25,
                    },
                    {
                        "file_uuid": "file-uuid-001",
                        "file_label": "marketing-copy.docx",
                        "value": "lang-es-uuid",
                        "label": "Spanish",
                        "token": 20,
                    },
                    {
                        "file_uuid": "file-uuid-002",
                        "file_label": "pricing.xlsx",
                        "value": "lang-fr-uuid",
                        "label": "French",
                        "token": 15,
                    },
                ],
                selected_pairs=[
                    "file-uuid-001:lang-fr-uuid",
                    "file-uuid-001:lang-es-uuid",
                ],
                ai_tokens=45,
                pdf_tokens=10,
                channel_id=CHANNEL_ID,
                message_ts="1234567890.123456",
            ),
        )
        add(
            "document_mt_job_modal",
            "Modals",
            document_mt_job_modal(CHANNEL_ID, FILE_INFO),
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
        ray_connection = make_ray_connection()
        add(
            "home_view (connected)",
            "Home",
            build_home_view(ray_connection, is_ibm=False),
        )

        add(
            "home_view (not connected)",
            "Home",
            build_home_view(None, is_ibm=False),
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
        ray_connection = make_ray_connection()
        entries.append(
            {
                "name": "home_view (IBM, connected, non-admin)",
                "category": "Home",
                **safe_extract(
                    build_home_view(ray_connection, is_ibm=True, is_admin=False)
                ),
            }
        )
        entries.append(
            {
                "name": "home_view (IBM, connected, admin)",
                "category": "Home",
                **safe_extract(
                    build_home_view(ray_connection, is_ibm=True, is_admin=True)
                ),
            }
        )

        entries.append(
            {
                "name": "home_view (IBM, not connected)",
                "category": "Home",
                **safe_extract(build_home_view(None, is_ibm=True)),
            }
        )

    return entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_catalog(
    language: str, catalog: dict[str, str] | None = None
) -> dict[str, Any]:
    configure_translation(language, catalog)
    messages = build_all_messages()
    views = build_all_views()
    return {
        "messages": messages,
        "views": views,
        "stats": {
            "total_messages": len(messages),
            "total_views": len(views),
            "total": len(messages) + len(views),
        },
    }


def parse_csv_values(value: str | None) -> list[str]:
    """Parse comma-separated values, preserving order and dropping empties."""
    if not value:
        return []
    items: list[str] = []
    seen: set[str] = set()
    for part in value.split(","):
        item = part.strip()
        if not item or item in seen:
            continue
        items.append(item)
        seen.add(item)
    return items


def entry_matches_filters(
    entry: dict[str, Any],
    *,
    match: re.Pattern[str] | None = None,
    names: set[str] | None = None,
    categories: set[str] | None = None,
) -> bool:
    """Return True when a catalog entry passes the active subset filters."""
    name = str(entry.get("name") or "")
    category = str(entry.get("category") or "")
    if names is not None and name not in names:
        return False
    if categories is not None and category not in categories:
        return False
    if match is not None and match.search(name) is None:
        return False
    return True


def filter_entries(
    entries: list[dict[str, Any]],
    *,
    match: str | None = None,
    names: list[str] | None = None,
    categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter catalog entries by regex name match, exact names, and/or category."""
    pattern = re.compile(match, re.IGNORECASE) if match else None
    name_set = set(names) if names else None
    category_set = set(categories) if categories else None
    if pattern is None and name_set is None and category_set is None:
        return entries
    return [
        entry
        for entry in entries
        if entry_matches_filters(
            entry,
            match=pattern,
            names=name_set,
            categories=category_set,
        )
    ]


def filter_catalog(
    catalog: dict[str, Any],
    *,
    match: str | None = None,
    names: list[str] | None = None,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Return a catalog copy limited to the requested template subset."""
    messages = filter_entries(
        catalog.get("messages") or [],
        match=match,
        names=names,
        categories=categories,
    )
    views = filter_entries(
        catalog.get("views") or [],
        match=match,
        names=names,
        categories=categories,
    )
    return {
        **catalog,
        "messages": messages,
        "views": views,
        "stats": {
            "total_messages": len(messages),
            "total_views": len(views),
            "total": len(messages) + len(views),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Block Kit JSON for Slack UI templates."
    )
    parser.add_argument(
        "--language",
        help="Single UI language to export. Defaults to UI_EXPORT_LANGUAGE or en.",
    )
    parser.add_argument(
        "--languages",
        help=(
            "Comma-separated UI languages to export. Overrides --language and "
            "UI_EXPORT_LANGUAGE. Defaults to UI_EXPORT_LANGUAGES when set."
        ),
    )
    parser.add_argument(
        "--translations-file",
        type=Path,
        default=(
            Path(os.environ["UI_EXPORT_TRANSLATIONS_FILE"])
            if os.environ.get("UI_EXPORT_TRANSLATIONS_FILE")
            else None
        ),
        help=(
            "Optional JSON translation catalog. Supports either "
            "{language: {source: translation}} or {source: translation}."
        ),
    )
    parser.add_argument(
        "--translation-source",
        choices=("catalog", "app", "db", "database"),
        default=TRANSLATION_SOURCE,
        help=(
            "Translation source for app.translate._ calls. 'catalog' uses the "
            "optional JSON file; 'app'/'db' uses the real DB-backed app translator."
        ),
    )
    parser.add_argument(
        "--match",
        help=(
            "Case-insensitive regex matched against template names. Use with "
            "--category/--names to export only templates under active work."
        ),
    )
    parser.add_argument(
        "--names",
        help="Comma-separated exact template names to include.",
    )
    parser.add_argument(
        "--category",
        help="Comma-separated catalog categories to include (e.g. Quotes,Modals).",
    )
    parser.add_argument(
        "--list-names",
        action="store_true",
        help="Print matching template names and exit without writing JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "output" / "blocks.json",
        help="Path to write the generated JSON file.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    language_value = (
        args.languages
        or os.environ.get("UI_EXPORT_LANGUAGES")
        or args.language
        or os.environ.get("UI_EXPORT_LANGUAGE")
        or DEFAULT_LANGUAGE
    )
    languages = parse_languages(language_value)
    match = args.match or os.environ.get("UI_EXPORT_MATCH")
    names = parse_csv_values(args.names or os.environ.get("UI_EXPORT_NAMES"))
    categories = parse_csv_values(args.category or os.environ.get("UI_EXPORT_CATEGORY"))
    subset_active = bool(match or names or categories)
    print(f"Generating Block Kit JSON for languages: {', '.join(languages)}...")
    if subset_active:
        print(
            "  Subset filters:"
            f" match={match!r}"
            f" names={names or None}"
            f" categories={categories or None}"
        )

    use_app_translator = args.translation_source in APP_TRANSLATION_SOURCES
    catalogs = {}
    for language in languages:
        translation_catalog = (
            {}
            if use_app_translator
            else load_translation_catalog(args.translations_file, language)
        )
        catalog = build_catalog(language, translation_catalog)
        catalogs[language] = filter_catalog(
            catalog,
            match=match,
            names=names or None,
            categories=categories or None,
        )

    default_language = languages[0]
    default_catalog = catalogs[default_language]

    if args.list_names:
        names_out = [
            f"{entry['category']}: {entry['name']}"
            for kind in ("messages", "views")
            for entry in default_catalog.get(kind) or []
        ]
        print("\n".join(names_out) if names_out else "(no matching templates)")
        print(f"\n{len(names_out)} matching templates")
        return

    if subset_active and default_catalog["stats"]["total"] == 0:
        raise SystemExit(
            "No templates matched the subset filters "
            f"(match={match!r}, names={names or None}, "
            f"categories={categories or None})."
        )

    output = {
        "generated_at": datetime.now().isoformat(),
        "default_language": default_language,
        "languages": languages,
        "catalogs": catalogs,
        # Keep the legacy shape for tools/tests that read output/blocks.json directly.
        "messages": default_catalog["messages"],
        "views": default_catalog["views"],
        "stats": default_catalog["stats"],
    }
    if subset_active:
        output["subset"] = {
            "match": match,
            "names": names,
            "categories": categories,
        }

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, default=str))

    label = "subset" if subset_active else "full"
    print(f"Generated {output['stats']['total']} templates ({label}) -> {output_path}")
    print(f"  Default language: {default_language}")
    print(f"  Messages: {output['stats']['total_messages']}")
    print(f"  Views:    {output['stats']['total_views']}")


if __name__ == "__main__":
    main()
