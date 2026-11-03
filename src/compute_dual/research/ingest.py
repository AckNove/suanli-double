"""摄取：URL / 本地文件 → 原始库 + 正文文本 + documents 索引。

失败一律显式记录（``parse_status='fetch_failed'|'parse_failed'``），
**不**产出任何证据结论 —— 抓不到就标缺口，不编造。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common.ids import sha256_hex
from ..common.timeutil import now_shanghai_str
from ..evidence.db import register_document
from ..tools.fetch import FetchOutcome, RawStore, fetch
from ..tools.parse_file import RawDocument, parse_bytes


@dataclass
class IngestResult:
    ok: bool
    doc_id: str | None
    url: str | None
    kind: str | None
    status_code: int | None
    chars: int = 0
    pages: int | None = None
    tables: int = 0
    error_kind: str | None = None
    error_detail: str | None = None
    raw_path: Path | None = None
    text_path: Path | None = None
    document: RawDocument | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "doc_id": self.doc_id,
            "url": self.url,
            "kind": self.kind,
            "http_status": self.status_code,
            "chars": self.chars,
            "pages": self.pages,
            "tables": self.tables,
            "error_kind": self.error_kind,
            "error_detail": self.error_detail,
            "raw_path": str(self.raw_path) if self.raw_path else None,
            "text_path": str(self.text_path) if self.text_path else None,
        }


def _record_failure_row(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    url: str | None,
    source_name: str | None,
    parse_status: str,
    error_kind: str,
    error_detail: str,
    http_status: int | None,
    retrieved_at_shanghai: str,
) -> str:
    """抓取/解析失败也要留痕：用 URL 哈希做 doc_id，正文为空且标明状态。"""
    identity = url or (f"file:{source_name}" if source_name else "unknown")
    key = identity.encode("utf-8")
    return register_document(
        conn,
        project_id=project_id,
        payload=key,
        url=url,
        source_name=source_name,
        content_type=None,
        http_status=http_status,
        retrieved_at_shanghai=retrieved_at_shanghai,
        raw_path=None,
        text_path=None,
        parse_status=parse_status,
        error_kind=error_kind,
        error_detail=error_detail[:500],
    )


def ingest_bytes(
    conn: sqlite3.Connection,
    payload: bytes,
    *,
    project_id: str,
    url: str | None = None,
    source_name: str | None = None,
    content_type: str | None = None,
    http_status: int | None = None,
    store: RawStore | None = None,
) -> IngestResult:
    """解析并落盘一段已知内容（已抓到的响应体或本地文件）。"""
    store = store or RawStore()
    retrieved = now_shanghai_str()

    try:
        document = parse_bytes(
            payload, content_type=content_type, filename=source_name, url=url
        )
    except Exception as exc:  # noqa: BLE001 - 解析失败降级，保留原件
        error_kind = getattr(exc, "error_kind", "parse")
        saved = store.save(
            payload,
            project_id=project_id,
            url=url,
            content_type=content_type,
            http_status=http_status,
            retrieved_at_shanghai=retrieved,
            source_name=source_name,
            parse_status="parse_failed",
            error_kind=error_kind,
            error_detail=str(exc),
        )
        doc_id = register_document(
            conn,
            project_id=project_id,
            payload=payload,
            url=url,
            source_name=source_name,
            content_type=content_type,
            http_status=http_status,
            retrieved_at_shanghai=retrieved,
            raw_path=str(saved.raw_path) if saved.raw_path else None,
            text_path=None,
            parse_status="parse_failed",
            error_kind=error_kind,
            error_detail=str(exc)[:500],
        )
        return IngestResult(
            ok=False,
            doc_id=doc_id,
            url=url,
            kind=None,
            status_code=http_status,
            error_kind=error_kind,
            error_detail=str(exc),
            raw_path=saved.raw_path,
        )

    saved = store.save(
        payload,
        project_id=project_id,
        url=url,
        content_type=document.content_type,
        http_status=http_status,
        retrieved_at_shanghai=document.retrieved_at_shanghai,
        source_name=source_name,
        text_markdown=document.to_markdown(),
        page_count=document.page_count,
    )
    doc_id = register_document(
        conn,
        project_id=project_id,
        payload=payload,
        url=url,
        source_name=source_name,
        content_type=document.content_type,
        http_status=http_status,
        retrieved_at_shanghai=document.retrieved_at_shanghai,
        raw_path=str(saved.raw_path) if saved.raw_path else None,
        text_path=str(saved.text_path) if saved.text_path else None,
        page_count=document.page_count,
        parse_status="ok",
    )

    return IngestResult(
        ok=True,
        doc_id=doc_id,
        url=url,
        kind=document.kind,
        status_code=http_status,
        chars=len(document.text),
        pages=document.page_count,
        tables=len(document.tables),
        raw_path=saved.raw_path,
        text_path=saved.text_path,
        document=document,
    )


def ingest_url(
    conn: sqlite3.Connection,
    url: str,
    *,
    project_id: str,
    source_name: str | None = None,
    store: RawStore | None = None,
    transport: Any | None = None,
) -> IngestResult:
    """抓取 URL 并入库。抓取失败只落失败记录，不产生正文与证据。"""
    outcome: FetchOutcome = fetch(url, transport=transport)

    if not outcome.ok:
        doc_id = _record_failure_row(
            conn,
            project_id=project_id,
            url=url,
            source_name=source_name,
            parse_status="fetch_failed",
            error_kind=outcome.error_kind or "network",
            error_detail=outcome.error_detail or "抓取失败",
            http_status=outcome.status_code,
            retrieved_at_shanghai=outcome.retrieved_at_shanghai,
        )
        return IngestResult(
            ok=False,
            doc_id=doc_id,
            url=url,
            kind=None,
            status_code=outcome.status_code,
            error_kind=outcome.error_kind or "network",
            error_detail=outcome.error_detail,
        )

    return ingest_bytes(
        conn,
        outcome.body,
        project_id=project_id,
        url=url,
        source_name=source_name,
        content_type=outcome.content_type,
        http_status=outcome.status_code,
        store=store,
    )


def ingest_file(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    project_id: str,
    store: RawStore | None = None,
) -> IngestResult:
    """解析本地文件并入库（对应交接文档「人工粘贴 URL/文件入队」路径）。"""
    file_path = Path(path)
    if not file_path.exists():
        return IngestResult(
            ok=False,
            doc_id=None,
            url=None,
            kind=None,
            status_code=None,
            error_kind="parse",
            error_detail=f"文件不存在：{file_path}",
        )
    payload = file_path.read_bytes()
    return ingest_bytes(
        conn,
        payload,
        project_id=project_id,
        url=None,
        source_name=file_path.name,
        store=store,
    )


def read_stored_text(doc_id: str, store: RawStore | None = None) -> str | None:
    """读回已落盘的正文（供人工核对、摘录）。"""
    return (store or RawStore()).read_text(doc_id)


def document_sha(conn: sqlite3.Connection, doc_id: str) -> str | None:
    row = conn.execute("SELECT sha256 FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    return row["sha256"] if row else None


def sha_of(payload: bytes) -> str:
    return sha256_hex(payload)
