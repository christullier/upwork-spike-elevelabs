"""Split long text into chunks that fit under the model's per-request limit.

Rules, in order of preference:
1. Break between paragraphs (blank lines).
2. If one paragraph is too long, break between sentences.
3. Only if a single sentence is longer than the limit (rare in prose) do we
   fall back to the last space before the limit, so we never cut a word.

Pure functions, no I/O. See tests/test_chunker.py.
"""

from __future__ import annotations

import re

DEFAULT_LIMIT = 9_000  # eleven_v4 accepts 10,000 chars per request; keep a margin.

# Words that end in a period but don't end a sentence.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "prof", "rev", "gen", "col",
    "capt", "lt", "mt", "no", "vs", "etc", "e.g", "i.e", "fig", "vol", "ch",
}

# A sentence ends at . ! ? or … optionally followed by closing quotes/brackets,
# then whitespace, then something that is not a lowercase letter
# ("Is it you?" he asked. stays one sentence).
_SENTENCE_END = re.compile(r"""[.!?…]+["'”’)\]]*\s+(?=[^a-z\s])""")


def split_paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def split_sentences(paragraph: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(paragraph):
        candidate = paragraph[start:match.start() + len(match.group().rstrip())]
        last_word = candidate.rstrip(".!?…\"'”’)]").rsplit(None, 1)[-1:] or [""]
        if last_word[0].lower() in _ABBREVIATIONS:
            continue
        sentences.append(candidate.strip())
        start = match.end()
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _hard_split(sentence: str, limit: int) -> list[str]:
    """Last resort for a single sentence longer than the limit: cut at spaces."""
    pieces: list[str] = []
    rest = sentence
    while len(rest) > limit:
        cut = rest.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit  # one enormous "word"; nothing better to do
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def _pack(units: list[str], limit: int, joiner: str) -> list[str]:
    """Greedily join units with `joiner` while staying under `limit`."""
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{joiner}{unit}" if current else unit
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def split_text(text: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Return chunks, each at most `limit` characters, in reading order."""
    if limit < 1:
        raise ValueError("limit must be positive")

    # Break oversized paragraphs into sentence-sized units first, then pack.
    units: list[tuple[str, bool]] = []  # (text, starts_new_paragraph)
    for paragraph in split_paragraphs(text):
        if len(paragraph) <= limit:
            units.append((paragraph, True))
            continue
        pieces: list[str] = []
        for sentence in split_sentences(paragraph):
            pieces.extend(_hard_split(sentence, limit) if len(sentence) > limit else [sentence])
        for i, piece_group in enumerate(_pack(pieces, limit, " ")):
            units.append((piece_group, i == 0))

    chunks: list[str] = []
    current = ""
    for unit, new_paragraph in units:
        joiner = "\n\n" if new_paragraph else " "
        candidate = f"{current}{joiner}{unit}" if current else unit
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def word_count(text: str) -> int:
    return len(text.split())
