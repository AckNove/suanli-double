"""结构化工具日志（JSONL）。

设计文档 §15：记录 request_id / tool / latency / error；失败可分类，便于事后核查「有没有伪造结果」。
日志只记录元数据，**不记录** L2 商务原文。
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..config import get_settings
from .ids import new_request_id
from .timeutil import now_shanghai_str


def _log_path(log_file: Path | None = None) -> Path:
    if log_file is not None:
        return log_file
    settings = get_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    return settings.logs_dir / "tool_calls.jsonl"


def log_event(tool: str, *, log_file: Path | None = None, **fields: Any) -> dict[str, Any]:
    """追加一条工具日志，返回写入的记录。"""
    record: dict[str, Any] = {
        "ts": now_shanghai_str(),
        "tool": tool,
        "request_id": fields.pop("request_id", None) or new_request_id(),
    }
    record.update(fields)

    path = _log_path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


@contextmanager
def tool_span(tool: str, *, log_file: Path | None = None, **fields: Any) -> Iterator[dict[str, Any]]:
    """计时上下文：正常退出记 ok，异常记 error_kind 后原样抛出。"""
    started = time.perf_counter()
    request_id = new_request_id()
    context: dict[str, Any] = {"request_id": request_id, **fields}
    try:
        yield context
    except BaseException as exc:  # noqa: BLE001 - 需要记录全部失败类型后再抛出
        log_event(
            tool,
            log_file=log_file,
            request_id=request_id,
            status="error",
            error_kind=getattr(exc, "error_kind", type(exc).__name__),
            error_detail=str(exc)[:500],
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            **fields,
        )
        raise
    else:
        log_event(
            tool,
            log_file=log_file,
            request_id=request_id,
            status="ok",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            **fields,
        )
