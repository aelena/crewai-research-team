from research_team.voice import active_voice, lint, list_voices, load_voice, prose, word_count


def rules(text, voice):
    return {v.rule for v in lint(text, voice)}


def test_profiles_load_and_active(home):
    names = {v.slug for v in list_voices(home / "voices")}
    assert {"antonio-elena", "default"} <= names
    assert active_voice(home / "voices") == "antonio-elena"
    assert load_voice(None, home / "voices").slug == "antonio-elena"


def test_unknown_voice_lists_known(home):
    import pytest

    with pytest.raises(FileNotFoundError, match="antonio-elena"):
        load_voice("nope", home / "voices")


def test_prompt_carries_hard_rules_and_examples(home):
    text = load_voice("antonio-elena", home / "voices").prompt(home)
    assert "Never use em dashes" in text
    assert "No contractions" in text
    assert "delve" in text
    assert "STYLE EXAMPLES" in text  # references/flowtrack-li-post.md is read in


def test_clean_text_passes(home):
    v = load_voice("antonio-elena", home / "voices")
    assert lint("Every tracker helps you start things. Almost none helps you stop.", v) == []


def test_hard_rules(home):
    v = load_voice("antonio-elena", home / "voices")
    assert rules("This is a game-changing idea — and it's here.", v) == {
        "avoid-vocabulary", "no-em-dashes", "no-contractions"}
    assert "exclamations" in rules("It works!", v)


def test_wildcard_phrase(home):
    v = load_voice("antonio-elena", home / "voices")
    assert "avoid-vocabulary" in rules("It is not just a tool, but a way of working.", v)


def test_quotes_sources_and_urls_are_not_the_authors_prose(home):
    v = load_voice("antonio-elena", home / "voices")
    text = (
        'The CEO said "we can\'t wait to unlock value" on the call.\n\n'
        "> A quoted block that doesn't count.\n\n"
        "See [the filing](https://x.test/landscape-report).\n\n"
        "## Sources\n\n- [It's a landscape](https://y.test)\n"
    )
    assert lint(text, v) == []


def test_sentence_length_is_a_warning(home):
    v = load_voice("antonio-elena", home / "voices")
    long = " ".join(["word"] * 40) + "."
    [issue] = lint(long, v)
    assert issue.rule == "sentence-length" and issue.severity == "warning"


def test_default_voice_is_permissive(home):
    v = load_voice("default", home / "voices")
    assert lint("It's fine — really.", v) == []


def test_word_count_ignores_front_matter_and_sources():
    text = "---\ntitle: x\n---\n\none two three\n\n## Sources\n\n- a b c d e f"
    assert word_count(text) == 3
    assert "Sources" not in prose(text)
