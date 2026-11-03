"""文件解析：PDF / DOCX / XLSX / HTML / 纯文本 → RawDocument。

设计文档 §4：输出统一为 RawDocument（文本块、页码、表格、文件哈希）。
解析失败抛 ``ParseError(error_kind='parse')``，不返回伪造正文。
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common.errors import ParseError
from ..common.ids import content_doc_id, sha256_hex
from ..common.timeutil import now_shanghai_str
from .textract import extract_html_text, looks_like_html

SUPPORTED_KINDS = ("pdf", "docx", "xlsx", "html", "text")


@dataclass
class TextBlock:
    index: int
    text: str
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "page": self.page, "text": self.text}


@dataclass
class RawDocument:
    doc_id: str
    kind: str
    content_type: str
    text: str
    blocks: list[TextBlock] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    sha256: str = ""
    byte_size: int = 0
    page_count: int | None = None
    title: str | None = None
    retrieved_at_shanghai: str = ""
    source_path: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "kind": self.kind,
            "content_type": self.content_type,
            "chars": len(self.text),
            "pages": self.page_count,
            "tables": len(self.tables),
            "sha256": self.sha256[:12],
            "byte_size": self.byte_size,
            "title": self.title,
        }

    def to_markdown(self) -> str:
        """落盘到 ``data/text/{doc_id}.md`` 的正文表示。"""
        header = [
            f"<!-- doc_id: {self.doc_id} -->",
            f"<!-- kind: {self.kind} -->",
            f"<!-- sha256: {self.sha256} -->",
            f"<!-- retrieved_at_shanghai: {self.retrieved_at_shanghai} -->",
        ]
        if self.title:
            header.append(f"<!-- title: {self.title} -->")
        if self.source_path:
            header.append(f"<!-- source: {self.source_path} -->")

        body: list[str] = []
        if self.page_count and len(self.blocks) > 1:
            for block in self.blocks:
                if block.page:
                    body.append(f"\n## 第 {block.page} 页\n")
                body.append(block.text)
        else:
            body.append(self.text)

        if self.tables:
            body.append("\n## 表格\n")
            for table_index, table in enumerate(self.tables, start=1):
                body.append(f"\n### 表 {table_index}\n")
                for row in table:
                    body.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")

        return "\n".join(header) + "\n\n" + "\n".join(body).strip() + "\n"


def detect_kind(
    payload: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
    url: str | None = None,
) -> str:
    """按魔数 → 文件名 → URL → Content-Type 的顺序判断类型。"""
    head = payload[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        name = (filename or url or "").lower()
        if name.endswith(".docx") or "wordprocessingml" in (content_type or ""):
            return "docx"
        if name.endswith(".xlsx") or "spreadsheetml" in (content_type or ""):
            return "xlsx"
        # 无法靠魔数区分时再看扩展名，默认按 docx 尝试并让解析器报错
        return "docx" if name.endswith(".docx") else "xlsx"
    if looks_like_html(payload, content_type):
        return "html"
    return "text"


def _parse_pdf(payload: bytes) -> tuple[str, list[TextBlock], list[list[list[str]]], int, str | None]:
    try:
        import pymupdf  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ParseError("缺少 pymupdf，无法解析 PDF：pip install pymupdf", error_kind="parse") from exc

    blocks: list[TextBlock] = []
    tables: list[list[list[str]]] = []
    page_texts: list[str] = []
    title: str | None = None

    try:
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            metadata = document.metadata or {}
            title = (metadata.get("title") or "").strip() or None
            for page_index, page in enumerate(document, start=1):
                page_text = page.get_text("text") or ""
                page_texts.append(page_text)
                blocks.append(TextBlock(index=page_index - 1, page=page_index, text=page_text.strip()))
                try:
                    found = page.find_tables()
                    for table in getattr(found, "tables", []) or []:
                        rows = table.extract() or []
                        cleaned = [
                            ["" if cell is None else str(cell).strip() for cell in row] for row in rows
                        ]
                        if cleaned:
                            tables.append(cleaned)
                except Exception:  # noqa: BLE001 - 表格识别失败不影响正文
                    continue
            page_count = document.page_count
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"PDF 解析失败：{exc}", error_kind="parse") from exc

    return "\n\n".join(page_texts).strip(), blocks, tables, page_count, title


def _parse_docx(payload: bytes) -> tuple[str, list[TextBlock], list[list[list[str]]], None, str | None]:
    import io

    try:
        import docx  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ParseError("缺少 python-docx：pip install python-docx", error_kind="parse") from exc

    try:
        document = docx.Document(io.BytesIO(payload))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"DOCX 解析失败：{exc}", error_kind="parse") from exc

    blocks: list[TextBlock] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = (paragraph.text or "").strip()
        if text:
            blocks.append(TextBlock(index=index, text=text))

    tables: list[list[list[str]]] = []
    for table in document.tables:
        rows = [[(cell.text or "").strip() for cell in row.cells] for row in table.rows]
        if rows:
            tables.append(rows)

    text = "\n".join(block.text for block in blocks)
    return text, blocks, tables, None, None


def _parse_xlsx(payload: bytes) -> tuple[str, list[TextBlock], list[list[list[str]]], None, str | None]:
    import io

    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ParseError("缺少 openpyxl：pip install openpyxl", error_kind="parse") from exc

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"XLSX 解析失败：{exc}", error_kind="parse") from exc

    blocks: list[TextBlock] = []
    tables: list[list[list[str]]] = []
    lines: list[str] = []

    for sheet_index, sheet in enumerate(workbook.worksheets):
        rows: list[list[str]] = []
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if value is None else str(value).strip() for value in row]
            if any(cells):
                rows.append(cells)
        if not rows:
            continue
        tables.append(rows)
        blocks.append(TextBlock(index=sheet_index, text=f"## 工作表：{sheet.title}"))
        for cells in rows:
            lines.append(" | ".join(cells))

    try:
        workbook.close()
    except Exception:  # noqa: BLE001
        pass

    return "\n".join(lines), blocks, tables, None, None


def parse_bytes(
    payload: bytes,
    *,
    kind: str | None = None,
    content_type: str | None = None,
    filename: str | None = None,
    url: str | None = None,
) -> RawDocument:
    """把字节解析为 RawDocument。"""
    if not payload:
        raise ParseError("内容为空，无法解析", error_kind="parse")

    resolved_kind = kind or detect_kind(payload, content_type=content_type, filename=filename, url=url)
    guessed_type = content_type or mimetypes.guess_type(filename or url or "")[0]
    retrieved = now_shanghai_str()

    if resolved_kind == "pdf":
        text, blocks, tables, page_count, title = _parse_pdf(payload)
        content_type = content_type or "application/pdf"
    elif resolved_kind == "docx":
        text, blocks, tables, page_count, title = _parse_docx(payload)
        content_type = content_type or (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    elif resolved_kind == "xlsx":
        text, blocks, tables, page_count, title = _parse_xlsx(payload)
        content_type = content_type or (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    elif resolved_kind == "html":
        text, title = extract_html_text(payload)
        blocks = [TextBlock(index=0, text=text)] if text else []
        tables = []
        page_count = None
        content_type = content_type or "text/html"
    elif resolved_kind == "text":
        text = payload.decode("utf-8", errors="replace")
        blocks = [TextBlock(index=0, text=text)] if text.strip() else []
        tables = []
        page_count = None
        title = None
        content_type = content_type or "text/plain"
    else:  # pragma: no cover - 防御
        raise ParseError(f"不支持的类型：{resolved_kind}", error_kind="parse")

    if not text.strip():
        raise ParseError(
            f"{resolved_kind} 解析后正文为空（可能是扫描件或权限受限页），不生成宣称有内容的证据",
            error_kind="parse",
        )

    return RawDocument(
        doc_id=content_doc_id(payload),
        kind=resolved_kind,
        content_type=content_type or guessed_type or "application/octet-stream",
        text=text,
        blocks=blocks,
        tables=tables,
        sha256=sha256_hex(payload),
        byte_size=len(payload),
        page_count=page_count,
        title=title,
        retrieved_at_shanghai=retrieved,
        source_path=filename,
    )


def parse_file(path: str | Path) -> RawDocument:
    """解析本地文件。"""
    file_path = Path(path)
    if not file_path.exists():
        raise ParseError(f"文件不存在：{file_path}", error_kind="parse")
    payload = file_path.read_bytes()
    return parse_bytes(payload, filename=file_path.name)


def find_snippets(text: str, keywords: list[str], *, window: int = 120, limit: int = 5) -> list[str]:
    """在正文中按关键词截取片段，供人工核对时定位原文（不做摘要生成）。"""
    snippets: list[str] = []
    normalized = re.sub(r"\s+", " ", text)
    for keyword in keywords:
        if not keyword:
            continue
        for match in re.finditer(re.escape(keyword), normalized):
            start = max(0, match.start() - window)
            end = min(len(normalized), match.end() + window)
            snippet = normalized[start:end].strip()
            if snippet and snippet not in snippets:
                snippets.append(snippet)
            if len(snippets) >= limit:
                return snippets
    return snippets
