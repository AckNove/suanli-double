"""网络抓取 + 原始资料落盘。

设计文档 §3 / §5 / §15：

- HTML/PDF 内容**只来自真实响应体**；非 2xx 只记状态码与失败分类，绝不生成正文。
- 落盘结构 ``data/raw/{project}/{yyyy}/{mm}/{doc_id}/source.bin + meta.json``，
  正文另存 ``data/text/{doc_id}.md``。
- 失败分类：``network`` / ``parse`` / ``policy_block`` / ``budget``。

搜索层本期**未接任何付费 API**：只提供可插拔 Provider 空壳与人工入队，
未配置就明确报 ``search_failed``，不返回伪造命中。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..common.errors import FetchError
from ..common.ids import content_doc_id, new_request_id, timestamp_for_path
from ..common.logging import log_event
from ..common.timeutil import now_shanghai_str
from ..config import Settings, get_settings

#: 视为「策略/权限拦截」的状态码（换源，不重试到底）
POLICY_BLOCK_STATUS = {401, 403, 407, 429, 451}


@dataclass
class FetchOutcome:
    url: str
    ok: bool
    status_code: int | None
    content_type: str | None
    body: bytes
    retrieved_at_shanghai: str
    error_kind: str | None = None
    error_detail: str | None = None
    final_url: str | None = None
    request_id: str = ""

    @property
    def byte_size(self) -> int:
        return len(self.body)


def _classify_status(status: int) -> str:
    if status in POLICY_BLOCK_STATUS:
        return "policy_block"
    return "network"


def fetch(
    url: str,
    *,
    transport: Any | None = None,
    timeout: float = 25.0,
    user_agent: str | None = None,
    log: bool = True,
) -> FetchOutcome:
    """抓取 URL。任何失败都以 ``ok=False`` 返回并带 ``error_kind``，不抛异常给调用方。

    ``transport`` 可注入 ``httpx.MockTransport`` 用于测试。
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise FetchError("缺少 httpx：pip install httpx", error_kind="network") from exc

    request_id = new_request_id()
    headers = {
        "User-Agent": user_agent
        or "compute-dual/0.1 (research MVP; contact: local operator)",
        "Accept": "text/html,application/pdf,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
    }
    retrieved = now_shanghai_str()

    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, transport=transport, headers=headers
        ) as client:
            response = client.get(url)
    except Exception as exc:  # noqa: BLE001 - 网络层任何异常都降级
        outcome = FetchOutcome(
            url=url,
            ok=False,
            status_code=None,
            content_type=None,
            body=b"",
            retrieved_at_shanghai=retrieved,
            error_kind="network",
            error_detail=f"{type(exc).__name__}: {exc}"[:300],
            request_id=request_id,
        )
        if log:
            log_event(
                "fetch",
                request_id=request_id,
                url=url,
                status="error",
                error_kind=outcome.error_kind,
                error_detail=outcome.error_detail,
            )
        return outcome

    body = response.content or b""
    ok = 200 <= response.status_code < 300
    outcome = FetchOutcome(
        url=url,
        ok=ok,
        status_code=response.status_code,
        content_type=response.headers.get("content-type"),
        body=body if ok else b"",
        retrieved_at_shanghai=retrieved,
        final_url=str(response.url),
        error_kind=None if ok else _classify_status(response.status_code),
        error_detail=None if ok else f"HTTP {response.status_code}（未采信响应体）",
        request_id=request_id,
    )
    if log:
        log_event(
            "fetch",
            request_id=request_id,
            url=url,
            status="ok" if ok else "error",
            http_status=response.status_code,
            error_kind=outcome.error_kind,
            byte_size=outcome.byte_size,
        )
    return outcome


# --------------------------------------------------------------------------- 落盘


@dataclass
class StoredRaw:
    doc_id: str
    raw_path: Path | None
    text_path: Path | None
    meta: dict[str, Any] = field(default_factory=dict)


class RawStore:
    """原始资料与正文的本地存储（不入 Git）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()

    def save(
        self,
        payload: bytes,
        *,
        project_id: str,
        url: str | None,
        content_type: str | None,
        http_status: int | None,
        retrieved_at_shanghai: str,
        source_name: str | None = None,
        text: str | None = None,
        text_markdown: str | None = None,
        parse_status: str = "ok",
        error_kind: str | None = None,
        error_detail: str | None = None,
        page_count: int | None = None,
    ) -> StoredRaw:
        doc_id = content_doc_id(payload)
        year, month = timestamp_for_path()

        doc_dir = self.settings.raw_dir / project_id / year / month / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)

        from ..tools.parse_file import detect_kind  # 局部导入避免循环

        kind = detect_kind(payload, content_type=content_type, url=url) if payload else "unknown"
        suffix = {
            "pdf": ".pdf",
            "docx": ".docx",
            "xlsx": ".xlsx",
            "html": ".html",
            "text": ".txt",
        }.get(kind, ".bin")

        raw_path: Path | None = None
        if payload:
            raw_path = doc_dir / f"source{suffix}"
            raw_path.write_bytes(payload)

        meta = {
            "doc_id": doc_id,
            "project_id": project_id,
            "url": url,
            "source_name": source_name,
            "content_type": content_type,
            "http_status": http_status,
            "byte_size": len(payload),
            "retrieved_at_shanghai": retrieved_at_shanghai,
            "kind": kind,
            "parse_status": parse_status,
            "error_kind": error_kind,
            "error_detail": error_detail,
            "page_count": page_count,
            "raw_file": raw_path.name if raw_path else None,
        }
        (doc_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        text_path: Path | None = None
        if text_markdown is not None:
            text_path = self.settings.text_dir / f"{doc_id}.md"
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(text_markdown, encoding="utf-8")
        elif text:
            text_path = self.settings.text_dir / f"{doc_id}.md"
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(text, encoding="utf-8")

        return StoredRaw(doc_id=doc_id, raw_path=raw_path, text_path=text_path, meta=meta)

    def read_text(self, doc_id: str) -> str | None:
        path = self.settings.text_dir / f"{doc_id}.md"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def read_meta(self, doc_id: str) -> dict[str, Any] | None:
        matches = list((self.settings.raw_dir).glob(f"*/*/*/{doc_id}/meta.json"))
        if not matches:
            return None
        return json.loads(matches[0].read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- 搜索（未接入）


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    rank: int | None = None


class SearchProvider(Protocol):
    """工具契约 §9：search(query) -> hits[]。"""

    name: str

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]: ...


class UnconfiguredSearchProvider:
    """本期默认实现：**未接任何搜索 API**。

    宁可显式失败，也不返回伪造命中。人工找链接后用
    ``ak fetch ingest --url ...`` 入队即可。
    """

    name = "unconfigured"

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        log_event(
            "search",
            status="search_failed",
            error_kind="budget",
            error_detail="未配置搜索 Provider（本期人工入队）",
            query=query,
        )
        raise FetchError(
            "未配置搜索 Provider：本期不接外部搜索 API，请人工提供 URL 后用 `ak fetch ingest --url` 入队",
            error_kind="budget",
        )


def manual_queue_file(settings: Settings | None = None) -> Path:
    """人工 URL 入队文件（每行一个 URL，# 开头为注释）。"""
    settings = settings or get_settings()
    return settings.data_dir / "search_queue.txt"


def read_manual_queue(settings: Settings | None = None) -> list[str]:
    path = manual_queue_file(settings)
    if not path.exists():
        return []
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        urls.append(entry)
    return urls
