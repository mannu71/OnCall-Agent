"""Unit tests for hashline content-hash-anchored edits."""
import pytest

from app.harness.hashline import (
    line_hash,
    annotate,
    parse_anchor,
    apply_edit,
    HashlineError,
)

SAMPLE = "def f(x):\n    y = x + 1\n    return y\n"


def _anchor(content: str, line_no: int) -> str:
    """Build the anchor string the model would copy from an annotated read."""
    lines = content.splitlines()
    return f"L{line_no}#{line_hash(lines[line_no - 1])}"


# ── helpers ──────────────────────────────────────────────────────────────────


def test_line_hash_newline_insensitive():
    assert line_hash("foo") == line_hash("foo\n") == line_hash("foo\r\n")


def test_annotate_format():
    out = annotate("a\nb")
    lines = out.splitlines()
    assert lines[0].startswith("L1#") and lines[0].endswith(": a")
    assert lines[1].startswith("L2#") and lines[1].endswith(": b")


def test_parse_anchor_variants():
    assert parse_anchor("L42#ABCD12") == (42, "abcd12")
    assert parse_anchor("42#abcd12") == (42, "abcd12")
    assert parse_anchor("42:abcd12") == (42, "abcd12")
    with pytest.raises(HashlineError):
        parse_anchor("not-an-anchor")


# ── apply: happy paths ───────────────────────────────────────────────────────


def test_single_line_replace_unchanged_file():
    res = apply_edit(SAMPLE, _anchor(SAMPLE, 2), "    y = x + 2")
    assert "y = x + 2" in res.updated
    assert "y = x + 1" not in res.updated
    assert res.removed == 1 and res.added == 1
    assert res.updated.endswith("\n")  # trailing newline preserved


def test_multi_line_span_replace():
    res = apply_edit(SAMPLE, _anchor(SAMPLE, 2), "    return x + 1",
                     end_anchor=_anchor(SAMPLE, 3))
    assert "return x + 1" in res.updated
    assert "y = x + 1" not in res.updated and "return y" not in res.updated
    assert res.removed == 2 and res.added == 1


def test_deletion_with_empty_new_text():
    res = apply_edit(SAMPLE, _anchor(SAMPLE, 2), "")
    assert "y = x + 1" not in res.updated
    assert res.added == 0


# ── apply: drift tolerance ───────────────────────────────────────────────────


def test_drift_lines_inserted_above():
    # Anchor computed against the ORIGINAL file (line 2), then two lines are
    # prepended so the real content sits at line 4 — the hash still locates it.
    anchor = _anchor(SAMPLE, 2)  # hash of "    y = x + 1"
    drifted = "# header\n# header2\n" + SAMPLE
    res = apply_edit(drifted, anchor, "    y = x + 99")
    assert "y = x + 99" in res.updated
    assert res.start_line == 4  # resolved to the drifted position


def test_duplicate_lines_disambiguated_by_line_number():
    content = "x = 1\nkeep\nx = 1\n"  # identical line at L1 and L3
    h = line_hash("x = 1")
    # Target the SECOND occurrence (line 3).
    res = apply_edit(content, f"L3#{h}", "x = 2")
    assert res.updated == "x = 1\nkeep\nx = 2\n"
    assert res.start_line == 3


# ── apply: clean failures ────────────────────────────────────────────────────


def test_changed_anchor_fails_clean():
    # Anchor for line 2, but the file's line 2 content has since changed.
    anchor = _anchor(SAMPLE, 2)
    mutated = SAMPLE.replace("    y = x + 1", "    y = x + 1000")
    with pytest.raises(HashlineError):
        apply_edit(mutated, anchor, "whatever")


def test_inverted_span_fails():
    with pytest.raises(HashlineError):
        apply_edit(SAMPLE, _anchor(SAMPLE, 3), "x", end_anchor=_anchor(SAMPLE, 2))
