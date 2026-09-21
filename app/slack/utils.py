import logging
import math
import re
from typing import Any

from app.translate import _
from app.slack.buglog_notifier import notify_exception

logger = logging.getLogger(__name__)

CALLBACK_ERROR_DETAIL_MAX_LENGTH = 500


def is_channel_im(channel_id: str | None) -> bool:
    """Check if a channel is an IM (direct message between the bot and a user)
    by looking at the channel ID. There may be false negatives, e.g. if the
    channel ID begins with "C". Also check that the channel_type is "im".

    Args:
        channel_id (str | None): The Slack channel ID.

    Returns:
        bool: The channel is an IM.
    """
    if not channel_id:
        return False
    return (
        channel_id.startswith("D")
        or channel_id.startswith("U")
        or channel_id.startswith("W")
    )


def format_strings_display(strings: list[str], *, and_string: str = "&") -> str:
    """Format a list of strings for display in human-readable form.

    E.g. English, French and Spanish.

    Args:
        strings (list[str]): The list of strings to format.
        and_string (str, optional): The "and" string to use. Defaults to "&".

    Returns:
        str: The formatted string.
    """
    if and_string == "&":
        and_string = _("&")
    elif and_string == "and":
        and_string = _("and")
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0]
    if len(strings) == 2:
        return f"{strings[0]} {and_string} {strings[1]}"
    return f"{', '.join(strings[:-1])}, {and_string} {strings[-1]}"


def safe_callback_error_detail(payload_error: Any) -> str:
    error_detail = " ".join(str(payload_error or "").split())
    if not error_detail:
        return _("Unknown error")
    return error_detail[:CALLBACK_ERROR_DETAIL_MAX_LENGTH]


def format_error_detail(template: str, error_detail: str) -> str:
    """Fill an error template without ever raising on mangled translations.

    A translated template whose placeholder was mangled (seen on UAT as
    ``{ "error"}``) falls back to the template prefix with the detail
    appended, so error reporting never hides the real error behind a 500.
    """
    try:
        return template.format(error_detail=error_detail)
    except (KeyError, IndexError, ValueError):
        notify_exception(
            Exception(f"Unformattable callback error template: {template!r}"),
            "Callback error template mangled",
        )
        return f"{template.split(':')[0]}: {error_detail}"


def format_callback_error(stage: str, payload_error: Any) -> str:
    error_detail = safe_callback_error_detail(payload_error)
    templates = {
        "transcription": _("Transcription failed: {error_detail}"),
        "translation": _("Translation failed: {error_detail}"),
        "embedding": _("Embedding failed: {error_detail}"),
    }
    template = templates.get(stage)
    if template is None:
        raise ValueError(f"Unsupported callback error stage: {stage}")
    return format_error_detail(template, error_detail)


def order_translations_by_target_language_order(
    translations: dict[str, Any],
    target_language_order: list[str] | None,
) -> dict[str, Any]:
    """Order translations by caller-requested target order, keeping leftovers."""
    if not target_language_order:
        return translations

    ordered_translations: dict[str, Any] = {}
    for target_language in target_language_order:
        if (
            target_language in translations
            and target_language not in ordered_translations
        ):
            ordered_translations[target_language] = translations[target_language]

    for target_language, translated_text in translations.items():
        if target_language not in ordered_translations:
            ordered_translations[target_language] = translated_text

    return ordered_translations


def strip_slack_formatting(text: str) -> str:
    """Strip Slack formatting from a string. Slack uses it's own special form
    of Markdown to format text. This function will remove some of the formatting,
    but is not perfect.

    See:
        https://api.slack.com/reference/surfaces/formatting
        https://api.slack.com/reference/surfaces/formatting#retrieving-messages

    Args:
        text (str): The original Slack text.

    Returns:
        str: The text with formatting stripped.
    """
    # TODO Unit test
    if not text:
        return ""
    # Remove user and channel mentions.
    text = re.sub(r"<(#C|@U|@W|!).*?>", "", text)
    # Links
    text = unformat_links(text)
    # Blockquote
    # text = re.sub(r"(^|\n)(>|&gt;)\s?", "\n", text).lstrip()
    # More if needed...
    return text.strip()


def unformat_links(text: str) -> str:
    """Convert links in a Slack text to plain text. Some text may be impossible
    to parse if it uses special characters, e.g. <, >, |.

    See:
        https://api.slack.com/reference/surfaces/formatting#retrieving-messages

    Args:
        text (str): The original Slack text.

    Returns:
        str: The text with links unformatted.
    """
    if not text:
        return ""
    # TODO Unit test
    # Slack returns weirdly formatted text if the link text contains <, >, |.
    # <a (link) -> &lt;<https://example.com>|&lt;a&gt;
    # a> (link) -> <http://example.com|a>&gt;
    # <a> (link) -> &lt;<https://example.com>|&lt;a&gt;&gt;
    # a<b>c (link) -> &lt;<https://example.com>|a&lt;b&gt;c&gt; (cannot parse)

    # <https://example.com> -> https://example.com
    text = re.sub(r"<(http[^|>]+)>", r"\1", text)
    # &lt;https://example.com|abc&gt; -> abc
    # &lt;https://example.com|ab|cd&gt; -> ab|cd
    # https://stackoverflow.com/questions/406230/regular-expression-to-match-a-line-that-doesnt-contain-a-word
    text = re.sub(r"&lt;(?:(?!&gt;)[^|])+\|(.+?)&gt;", r"\1", text)
    # <https://example.com|abc> -> abc
    # <https://example.com|ab|cd> > ab|cd
    text = re.sub(r"<[^|>]+\|(.+?)>", r"\1", text)
    # Convert &lt; and &gt; to < and >
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return text


def escape_slack_emoji(text: str) -> str:
    """Escape Slack emoji and special tags by replacing them with indexed img placeholders.
    Preserves surrounding whitespace in the escaped output.

    Args:
        text (str): The text to escape.

    Returns:
        str: The text with Slack emoji and special tags replaced with <img id="N"/> placeholders.
    """
    # Slack uses :emoji: syntax for emoji and <@U123>, <#C123>, <https://...> for mentions/links.
    # Capture optional surrounding whitespace to preserve spacing in escaped output.
    pattern = r"( ?)(:[^\s]*?:|<[^\s]*>)( ?)"

    counter = [0]

    def replacer(m: re.Match) -> str:
        pre, _, post = m.groups()
        idx = counter[0]
        counter[0] += 1
        return f"{pre}<img id='{idx}'/>{post}"

    return re.sub(pattern, replacer, text)


def unescape_slack_emoji(translated_text: str, source_text: str) -> str:
    """Restore Slack emoji and special tags from indexed img placeholders.
    Restores original spacing from source text regardless of how translation modified it.

    Args:
        translated_text (str): The translated text with img placeholders.
        source_text (str): The original source text to extract original values from.

    Returns:
        str: The text with img placeholders replaced with original Slack content and spacing.
    """
    # Find all original emoji/tags from source text with their surrounding spacing
    spacing_pattern = r"( ?)(:[^\s]*?:|<[^\s]*>)( ?)"
    original_matches_with_spacing = re.findall(spacing_pattern, source_text)

    # Normalize img tags that may have whitespace variations from translation API
    translated_text = re.sub(
        r'<\s*img\s+id\s*=\s*["\']?(\d+)["\']?\s*/?\s*>',
        _normalize_img_tag,
        translated_text,
    )

    # Replace each placeholder with original content including original spacing
    for i, (orig_pre, core, orig_post) in enumerate(original_matches_with_spacing):
        placeholder = f"<img id='{i}'/>"
        idx = translated_text.find(placeholder)
        if idx == -1:
            continue

        # Determine what spacing exists around the placeholder in translated text
        has_trans_pre = idx > 0 and translated_text[idx - 1] == " "
        end_idx = idx + len(placeholder)
        has_trans_post = (
            end_idx < len(translated_text) and translated_text[end_idx] == " "
        )

        # Calculate replacement boundaries to include any existing surrounding spaces
        replace_start = idx - (1 if has_trans_pre else 0)
        replace_end = end_idx + (1 if has_trans_post else 0)

        # Replace with original emoji and its original spacing
        replacement = orig_pre + core + orig_post
        translated_text = (
            translated_text[:replace_start]
            + replacement
            + translated_text[replace_end:]
        )

    return translated_text


def _normalize_img_tag(match: re.Match) -> str:
    """Normalize img tag format for consistent replacement."""
    idx = match.group(1)
    return f"<img id='{idx}'/>"


def split_text_into_blocks(
    text: str, max_length: int = 3000, first_chunk_limit: int | None = None
) -> list[str]:
    """Split long text into chunks that fit within Slack's block limit.

    Args:
        text (str): The text to split.
        max_length (int): Maximum characters per chunk. Defaults to 3000 (Slack's limit for mrkdwn text in section blocks).
        first_chunk_limit (int | None): Optional limit for the first chunk (useful when first chunk has a label).
            If None, uses max_length for all chunks.

    Returns:
        list[str]: List of text chunks, each within the specified limit.
    """
    first_limit = first_chunk_limit or max_length
    if len(text) <= first_limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    is_first = True

    while remaining:
        chunk_limit = first_limit if is_first else max_length
        if len(remaining) <= chunk_limit:
            chunks.append(remaining)
            break
        chunks.append(remaining[:chunk_limit])
        remaining = remaining[chunk_limit:]
        is_first = False

    return chunks


SCORED_CATEGORIES = [
    "translation_memory",
    "best",
    "good",
    "acceptable",
    "bad",
]


def calculate_evaluation_percentages(
    counts: dict[str, int],
) -> dict[str, int]:
    """Calculate quality evaluation percentages that always sum to 100%.

    Uses the Largest Remainder Method (Hamilton's method) to distribute
    rounding residuals fairly across categories, ensuring the total is
    exactly 100%.

    Args:
        counts: A dict mapping category names to segment counts
            (e.g. {"best": 2, "good": 5, "bad": 1, ...}).

    Returns:
        A dict mapping each scored category to its integer percentage.
        All values sum to exactly 100 (or all 0 if segment_count is 0).
    """
    segment_count = sum(counts.get(cat, 0) for cat in SCORED_CATEGORIES)

    if segment_count == 0:
        return {cat: 0 for cat in SCORED_CATEGORIES}

    raw = {cat: (counts.get(cat, 0) / segment_count) * 100 for cat in SCORED_CATEGORIES}
    floored = {cat: math.floor(val) for cat, val in raw.items()}
    remainders = {cat: raw[cat] - floored[cat] for cat in SCORED_CATEGORIES}

    missing = 100 - sum(floored.values())

    for cat in sorted(remainders, key=lambda c: remainders[c], reverse=True)[:missing]:
        floored[cat] += 1

    return floored


def segment_quality_score(score: float, taus_version: str = "1.0.0") -> str:
    """
    Determine the quality score based on the TAUS QE version.

    Args:
        score (float): The quality score.
        taus_version (str): The TAUS QE version. Defaults to "1.0.0".

    Returns:
        str: The quality category ("best", "good", "acceptable", "bad").
    """
    if taus_version == "2.0.0":
        # TAUS QE version 2.0.0
        if score >= 0.9:
            return _("Overall Translation Quality: Best")
        elif score >= 0.88:
            return _("Overall Translation Quality: Good")
        elif score >= 0.8:
            return _("Overall Translation Quality: Acceptable")
        return _("Overall Translation Quality: Bad")
    else:
        # TAUS QE version 1.0.0
        if score >= 0.95:
            return _("Overall Translation Quality: Best")
        elif score >= 0.9:
            return _("Overall Translation Quality: Good")
        elif score >= 0.85:
            return _("Overall Translation Quality: Acceptable")
        return _("Overall Translation Quality: Bad")


def extract_language_codes_from_form(
    form_data: dict[str, Any],
    block_id: str = "target_langs",
    action_id: str = "language_mt_options",
) -> list[str]:
    """Extract language codes from Slack modal form data.

    Args:
        form_data: The form state values from Slack view submission.
        block_id: The block ID containing the language selector. Defaults to "target_langs".
        action_id: The action ID of the language selector. Defaults to "language_mt_options".

    Returns:
        list[str]: List of language codes (e.g., ["en", "fr", "es"]).
    """
    selected_languages_data = (
        form_data.get(block_id, {}).get(action_id, {}).get("selected_options", [])
    )
    return [
        option.get("value") for option in selected_languages_data if option.get("value")
    ]


def strip_command_formatting(text: str) -> str:
    """Strip a single layer of Slack markdown wrapping from a slash-command arg.

    Slack wraps bold/italic/strike/code text in ``*``, ``_``, ``~`` or ``` ` ```.
    This removes one matching wrapper so command parsing sees the raw argument.
    Not perfect, but sufficient for the short tokens used in slash commands.
    """
    if re.match(r"(\*.+\*)|(~.+~)|(_.+_)|(`.+`)", text):
        return text[1:-1]
    return text


def calculate_total_estimated_days(time_estimates: list[float]) -> int:
    """Return the overall HV turnaround in days (Verify-aligned: global max).

    Each estimate is per (file, target language). Verify stores and displays
    turnaround independently per target and uses max when rolling up to a
    single job due date.
    """
    if not time_estimates:
        return 0
    return math.ceil(max(time_estimates))
