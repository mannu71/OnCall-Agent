"""Unit tests for the Wiki node's smart table-merge append.

`merge_markdown_tables` appends the new content's table data rows into the last
markdown table of the existing page when the headers match, and otherwise
returns None so the caller falls back to a separator + run-header block append.
"""
import pytest

from app.workflow.executor.handlers.wiki import merge_markdown_tables


TABLE_V1 = "| Name | Status |\n| --- | --- |\n| alpha | ok |"
TABLE_V2 = "| Name | Status |\n| --- | --- |\n| beta | fail |\n| gamma | ok |"


def test_matching_headers_appends_rows():
    merged = merge_markdown_tables(TABLE_V1, TABLE_V2)
    assert merged is not None
    lines = merged.splitlines()
    # Original header + separator + row, then the two new rows.
    assert lines == [
        "| Name | Status |",
        "| --- | --- |",
        "| alpha | ok |",
        "| beta | fail |",
        "| gamma | ok |",
    ]


def test_header_mismatch_returns_none():
    other = "| Name | Owner |\n| --- | --- |\n| beta | me |"
    assert merge_markdown_tables(TABLE_V1, other) is None


def test_existing_without_table_returns_none():
    assert merge_markdown_tables("# Page\n\nJust prose here.", TABLE_V2) is None


def test_new_without_table_returns_none():
    assert merge_markdown_tables(TABLE_V1, "no table, only text") is None


def test_alignment_colon_separators_match():
    v1 = "| Name | Status |\n| :--- | ---: |\n| alpha | ok |"
    v2 = "| Name | Status |\n| :---: | :---: |\n| beta | fail |"
    merged = merge_markdown_tables(v1, v2)
    assert merged is not None
    assert merged.splitlines()[-1] == "| beta | fail |"


def test_trailing_prose_after_table_preserved():
    existing = TABLE_V1 + "\n\nSome footer note."
    merged = merge_markdown_tables(existing, TABLE_V2)
    assert merged is not None
    lines = merged.splitlines()
    assert "| gamma | ok |" in lines
    assert lines[-1] == "Some footer note."
    # New rows inserted before the trailing prose, not after it.
    assert lines.index("| gamma | ok |") < lines.index("Some footer note.")


def test_headers_normalized_case_and_whitespace():
    v1 = "|  Name | Status  |\n| --- | --- |\n| alpha | ok |"
    v2 = "| NAME | status |\n| --- | --- |\n| beta | fail |"
    merged = merge_markdown_tables(v1, v2)
    assert merged is not None
    assert merged.splitlines()[-1] == "| beta | fail |"


def test_empty_new_table_returns_none():
    header_only = "| Name | Status |\n| --- | --- |"
    assert merge_markdown_tables(TABLE_V1, header_only) is None


def test_merges_into_last_table_when_multiple():
    existing = (
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
        "Some text\n\n"
        "| Name | Status |\n| --- | --- |\n| alpha | ok |"
    )
    merged = merge_markdown_tables(existing, TABLE_V2)
    assert merged is not None
    lines = merged.splitlines()
    # The first (A|B) table is untouched; new rows land in the last table.
    assert lines[2] == "| 1 | 2 |"
    assert lines[-1] == "| gamma | ok |"
