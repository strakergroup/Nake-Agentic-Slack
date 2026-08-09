"""Unit tests for IBM Slack HT service-account helpers (RAY-81247)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.ibm_ht_service_account import (
    DEFAULT_HT_SERVICE_ACCOUNT_MEMBER_UUID,
    custom_fields_form_value,
    requester_surrogate_custom_fields,
    should_use_ht_service_account,
)


def test_requester_surrogate_custom_fields():
    fields = requester_surrogate_custom_fields("poster@ibm.com")
    assert fields == [
        {"label": "Requester ID", "value": "poster@ibm.com"},
        {"label": "Surrogate ID", "value": "poster@ibm.com"},
    ]
    assert json.loads(custom_fields_form_value("poster@ibm.com")) == fields


def test_requester_surrogate_custom_fields_omits_blank_email():
    assert requester_surrogate_custom_fields("") == []
    assert requester_surrogate_custom_fields("   ") == []
    assert custom_fields_form_value("") == ""
    assert custom_fields_form_value("   ") == ""


def test_should_use_ht_service_account_ibm_with_super_group(monkeypatch):
    ray = MagicMock()
    ray.super_group = [MagicMock()]
    ray.client = None
    monkeypatch.setattr(
        "app.ibm_ht_service_account.is_ibm_enterprise",
        lambda enterprise_id: enterprise_id == "IBM",
    )
    assert should_use_ht_service_account("IBM", ray) is True
    assert should_use_ht_service_account("OTHER", ray) is False
    assert should_use_ht_service_account("IBM", None) is False
    ray.super_group = []
    assert should_use_ht_service_account("IBM", ray) is False


def test_should_use_ht_service_account_prefers_active_crm(monkeypatch):
    ray = MagicMock()
    ray.super_group = [MagicMock()]
    ray.client = MagicMock()  # active CRM member linked to Slack
    monkeypatch.setattr(
        "app.ibm_ht_service_account.is_ibm_enterprise",
        lambda enterprise_id: enterprise_id == "IBM",
    )
    assert should_use_ht_service_account("IBM", ray) is False


def test_default_service_account_uuid():
    assert DEFAULT_HT_SERVICE_ACCOUNT_MEMBER_UUID == (
        "D8CF434C-6FAD-496F-9A6B-C8EE6A0A066B"
    )
