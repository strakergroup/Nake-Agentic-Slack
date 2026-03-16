import pytest
from pydantic import ValidationError

from app.slack.templates.models import (
    AutoTranslationSettingsForm,
    EvaluateJobForm,
    JobSearchForm,
    NewJobForm,
    RayLanguage,
    SlackFile,
    SsoLoginForm,
    convert_pydantic_to_slack_error,
)


class TestConvertPydanticToSlackError:
    """Tests for convert_pydantic_to_slack_error function."""

    def test_convert_pydantic_to_slack_error_single_error(self):
        """Test converting a single validation error."""
        try:
            RayLanguage(code="", name="")  # Empty code should fail
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            assert isinstance(errors, dict)
            assert len(errors) > 0

    def test_convert_pydantic_to_slack_error_multiple_errors(self):
        """Test converting multiple validation errors."""
        try:
            SsoLoginForm(email="invalid", firstName="ab", lastName="cd")
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            assert isinstance(errors, dict)
            # Should have errors for multiple fields

    def test_convert_pydantic_to_slack_error_removes_prefix(self):
        """Test that 'Value error, ' prefix is removed."""
        try:
            RayLanguage(code="", name="")
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            for error_msg in errors.values():
                assert not error_msg.startswith("Value error, ")


class TestRayLanguage:
    """Tests for RayLanguage class."""

    def test_ray_language_creation(self):
        """Test creating a RayLanguage instance."""
        lang = RayLanguage(code="en", name="English")
        assert lang.code == "en"
        assert lang.name == "English"

    def test_ray_language_parse_slack_option(self):
        """Test parsing from Slack option format."""
        option = {"value": "fr", "text": {"text": "French"}}
        lang = RayLanguage.parse_slack_option(option)
        assert lang.code == "fr"
        assert lang.name == "French"


class TestSlackFile:
    """Tests for SlackFile class."""

    def test_slack_file_creation(self):
        """Test creating a SlackFile instance."""
        file = SlackFile(id="file-123", title="test.txt")
        assert file.id == "file-123"
        assert file.title == "test.txt"

    def test_slack_file_parse_slack_option(self):
        """Test parsing from Slack option format."""
        option = {"value": "file-456", "text": {"text": "document.pdf"}}
        file = SlackFile.parse_slack_option(option)
        assert file.id == "file-456"
        assert file.title == "document.pdf"


class TestJobSearchForm:
    """Tests for JobSearchForm class."""

    def test_job_search_form_creation(self):
        """Test creating a JobSearchForm instance."""
        form = JobSearchForm(reference="TJ-123")
        assert form.reference == "TJ-123"

    def test_job_search_form_empty_reference(self):
        """Test JobSearchForm with empty reference."""
        form = JobSearchForm()
        assert form.reference == ""

    def test_job_search_form_parse_slack(self):
        """Test parsing from Slack payload."""
        values = {"reference": {"reference": {"value": "TJ-456"}}}
        form = JobSearchForm.parse_slack(values)
        assert form.reference == "TJ-456"

    def test_job_search_form_parse_slack_missing_key(self):
        """Test parsing with missing key raises ValueError."""
        values = {}
        with pytest.raises(ValueError, match="Slack payload format is incorrect"):
            JobSearchForm.parse_slack(values)


class TestNewJobForm:
    """Tests for NewJobForm class."""

    def test_new_job_form_workflow_translation(self):
        """Test workflow property for Translation service."""
        form = NewJobForm(
            files=[SlackFile(id="f1", title="test.txt")],
            source_lang=RayLanguage(code="en", name="English"),
            target_langs=[RayLanguage(code="fr", name="French")],
            service="Translation",
            timeframe="3",
        )
        assert form.workflow == "TRANSLATION"

    def test_new_job_form_workflow_translation_validation(self):
        """Test workflow property for Translation with validation."""
        form = NewJobForm(
            files=[SlackFile(id="f1", title="test.txt")],
            source_lang=RayLanguage(code="en", name="English"),
            target_langs=[RayLanguage(code="fr", name="French")],
            service="Translation",
            timeframe="3",
            validation=True,
        )
        assert form.workflow == "TRANSLATION_VALIDATION"

    def test_new_job_form_workflow_translation_edit(self):
        """Test workflow property for Translation + Edit service."""
        form = NewJobForm(
            files=[SlackFile(id="f1", title="test.txt")],
            source_lang=RayLanguage(code="en", name="English"),
            target_langs=[RayLanguage(code="fr", name="French")],
            service="Translation + Edit",
            timeframe="3",
        )
        assert form.workflow == "TRANSLATION_REVIEW"

    def test_new_job_form_workflow_translation_edit_validation(self):
        """Test workflow property for Translation + Edit with validation."""
        form = NewJobForm(
            files=[SlackFile(id="f1", title="test.txt")],
            source_lang=RayLanguage(code="en", name="English"),
            target_langs=[RayLanguage(code="fr", name="French")],
            service="Translation + Edit",
            timeframe="3",
            validation=True,
        )
        assert form.workflow == "TRANSLATION_REVIEW_VALIDATION"

    def test_new_job_form_workflow_invalid_service(self):
        """Test workflow property raises error for invalid service."""
        form = NewJobForm(
            files=[SlackFile(id="f1", title="test.txt")],
            source_lang=RayLanguage(code="en", name="English"),
            target_langs=[RayLanguage(code="fr", name="French")],
            service="Invalid Service",
            timeframe="3",
        )
        with pytest.raises(ValueError, match="Cannot get workflow"):
            _ = form.workflow

    def test_new_job_form_validate_target_langs_empty(self):
        """Test validation fails for empty target languages."""
        with pytest.raises(ValueError, match="At least one target language"):
            NewJobForm(
                files=[SlackFile(id="f1", title="test.txt")],
                source_lang=RayLanguage(code="en", name="English"),
                target_langs=[],
                service="Translation",
                timeframe="3",
            )

    def test_new_job_form_validate_target_langs_same_as_source(self):
        """Test validation fails when target language matches source."""
        with pytest.raises(ValueError, match="source language cannot be a target"):
            NewJobForm(
                files=[SlackFile(id="f1", title="test.txt")],
                source_lang=RayLanguage(code="en", name="English"),
                target_langs=[RayLanguage(code="en", name="English")],
                service="Translation",
                timeframe="3",
            )

    def test_new_job_form_validate_files_empty(self):
        """Test validation fails for empty files list."""
        with pytest.raises(ValueError, match="At least one file"):
            NewJobForm(
                files=[],
                source_lang=RayLanguage(code="en", name="English"),
                target_langs=[RayLanguage(code="fr", name="French")],
                service="Translation",
                timeframe="3",
            )


class TestSsoLoginForm:
    """Tests for SsoLoginForm class."""

    def test_sso_login_form_creation(self):
        """Test creating a SsoLoginForm instance."""
        form = SsoLoginForm(email="test@example.com", firstName="John", lastName="Doe")
        assert form.email == "test@example.com"
        assert form.firstName == "John"
        assert form.lastName == "Doe"

    def test_sso_login_form_parse_slack(self):
        """Test parsing from Slack payload."""
        values = {
            "email": {"email": {"value": "test@example.com"}},
            "firstName": {"firstName": {"value": "John"}},
            "lastName": {"lastName": {"value": "Doe"}},
        }
        form = SsoLoginForm.parse_slack(values)
        assert form.email == "test@example.com"
        assert form.firstName == "John"
        assert form.lastName == "Doe"

    def test_sso_login_form_parse_slack_missing_key(self):
        """Test parsing with missing key raises ValueError."""
        values = {"email": {"email": {"value": "test@example.com"}}}
        with pytest.raises(ValueError, match="Slack payload format is incorrect"):
            SsoLoginForm.parse_slack(values)


class TestAutoTranslationSettingsForm:
    """Tests for AutoTranslationSettingsForm class."""

    def test_auto_translation_settings_form_parse_slack(self):
        """Test parsing from Slack payload."""
        values = {
            "channels": {"channels": {"selected_conversations": ["C123", "C456"]}},
            "languages": {
                "languages": {
                    "selected_options": [
                        {"value": "en"},
                        {"value": "fr"},
                    ]
                }
            },
            "display_format": {
                "display_format": {"selected_option": {"value": "thread"}}
            },
        }
        form = AutoTranslationSettingsForm.parse_slack(values)
        assert form.channels == ["C123", "C456"]
        assert form.languages == ["en", "fr"]
        assert form.display_format == "thread"

    def test_auto_translation_settings_form_parse_slack_missing_key(self):
        """Test parsing with missing key raises ValueError."""
        values = {"channels": {"channels": {"selected_conversations": ["C123"]}}}
        with pytest.raises(ValueError, match="Slack payload format is incorrect"):
            AutoTranslationSettingsForm.parse_slack(values)


class TestEvaluateJobForm:
    """Tests for EvaluateJobForm class."""

    def test_evaluate_job_form_parse_human_job_form(self):
        """Test parsing human job form."""
        values = {
            "source_lang": {
                "source_language_option_uuid": {
                    "selected_option": {"value": "src-lang-001"}
                }
            },
            "target_langs": {
                "language_options_uuid": {
                    "selected_options": [{"value": "lang-123"}, {"value": "lang-456"}]
                }
            },
            "files": {
                "files": {
                    "selected_options": [
                        {"value": "file-123", "text": {"text": "test.txt"}}
                    ]
                }
            },
            "reference": {"reference": {"value": "REF-123"}},
        }
        form = EvaluateJobForm.parse_human_job_form(values, "evaluate_job_human")
        assert form.reference == "REF-123"
        assert form.source_lang_uuid == "src-lang-001"
        assert form.target_langs_uuid == ["lang-123", "lang-456"]
        assert len(form.files) == 1
        assert form.files[0].id == "file-123"

    def test_evaluate_job_form_parse_human_job_form_with_notes(self):
        """Test parsing human job form with job notes."""
        values = {
            "source_lang": {
                "source_language_option_uuid": {
                    "selected_option": {"value": "src-lang-001"}
                }
            },
            "target_langs": {
                "language_options_uuid": {"selected_options": [{"value": "lang-123"}]}
            },
            "files": {
                "files": {
                    "selected_options": [
                        {"value": "file-123", "text": {"text": "test.txt"}}
                    ]
                }
            },
            "reference": {"reference": {"value": "REF-123"}},
            "job_notes": {"job_notes": {"value": "Test notes"}},
        }
        form = EvaluateJobForm.parse_human_job_form(values, "evaluate_job_human")
        assert form.job_notes == "Test notes"
        assert form.source_lang_uuid == "src-lang-001"

    def test_evaluate_job_form_parse_slack(self):
        """Test parsing from Slack payload."""
        values = {
            "reference": {"reference": {"value": "REF-123"}},
            "source_lang": {
                "source_language_option_uuid": {
                    "selected_option": {"value": "src-lang-001"}
                }
            },
            "target_langs": {
                "language_options_uuid": {"selected_options": [{"value": "lang-123"}]}
            },
            "files": {
                "files": {
                    "selected_options": [
                        {"value": "file-123", "text": {"text": "test.txt"}}
                    ]
                }
            },
            "workflow_options": {
                "workflow_options": {"selected_option": {"value": "workflow-123"}}
            },
        }
        form = EvaluateJobForm.parse_slack(values)
        assert form.reference == "REF-123"
        assert form.source_lang_uuid == "src-lang-001"
        assert form.workflow_options == "workflow-123"

    def test_evaluate_job_form_parse_slack_no_workflow_options(self):
        """Test parsing without workflow options."""
        values = {
            "reference": {"reference": {"value": "REF-123"}},
            "source_lang": {
                "source_language_option_uuid": {
                    "selected_option": {"value": "src-lang-001"}
                }
            },
            "target_langs": {
                "language_options_uuid": {"selected_options": [{"value": "lang-123"}]}
            },
            "files": {
                "files": {
                    "selected_options": [
                        {"value": "file-123", "text": {"text": "test.txt"}}
                    ]
                }
            },
        }
        form = EvaluateJobForm.parse_slack(values)
        assert form.workflow_options is None
        assert form.source_lang_uuid == "src-lang-001"
