from types import SimpleNamespace

from app.slack.language_validation import (
    get_conflicting_target_language_labels_from_rows,
    get_language_base_code,
    is_same_language_family,
)


def test_get_language_base_code_normalizes_variants():
    assert get_language_base_code("fr-CA") == "fr"
    assert get_language_base_code("pt_BR") == "pt"
    assert get_language_base_code("ja") == "ja"
    assert get_language_base_code(None) == ""


def test_is_same_language_family_matches_regional_variants():
    assert is_same_language_family("fr", "fr-CA")
    assert is_same_language_family("en-US", "en-GB")
    assert not is_same_language_family("fr", "en")


def test_get_conflicting_target_language_labels_from_rows():
    source_language = SimpleNamespace(
        uuid="source-fr", label="French", google_code="fr"
    )
    conflicting_target = SimpleNamespace(
        uuid="target-fr-ca", label="French Canadian", google_code="fr-CA"
    )
    allowed_target = SimpleNamespace(
        uuid="target-en", label="English", google_code="en"
    )

    conflicting = get_conflicting_target_language_labels_from_rows(
        "source-fr",
        ["target-fr-ca", "target-en"],
        [source_language, conflicting_target, allowed_target],
    )

    assert conflicting == ["French Canadian"]
