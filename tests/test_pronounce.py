from app.pronounce import Rule, apply_aliases, ipa_rules, parse_rules


def test_alias_replaces_whole_words_case_insensitively():
    rules = [Rule("Siobhan", "Shiv-awn")]
    assert apply_aliases("Siobhan met SIOBHAN.", rules) == "Shiv-awn met Shiv-awn."


def test_alias_does_not_touch_parts_of_words():
    assert apply_aliases("Holmesian Holmes", [Rule("Holmes", "Homes")]) == "Holmesian Homes"


def test_longest_match_wins_and_no_chaining():
    rules = [Rule("York", "Yawk"), Rule("New York", "Noo Yawk"), Rule("Yawk", "SHOULD NOT APPEAR")]
    assert apply_aliases("New York and York", rules) == "Noo Yawk and Yawk"


def test_ipa_rules_are_not_applied_to_text():
    rules = [Rule("Siobhan", "ʃɪˈvɔːn", "ipa")]
    assert apply_aliases("Siobhan", rules) == "Siobhan"
    assert ipa_rules(rules) == rules


def test_parse_rules_skips_blank_rows_and_normalises_kind():
    raw = [{"word": " Irene ", "say": "Eye-ree-nee"}, {"word": "", "say": "x"}, {"word": "Adler", "say": "ˈædlər", "kind": "IPA"}]
    assert parse_rules(raw) == [Rule("Irene", "Eye-ree-nee", "alias"), Rule("Adler", "ˈædlər", "ipa")]


def test_no_rules_is_a_no_op():
    assert apply_aliases("Unchanged.", []) == "Unchanged."
