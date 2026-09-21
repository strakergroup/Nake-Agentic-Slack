from app.agent.core.voice import check_copy


def rules(text):
    return [v.rule for v in check_copy(text)]


def test_clean_copy_passes():
    assert rules("Arbitr has started the French translation.") == []


def test_flags_em_and_en_dashes():
    assert rules("Ready — two files") == ["no-dash"]
    assert rules("Ready – two files") == ["no-dash"]


def test_flags_emoji():
    assert rules("Done \U0001f44d") == ["no-emoji"]
    assert rules("Done ✅") == ["no-emoji"]


def test_flags_lowercase_product_name_but_not_commands_or_paths():
    assert rules("ask arbitr to translate") == ["capitalise-arbitr"]
    assert rules("Type /arbitr to start") == []
    assert rules("the arbitr-agent branch") == []


def test_flags_exclamation_marks():
    assert rules("Your file is ready!") == ["no-exclamation"]


def test_flags_the_word_users():
    assert rules("Users can mute this") == ["no-users"]


def test_flags_retired_and_off_limits_words():
    assert rules("A seamless experience") == ["retired-word"]
    assert rules("Your trust score is 92") == ["off-limits-claim"]
    assert rules("Explained by Glass Box") == ["off-limits-claim"]


def test_flags_retired_names():
    assert rules("Connect to Verify") == ["retired-name"]
    assert rules("Open LanguageCloud") == ["retired-name"]


def test_japanese_and_accented_text_is_not_mistaken_for_emoji():
    assert rules("ローンチは10月14日に変更になりました。") == []
    assert rules("Überprüfung abgeschlossen. Tradução concluída.") == []


def test_flags_we_and_our_because_a_human_we_blurs_who_acted():
    assert rules("We have started the job") == ["no-we"]
    assert rules("USD 6.00 for us-east") == []
    assert rules("I've requested the quote.") == []
