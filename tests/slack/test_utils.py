from app.slack.utils import (
    escape_slack_emoji,
    format_strings_display,
    is_channel_im,
    segment_quality_score,
    split_text_into_blocks,
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
        assert "<img id='0'/>" in result
        assert result == "Hello <img id='0'/> world"

    def test_escape_slack_emoji_multiple(self):
        """Test escaping multiple emojis."""
        text = ":smile: Hello :wave: world :heart:"
        result = escape_slack_emoji(text)
        # Should have 3 indexed placeholders
        assert "<img id='0'/>" in result
        assert "<img id='1'/>" in result
        assert "<img id='2'/>" in result
        assert result == "<img id='0'/> Hello <img id='1'/> world <img id='2'/>"

    def test_escape_slack_emoji_no_emoji(self):
        """Test text without emojis."""
        text = "Hello world"
        assert escape_slack_emoji(text) == "Hello world"

    def test_escape_slack_emoji_custom_emoji(self):
        """Test escaping custom emojis."""
        text = "Hello :custom_emoji: world"
        result = escape_slack_emoji(text)
        assert "<img id='0'/>" in result

    def test_escape_slack_emoji_slack_special_tags(self):
        """Test escaping Slack special format tags like user mentions."""
        text = "Hello <@U123456> world"
        result = escape_slack_emoji(text)
        assert "<img id='0'/>" in result
        assert result == "Hello <img id='0'/> world"


class TestUnescapeSlackEmoji:
    """Tests for unescape_slack_emoji function."""

    def test_unescape_slack_emoji_simple(self):
        """Test unescaping a simple emoji."""
        source = "Hello :wave: world"
        translated = "Hola <img id='0'/> mundo"
        result = unescape_slack_emoji(translated, source)
        assert ":wave:" in result
        assert "<img" not in result
        assert result == "Hola :wave: mundo"

    def test_unescape_slack_emoji_multiple(self):
        """Test unescaping multiple emojis."""
        source = ":smile: Hello :wave: world"
        translated = "<img id='0'/> Hola <img id='1'/> mundo"
        result = unescape_slack_emoji(translated, source)
        assert ":smile:" in result
        assert ":wave:" in result
        assert "<img" not in result

    def test_unescape_slack_emoji_no_placeholders(self):
        """Test text without placeholders."""
        source = "Hello world"
        translated = "Hola mundo"
        assert unescape_slack_emoji(translated, source) == "Hola mundo"

    def test_unescape_slack_emoji_with_spaces(self):
        """Test unescaping with spaces in img tag (API whitespace variations)."""
        source = "Hello :wave: world"
        translated = 'Hola < img id = "0" /> mundo'
        result = unescape_slack_emoji(translated, source)
        assert ":wave:" in result
        assert "<img" not in result

    def test_unescape_slack_emoji_single_quotes(self):
        """Test unescaping with single quotes in attribute."""
        source = "Hello :wave: world"
        translated = "Hola <img id='0'/> mundo"
        result = unescape_slack_emoji(translated, source)
        assert result == "Hola :wave: mundo"

    def test_unescape_slack_emoji_no_quotes(self):
        """Test unescaping without quotes in attribute."""
        source = "Hello :wave: world"
        translated = "Hola <img id=0/> mundo"
        result = unescape_slack_emoji(translated, source)
        assert result == "Hola :wave: mundo"

    def test_unescape_slack_emoji_user_mention(self):
        """Test unescaping user mentions from indexed placeholders."""
        source = "Hello <@U123456> world"
        translated = "Hola <img id='0'/> mundo"
        result = unescape_slack_emoji(translated, source)
        assert result == "Hola <@U123456> mundo"

    def test_unescape_slack_emoji_channel_mention(self):
        """Test unescaping channel mentions from indexed placeholders."""
        source = "Check <#C123456|general> channel"
        translated = "Mira <img id='0'/> canal"
        result = unescape_slack_emoji(translated, source)
        assert result == "Mira <#C123456|general> canal"

    def test_unescape_slack_emoji_link(self):
        """Test unescaping links from indexed placeholders."""
        source = "Visit <https://example.com> now"
        translated = "Visita <img id='0'/> ahora"
        result = unescape_slack_emoji(translated, source)
        assert result == "Visita <https://example.com> ahora"

    def test_unescape_slack_emoji_mixed_content(self):
        """Test unescaping mixed emoji and user mentions."""
        source = ":wave: Hello <@U123456> :smile:"
        translated = "<img id='0'/> Hola <img id='1'/> <img id='2'/>"
        result = unescape_slack_emoji(translated, source)
        assert result == ":wave: Hola <@U123456> :smile:"

    def test_unescape_slack_emoji_restores_stripped_spacing(self):
        """Test that original spacing is restored when translation strips spaces around placeholder."""
        source = "Hello :wave: world"
        translated = (
            "hello<img id='0'/>world"  # Translation stripped spaces around placeholder
        )
        result = unescape_slack_emoji(translated, source)
        assert result == "hello :wave: world"  # Original spacing restored around emoji


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


class TestSplitTextIntoBlocks:
    """Tests for split_text_into_blocks function."""

    def test_text_within_limit(self):
        """Test that text within the limit returns a single chunk."""
        text = "This is a short text."
        result = split_text_into_blocks(text, max_length=100)
        assert result == [text]
        assert len(result) == 1

    def test_empty_string(self):
        """Test that empty string returns a single empty chunk."""
        result = split_text_into_blocks("")
        assert result == [""]
        assert len(result) == 1

    def test_text_exceeds_limit_single_line(self):
        """Test that text exceeding limit on a single line is split."""
        text = "a" * 5000
        result = split_text_into_blocks(text, max_length=1000)
        assert len(result) == 5
        assert all(len(chunk) <= 1000 for chunk in result)
        assert "".join(result) == text

    def test_text_exceeds_limit_multiple_lines(self):
        """Test that text exceeding limit across multiple lines is split."""
        text = "\n".join([f"Line {i}" for i in range(100)])
        result = split_text_into_blocks(text, max_length=50)
        assert len(result) > 1
        # Verify all chunks are within limit
        assert all(len(chunk) <= 50 for chunk in result)
        # Verify original text can be reconstructed (character-based splitting preserves all content)
        assert "".join(result) == text

    def test_first_chunk_limit(self):
        """Test that first_chunk_limit is respected for the first chunk."""
        text = "a" * 2000 + "\n" + "b" * 2000
        result = split_text_into_blocks(text, max_length=1500, first_chunk_limit=1000)
        assert len(result) >= 2
        # First chunk should respect first_chunk_limit
        assert len(result[0]) <= 1000
        # Subsequent chunks should respect max_length
        assert all(len(chunk) <= 1500 for chunk in result[1:])
        # Verify all content is preserved
        assert "".join(result) == text

    def test_first_chunk_limit_none(self):
        """Test that when first_chunk_limit is None, max_length is used for all chunks."""
        text = "a" * 2000 + "\n" + "b" * 2000
        result = split_text_into_blocks(text, max_length=1500, first_chunk_limit=None)
        assert len(result) >= 2
        # All chunks should respect max_length
        assert all(len(chunk) <= 1500 for chunk in result)
        # Verify all content is preserved
        assert "".join(result) == text

    def test_single_line_exceeds_limit(self):
        """Test that a single line exceeding limit is split mid-line."""
        long_line = "a" * 5000
        text = f"Short line\n{long_line}\nAnother short line"
        result = split_text_into_blocks(text, max_length=1000)
        # Should have multiple chunks because of the long line
        assert len(result) > 1
        assert all(len(chunk) <= 1000 for chunk in result)
        # Verify all content is preserved (all characters present)
        # Note: newlines around split lines are not preserved, but content is
        all_content = "".join(result)
        assert "Short line" in all_content
        assert long_line in all_content
        assert "Another short line" in all_content
        assert len(all_content.replace("\n", "")) == len(text.replace("\n", ""))

    def test_exact_limit(self):
        """Test text that is exactly at the limit."""
        text = "a" * 3000
        result = split_text_into_blocks(text, max_length=3000)
        assert result == [text]
        assert len(result) == 1

    def test_exact_limit_plus_one(self):
        """Test text that is one character over the limit."""
        text = "a" * 3001
        result = split_text_into_blocks(text, max_length=3000)
        assert len(result) == 2
        assert len(result[0]) == 3000
        assert len(result[1]) == 1
        assert "".join(result) == text

    def test_first_chunk_limit_exact(self):
        """Test that first chunk respects first_chunk_limit exactly."""
        text = "a" * 2000
        result = split_text_into_blocks(text, max_length=3000, first_chunk_limit=1000)
        assert len(result) == 2
        assert len(result[0]) == 1000
        assert len(result[1]) == 1000
        assert "".join(result) == text

    def test_first_chunk_limit_with_multiple_chunks(self):
        """Test first_chunk_limit when multiple chunks are needed."""
        # Create text that needs 3 chunks: first with limit 500, rest with limit 1000
        text = "a" * 400 + "\n" + "b" * 800 + "\n" + "c" * 800
        result = split_text_into_blocks(text, max_length=1000, first_chunk_limit=500)
        assert len(result) >= 2
        assert len(result[0]) <= 500
        assert all(len(chunk) <= 1000 for chunk in result[1:])
        # Verify all content is preserved
        assert "".join(result) == text

    def test_preserves_line_boundaries(self):
        """Test that text is split correctly (character-based splitting)."""
        lines = ["Line 1", "Line 2", "Line 3", "Line 4"]
        text = "\n".join(lines)
        # Set limit so that 2 lines fit but not 3
        result = split_text_into_blocks(text, max_length=15)
        # Verify all chunks are within limit
        assert all(len(chunk) <= 15 for chunk in result)
        # Verify all content is preserved (character-based splitting preserves all characters)
        assert "".join(result) == text

    def test_very_long_single_line_with_first_chunk_limit(self):
        """Test a very long single line with first_chunk_limit."""
        long_line = "a" * 10000
        result = split_text_into_blocks(
            long_line, max_length=2000, first_chunk_limit=1000
        )
        assert len(result) >= 5  # At least 5 chunks (10000 / 2000)
        assert len(result[0]) == 1000  # First chunk respects first_chunk_limit
        assert all(len(chunk) <= 2000 for chunk in result[1:])
        assert "".join(result) == long_line

    def test_multiline_text_with_first_chunk_limit(self):
        """Test multiline text where first chunk has a different limit."""
        # Create text where first chunk needs smaller limit
        lines = ["Header: Very long line that exceeds first chunk limit" + "x" * 500]
        lines.extend([f"Line {i}: Content" for i in range(20)])
        text = "\n".join(lines)
        result = split_text_into_blocks(text, max_length=200, first_chunk_limit=100)
        assert len(result) > 1
        assert len(result[0]) <= 100
        assert all(len(chunk) <= 200 for chunk in result[1:])
        # Verify all content is preserved
        assert "".join(result) == text

    def test_newline_handling(self):
        """Test that newlines are properly handled and counted."""
        text = "Line1\nLine2\nLine3"
        result = split_text_into_blocks(text, max_length=10)
        # Should split, preserving all characters including newlines
        # Verify all content is preserved (newlines are preserved as characters)
        assert "".join(result) == text
        # Verify chunks are within limit
        assert all(len(chunk) <= 10 for chunk in result)

    def test_default_max_length(self):
        """Test that default max_length of 3000 is used."""
        text = "a" * 6000
        result = split_text_into_blocks(text)
        assert len(result) >= 2
        assert all(len(chunk) <= 3000 for chunk in result)

    def test_mixed_line_lengths(self):
        """Test text with mixed line lengths."""
        text = "\n".join(
            [
                "Short",
                "a" * 100,
                "Medium length line",
                "b" * 200,
                "Another short line",
            ]
        )
        result = split_text_into_blocks(text, max_length=150)
        assert len(result) > 1
        assert all(len(chunk) <= 150 for chunk in result)
        # Verify all content is preserved (character-based splitting preserves all characters)
        assert "".join(result) == text
