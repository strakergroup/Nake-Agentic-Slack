"""Tests for app/mt/translate.py - Language resolution functions."""

from unittest.mock import MagicMock, patch

import pytest

from app.mt.translate import resolve_language, resolve_language_code


class TestResolveLanguageCode:
    """Tests for resolve_language_code function."""

    @patch("app.mt.translate.Session")
    @patch("app.mt.translate.engines")
    def test_resolve_language_code_by_bcp47(self, mock_engines, mock_session_class):
        """Test resolving language by BCP-47 code."""
        mock_language = MagicMock()
        mock_language.bcp_47 = "en-US"
        mock_language.label = "English"
        mock_language.code = "en"
        mock_language.google_code = "en"

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.first.return_value = mock_language
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_engines.__getitem__.return_value = "engine"

        result = resolve_language_code("en-US")

        assert result == mock_language
        mock_session.close.assert_called_once()

    @patch("app.mt.translate.Session")
    @patch("app.mt.translate.engines")
    def test_resolve_language_code_by_label(self, mock_engines, mock_session_class):
        """Test resolving language by label."""
        mock_language = MagicMock()
        mock_language.bcp_47 = "en-US"
        mock_language.label = "English"
        mock_language.code = "en"
        mock_language.google_code = "en"

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.first.return_value = mock_language
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_engines.__getitem__.return_value = "engine"

        result = resolve_language_code("English")

        assert result == mock_language

    @patch("app.mt.translate.Session")
    @patch("app.mt.translate.engines")
    def test_resolve_language_code_by_code(self, mock_engines, mock_session_class):
        """Test resolving language by code."""
        mock_language = MagicMock()
        mock_language.bcp_47 = "en-US"
        mock_language.label = "English"
        mock_language.code = "en"
        mock_language.google_code = "en"

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.first.return_value = mock_language
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_engines.__getitem__.return_value = "engine"

        result = resolve_language_code("en")

        assert result == mock_language

    @patch("app.mt.translate.Session")
    @patch("app.mt.translate.engines")
    def test_resolve_language_code_not_found(self, mock_engines, mock_session_class):
        """Test resolving language that doesn't exist."""
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.first.return_value = None
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_engines.__getitem__.return_value = "engine"

        result = resolve_language_code("nonexistent")

        assert result is None

    @patch("app.mt.translate.Session")
    @patch("app.mt.translate.engines")
    def test_resolve_language_code_with_hyphen_fallback(
        self, mock_engines, mock_session_class
    ):
        """Test resolving language with hyphen uses fallback search."""
        mock_language = MagicMock()
        mock_language.bcp_47 = "fr-CA"
        mock_language.label = "French (Canada)"
        mock_language.code = "fr"
        mock_language.google_code = "fr"

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        # First query returns None
        # Second query (fallback) returns language
        mock_filter.first.side_effect = [None, mock_language]
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_engines.__getitem__.return_value = "engine"

        result = resolve_language_code("french-canada")

        assert result == mock_language

    def test_resolve_language_code_none_input(self):
        """Test resolving language with None input."""
        result = resolve_language_code(None)

        assert result is None


class TestResolveLanguage:
    """Tests for resolve_language function."""

    @patch("app.mt.translate.get_auto_translate_languages")
    @patch("app.mt.translate.resolve_language_code")
    def test_resolve_language_google_engine(self, mock_resolve_code, mock_get_langs):
        """Test resolving languages for Google engine."""
        mock_get_langs.return_value = {"fr": "French", "es": "Spanish"}
        mock_lang = MagicMock()
        mock_lang.google_code = "de"
        mock_resolve_code.return_value = mock_lang

        result = resolve_language(["fr", "de"], "google")

        assert result == ["fr", "de"]  # fr is in auto-translate dict, de is resolved

    @patch("app.mt.translate.get_auto_translate_languages")
    @patch("app.mt.translate.resolve_language_code")
    def test_resolve_language_microsoft_engine(self, mock_resolve_code, mock_get_langs):
        """Test resolving languages for Microsoft engine."""
        mock_get_langs.return_value = {"fr": "French"}
        mock_lang = MagicMock()
        mock_lang.bcp_47 = "fr-CA"
        mock_resolve_code.return_value = mock_lang

        result = resolve_language(["french"], "microsoft")

        assert result == ["fr-CA"]  # Uses bcp_47 for Microsoft

    @patch("app.mt.translate.get_auto_translate_languages")
    @patch("app.mt.translate.resolve_language_code")
    def test_resolve_language_microsoft_no_bcp47(
        self, mock_resolve_code, mock_get_langs
    ):
        """Test resolving languages for Microsoft engine without bcp_47."""
        mock_get_langs.return_value = {}
        mock_lang = MagicMock()
        mock_lang.bcp_47 = None
        mock_lang.shortname = "fr"
        mock_resolve_code.return_value = mock_lang

        with patch("app.mt.translate.langcodes.get") as mock_langcodes:
            mock_langcodes.return_value.language = "fr"
            result = resolve_language(["french"], "microsoft")

            assert result == ["fr"]

    @patch("app.mt.translate.get_auto_translate_languages")
    @patch("app.mt.translate.resolve_language_code")
    def test_resolve_language_no_results(self, mock_resolve_code, mock_get_langs):
        """Test resolving languages with no results defaults to English."""
        mock_get_langs.return_value = {}
        mock_resolve_code.return_value = None

        result = resolve_language(["nonexistent"], "google")

        assert result == ["en"]  # Default fallback

    @patch("app.mt.translate.get_auto_translate_languages")
    @patch("app.mt.translate.resolve_language_code")
    def test_resolve_language_microsoft_not_in_dict(
        self, mock_resolve_code, mock_get_langs
    ):
        """Test resolving languages for Microsoft when not in auto-translate dict."""
        mock_get_langs.return_value = {"fr": "French"}
        mock_lang = MagicMock()
        mock_lang.bcp_47 = "es-ES"
        mock_resolve_code.return_value = mock_lang

        result = resolve_language(["spanish"], "microsoft")

        assert result == ["es-ES"]
