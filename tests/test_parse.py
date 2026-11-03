"""解析器测试（T13 验收：对 tests/fixtures 跑通解析）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from compute_dual.common.errors import ParseError
from compute_dual.tools.parse_file import detect_kind, find_snippets, parse_bytes, parse_file
from compute_dual.tools.textract import extract_html_text


def test_parse_html_keeps_article_drops_nav_and_script(fixtures_dir: Path) -> None:
    payload = (fixtures_dir / "sample.html").read_bytes()
    document = parse_bytes(payload, filename="sample.html", content_type="text/html")

    assert document.kind == "html"
    assert "合成夹具" in document.text
    assert "第二段用于验证正文抽取" in document.text
    assert "console.log" not in document.text


def test_extract_html_title() -> None:
    html = b"<html><head><title>My Title</title></head><body><p>hello</p></body></html>"
    text, title = extract_html_text(html)
    assert "hello" in text
    assert title == "My Title"


def test_parse_pdf_extracts_pages(fixtures_dir: Path) -> None:
    document = parse_file(fixtures_dir / "sample.pdf")

    assert document.kind == "pdf"
    assert document.page_count == 2
    assert "解析器测试夹具" in document.text
    assert "第二页用于验证多页合并" in document.text
    assert len(document.blocks) == 2


def test_parse_docx_extracts_text_and_table(fixtures_dir: Path) -> None:
    document = parse_file(fixtures_dir / "sample.docx")

    assert document.kind == "docx"
    assert "合成夹具 DOCX" in document.text
    assert document.tables, "DOCX 表格应被解析"
    assert any("H100" in cell for row in document.tables[0] for cell in row)


def test_parse_xlsx_extracts_rows(fixtures_dir: Path) -> None:
    document = parse_file(fixtures_dir / "sample.xlsx")

    assert document.kind == "xlsx"
    assert "东南亚" in document.text
    assert document.tables
    assert document.tables[0][0][0] == "区域"


def test_detect_kind_by_magic_bytes(fixtures_dir: Path) -> None:
    assert detect_kind((fixtures_dir / "sample.pdf").read_bytes()) == "pdf"
    assert detect_kind((fixtures_dir / "sample.docx").read_bytes(), filename="a.docx") == "docx"
    assert detect_kind((fixtures_dir / "sample.xlsx").read_bytes(), filename="a.xlsx") == "xlsx"
    assert detect_kind(b"<!DOCTYPE html><html></html>") == "html"
    assert detect_kind(b"plain text content") == "text"


def test_empty_payload_raises() -> None:
    with pytest.raises(ParseError):
        parse_bytes(b"")


def test_corrupt_pdf_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_bytes(b"%PDF-1.4 not really a pdf", filename="broken.pdf")


def test_missing_file_raises() -> None:
    with pytest.raises(ParseError):
        parse_file("tests/fixtures/does-not-exist.pdf")


def test_to_markdown_contains_metadata(fixtures_dir: Path) -> None:
    document = parse_file(fixtures_dir / "sample.pdf")
    markdown = document.to_markdown()
    assert f"doc_id: {document.doc_id}" in markdown
    assert "第 1 页" in markdown


def test_find_snippets_locates_keywords(fixtures_dir: Path) -> None:
    document = parse_file(fixtures_dir / "sample.pdf")
    snippets = find_snippets(document.text, ["交付周期"])
    assert snippets
    assert any("交付" in snippet for snippet in snippets)
