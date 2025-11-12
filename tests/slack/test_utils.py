from unittest.mock import MagicMock

from app.slack.utils import (
    escape_slack_emoji,
    format_strings_display,
    is_channel_im,
    replace_xtag,
    segment_quality_score,
    strip_slack_formatting,
    unescape_slack_emoji,
    unformat_links,
)


class TestIsChannelIm:
    """Tests for is_channel_im function."""

    def test_is_channel_im_with_d_prefix(self):
        """Test that channel IDs starting with 'D' are identified as IM."""
        assert is_channel_im("D1234567890") is True

    def test_is_channel_im_with_u_prefix(self):
        """Test that channel IDs starting with 'U' are identified as IM."""
        assert is_channel_im("U1234567890") is True

    def test_is_channel_im_with_w_prefix(self):
        """Test that channel IDs starting with 'W' are identified as IM."""
        assert is_channel_im("W1234567890") is True

    def test_is_channel_im_with_c_prefix(self):
        """Test that channel IDs starting with 'C' are NOT identified as IM."""
        assert is_channel_im("C1234567890") is False

    def test_is_channel_im_with_none(self):
        """Test that None returns False."""
        assert is_channel_im(None) is False

    def test_is_channel_im_with_empty_string(self):
        """Test that empty string returns False."""
        assert is_channel_im("") is False


class TestFormatStringsDisplay:
    """Tests for format_strings_display function."""

    def test_format_strings_display_empty_list(self):
        """Test formatting an empty list."""
        assert format_strings_display([]) == ""

    def test_format_strings_display_single_item(self):
        """Test formatting a single item."""
        assert format_strings_display(["English"]) == "English"

    def test_format_strings_display_two_items(self):
        """Test formatting two items."""
        assert format_strings_display(["English", "French"]) == "English & French"

    def test_format_strings_display_multiple_items(self):
        """Test formatting multiple items."""
        result = format_strings_display(["English", "French", "Spanish"])
        assert result == "English, French, & Spanish"  # Oxford comma style

    def test_format_strings_display_custom_and_string(self):
        """Test formatting with custom 'and' string."""
        result = format_strings_display(["English", "French"], and_string="and")
        assert result == "English and French"

    def test_format_strings_display_four_items(self):
        """Test formatting four items."""
        result = format_strings_display(["English", "French", "Spanish", "German"])
        assert result == "English, French, Spanish, & German"  # Oxford comma style


class TestStripSlackFormatting:
    """Tests for strip_slack_formatting function."""

    def test_strip_slack_formatting_empty_string(self):
        """Test stripping formatting from empty string."""
        assert strip_slack_formatting("") == ""

    def test_strip_slack_formatting_none(self):
        """Test stripping formatting from None."""
        assert strip_slack_formatting(None) == ""

    def test_strip_slack_formatting_user_mention(self):
        """Test removing user mentions."""
        text = "Hello <@U1234567890>!"
        result = strip_slack_formatting(text)
        assert "@U1234567890" not in result
        assert "Hello" in result

    def test_strip_slack_formatting_channel_mention(self):
        """Test removing channel mentions."""
        text = "Check out <#C1234567890>!"
        result = strip_slack_formatting(text)
        assert "#C1234567890" not in result
        assert "Check out" in result

    def test_strip_slack_formatting_workspace_mention(self):
        """Test removing workspace mentions."""
        text = "Message <@W1234567890>!"
        result = strip_slack_formatting(text)
        assert "@W1234567890" not in result

    def test_strip_slack_formatting_preserves_plain_text(self):
        """Test that plain text is preserved."""
        text = "This is plain text without formatting"
        assert strip_slack_formatting(text) == "This is plain text without formatting"


class TestUnformatLinks:
    """Tests for unformat_links function."""

    def test_unformat_links_empty_string(self):
        """Test unformatting links from empty string."""
        assert unformat_links("") == ""

    def test_unformat_links_none(self):
        """Test unformatting links from None."""
        assert unformat_links(None) == ""

    def test_unformat_links_simple_url(self):
        """Test unformatting a simple URL."""
        text = "<https://example.com>"
        assert unformat_links(text) == "https://example.com"

    def test_unformat_links_with_text(self):
        """Test unformatting a link with text."""
        text = "<https://example.com|Click here>"
        assert unformat_links(text) == "Click here"

    def test_unformat_links_multiple_links(self):
        """Test unformatting multiple links."""
        text = "<https://example.com|Link1> and <https://test.com|Link2>"
        result = unformat_links(text)
        assert "Link1" in result
        assert "Link2" in result
        assert "https://example.com" not in result
        assert "https://test.com" not in result

    def test_unformat_links_html_encoded(self):
        """Test unformatting HTML-encoded links."""
        text = "&lt;<https://example.com>|Link&gt;"
        result = unformat_links(text)
        assert "Link" in result
        # The function converts &lt; and &gt; to < and >, but removes link formatting
        # So we just verify Link is extracted correctly
        assert "Link" == result.strip()

    def test_unformat_links_preserves_plain_text(self):
        """Test that plain text without links is preserved."""
        text = "This is plain text"
        assert unformat_links(text) == "This is plain text"


class TestEscapeSlackEmoji:
    """Tests for escape_slack_emoji function."""

    def test_escape_slack_emoji_simple(self):
        """Test escaping a simple emoji."""
        text = "Hello :wave: world"
        result = escape_slack_emoji(text)
        assert ":wave:" not in result
        assert "<x i=" in result

    def test_escape_slack_emoji_multiple(self):
        """Test escaping multiple emojis."""
        text = ":smile: Hello :wave: world :heart:"
        result = escape_slack_emoji(text)
        # Should have 3 emoji placeholders
        assert result.count("<x i=") == 3

    def test_escape_slack_emoji_no_emoji(self):
        """Test text without emojis."""
        text = "Hello world"
        assert escape_slack_emoji(text) == "Hello world"

    def test_escape_slack_emoji_custom_emoji(self):
        """Test escaping custom emojis."""
        text = "Hello :custom_emoji: world"
        result = escape_slack_emoji(text)
        assert ":custom_emoji:" not in result
        assert "<x i=" in result


class TestUnescapeSlackEmoji:
    """Tests for unescape_slack_emoji function."""

    def test_unescape_slack_emoji_simple(self):
        """Test unescaping a simple emoji."""
        source = "Hello :wave: world"
        translated = "Hola <x i=0/> mundo"
        result = unescape_slack_emoji(translated, source)
        assert ":wave:" in result
        assert "<x i=0/>" not in result

    def test_unescape_slack_emoji_multiple(self):
        """Test unescaping multiple emojis."""
        source = ":smile: Hello :wave: world"
        translated = "<x i=0/> Hola <x i=1/> mundo"
        result = unescape_slack_emoji(translated, source)
        assert ":smile:" in result
        assert ":wave:" in result
        assert "<x i=" not in result

    def test_unescape_slack_emoji_no_placeholders(self):
        """Test text without placeholders."""
        source = "Hello world"
        translated = "Hola mundo"
        assert unescape_slack_emoji(translated, source) == "Hola mundo"

    def test_unescape_slack_emoji_with_spaces(self):
        """Test unescaping with spaces in placeholder."""
        source = "Hello :wave: world"
        translated = "Hola <x i = 0 /> mundo"
        result = unescape_slack_emoji(translated, source)
        assert ":wave:" in result


class TestReplaceXtag:
    """Tests for replace_xtag function."""

    def test_replace_xtag_with_number(self):
        """Test replacing x tag with a number."""
        match = MagicMock()
        match.group.return_value = "0"
        result = replace_xtag(match)
        assert result == "<x i=0/>"

    def test_replace_xtag_with_different_number(self):
        """Test replacing x tag with different number."""
        match = MagicMock()
        match.group.return_value = "5"
        result = replace_xtag(match)
        assert result == "<x i=5/>"


class TestSegmentQualityScore:
    """Tests for segment_quality_score function."""

    def test_segment_quality_score_v2_best(self):
        """Test TAUS QE 2.0.0 with best score."""
        result = segment_quality_score(0.95, "2.0.0")
        assert "Best" in result

    def test_segment_quality_score_v2_good(self):
        """Test TAUS QE 2.0.0 with good score."""
        result = segment_quality_score(0.89, "2.0.0")
        assert "Good" in result

    def test_segment_quality_score_v2_acceptable(self):
        """Test TAUS QE 2.0.0 with acceptable score."""
        result = segment_quality_score(0.85, "2.0.0")
        assert "Acceptable" in result

    def test_segment_quality_score_v2_bad(self):
        """Test TAUS QE 2.0.0 with bad score."""
        result = segment_quality_score(0.75, "2.0.0")
        assert "Bad" in result

    def test_segment_quality_score_v1_best(self):
        """Test TAUS QE 1.0.0 with best score."""
        result = segment_quality_score(0.98, "1.0.0")
        assert "Best" in result

    def test_segment_quality_score_v1_good(self):
        """Test TAUS QE 1.0.0 with good score."""
        result = segment_quality_score(0.92, "1.0.0")
        assert "Good" in result

    def test_segment_quality_score_v1_acceptable(self):
        """Test TAUS QE 1.0.0 with acceptable score."""
        result = segment_quality_score(0.87, "1.0.0")
        assert "Acceptable" in result

    def test_segment_quality_score_v1_bad(self):
        """Test TAUS QE 1.0.0 with bad score."""
        result = segment_quality_score(0.80, "1.0.0")
        assert "Bad" in result

    def test_segment_quality_score_default_version(self):
        """Test with default version (1.0.0)."""
        result = segment_quality_score(0.98)
        assert "Best" in result

    def test_segment_quality_score_boundary_values_v2(self):
        """Test boundary values for v2."""
        assert "Best" in segment_quality_score(0.90, "2.0.0")
        assert "Good" in segment_quality_score(0.88, "2.0.0")
        assert "Acceptable" in segment_quality_score(0.80, "2.0.0")
        assert "Bad" in segment_quality_score(0.79, "2.0.0")

    def test_segment_quality_score_boundary_values_v1(self):
        """Test boundary values for v1."""
        assert "Best" in segment_quality_score(0.95, "1.0.0")
        assert "Good" in segment_quality_score(0.90, "1.0.0")
        assert "Acceptable" in segment_quality_score(0.85, "1.0.0")
        assert "Bad" in segment_quality_score(0.84, "1.0.0")
