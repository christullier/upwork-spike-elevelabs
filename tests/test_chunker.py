from pathlib import Path

import pytest

from app.chunker import split_sentences, split_text
from app.server import strip_source_note

CHAPTER = strip_source_note((Path(__file__).parent.parent / "samples" / "chapter.txt").read_text())


def ends_sentence(chunk: str) -> bool:
    return chunk.rstrip("\"'”’)]").endswith((".", "!", "?", "…", "—"))


def test_empty_and_whitespace_input():
    assert split_text("") == []
    assert split_text("   \n\n  \n") == []


def test_short_text_is_one_chunk():
    assert split_text("Hello there. How are you?") == ["Hello there. How are you?"]


def test_respects_limit_on_real_chapter():
    longest_sentence = max(len(s) for p in CHAPTER.split("\n\n") for s in split_sentences(p))
    assert longest_sentence < 1000
    for limit in (300, 1000, 4000, 9000):
        chunks = split_text(CHAPTER, limit)
        assert all(len(c) <= limit for c in chunks)
        if limit >= longest_sentence:  # below that, a too-long sentence must be split at a space
            assert all(ends_sentence(c) for c in chunks), "a chunk was cut mid-sentence"


def test_default_limit_on_sample_chapter():
    chunks = split_text(CHAPTER)
    assert [len(c) for c in chunks] and all(len(c) <= 9000 for c in chunks)
    assert all(c.endswith((".", "?", "!", "”", "’", '"')) for c in chunks)


def test_no_text_lost_or_reordered():
    chunks = split_text(CHAPTER, 1500)
    assert " ".join(" ".join(chunks).split()) == " ".join(CHAPTER.split())


def test_prefers_paragraph_breaks():
    paras = ["A" * 40 + ".", "B" * 40 + ".", "C" * 40 + "."]
    text = "\n\n".join(paras)
    # Room for two paragraphs per chunk: break falls between paragraphs, not inside one.
    assert split_text(text, 90) == [paras[0] + "\n\n" + paras[1], paras[2]]


def test_long_paragraph_splits_on_sentences():
    sentences = [f"Sentence number {i} is right here." for i in range(30)]
    paragraph = " ".join(sentences)
    chunks = split_text(paragraph, 200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    for c in chunks:
        assert c.endswith("here."), c
    assert " ".join(chunks) == paragraph


def test_single_overlong_sentence_falls_back_to_word_boundary():
    sentence = " ".join(["word"] * 100) + "."
    chunks = split_text(sentence, 50)
    assert all(len(c) <= 50 for c in chunks)
    assert all(not c.startswith(" ") and "wor d" not in c for c in chunks)
    assert " ".join(chunks) == sentence


def test_sentence_splitter_handles_quotes_and_abbreviations():
    text = 'Mr. Holmes looked up. "Is it you, Watson?" he asked. Dr. Watson nodded!'
    assert split_sentences(text) == ["Mr. Holmes looked up.", '"Is it you, Watson?" he asked.', "Dr. Watson nodded!"]


def test_rejects_bad_limit():
    with pytest.raises(ValueError):
        split_text("Hi.", 0)
