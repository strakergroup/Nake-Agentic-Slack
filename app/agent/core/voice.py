"""Arbitr voice rules that can be checked mechanically.

Python port of poc/js/copy-rules.js so the simulation and the agent are held to
the same rules. Used on every fixed string at test time and on every
model-written reply at run time (where it only records, never blocks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Emoji and pictographs. Python's `re` has no \p{Extended_Pictographic}; these
# ranges cover the emoji blocks plus variation selector 16 and keycap marks.
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # mahjong .. symbols and pictographs extended-A
    "\U00002600-\U000027bf"  # misc symbols, dingbats
    "\U00002b00-\U00002bff"  # arrows and stars used as emoji
    "\U0000fe0f\U000020e3"
    "]"
)

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("no-dash", re.compile("[–—]")),
    ("no-emoji", _EMOJI),
    ("capitalise-arbitr", re.compile(r"(?<![/\w.-])arbitr(?![\w-])")),
    ("no-exclamation", re.compile("!")),
    ("no-users", re.compile(r"\busers?\b", re.I)),
    ("no-we", re.compile(r"\b(We|we|Our|our|ours)\b")),
    (
        "retired-word",
        re.compile(
            r"\b(seamless(ly)?|empower(s|ed|ing)?|unlock(s|ed|ing)?|effortless(ly)?"
            r"|bottleneck|raw potential|revolutionary|next-gen)\b",
            re.I,
        ),
    ),
    (
        "off-limits-claim",
        re.compile(r"\b(trust scor(e|ing)|consensus voting|glass box)\b", re.I),
    ),
    (
        "retired-name",
        re.compile(
            r"\b(Straker\.AI|NotVerify|LanguageCloud|RAY Translate|Connect to Verify)\b"
        ),
    ),
]


@dataclass(frozen=True)
class Violation:
    rule: str
    text: str


def check_copy(text: str) -> list[Violation]:
    return [Violation(rule, text) for rule, pattern in _RULES if pattern.search(text)]
