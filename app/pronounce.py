"""Pronunciation rules: "word -> how to say it".

Two kinds:
- alias: replaced in the text before it is sent ("Siobhan" -> "Shiv-awn").
  Done locally so you can see exactly what the model reads.
- ipa:   an IPA phoneme string ("Siobhan" -> "ʃɪˈvɔːn"). Eleven v4 has no SSML,
  so these go to ElevenLabs as a pronunciation dictionary (phoneme rules are
  supported on eleven_v4 per the docs) instead of being written into the text.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass
class Rule:
    word: str
    say: str
    kind: str = "alias"  # "alias" or "ipa"

    def to_dict(self) -> dict:
        return asdict(self)


def parse_rules(raw: list[dict] | None) -> list[Rule]:
    rules = []
    for item in raw or []:
        word = (item.get("word") or "").strip()
        say = (item.get("say") or "").strip()
        kind = (item.get("kind") or "alias").strip().lower()
        if word and say:
            rules.append(Rule(word, say, "ipa" if kind == "ipa" else "alias"))
    return rules


def apply_aliases(text: str, rules: list[Rule]) -> str:
    """Replace whole words, case-insensitively, in a single pass.

    Single pass means a replacement is never itself replaced again, and longer
    entries win over shorter ones ("New York" before "York").
    """
    aliases = {r.word.lower(): r.say for r in rules if r.kind == "alias"}
    if not aliases:
        return text
    words = sorted(aliases, key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE)
    return pattern.sub(lambda m: aliases[m.group(0).lower()], text)


def ipa_rules(rules: list[Rule]) -> list[Rule]:
    return [r for r in rules if r.kind == "ipa"]
