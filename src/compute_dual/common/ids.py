"""ID 生成与常量。

- 证据 ID 沿用交接包既有格式：``A-E01``（项目A）、``B-E13``（项目B）。
- 原始资料 ID 用内容哈希前缀，保证同内容不重复落盘。
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime

from .timeutil import now_shanghai, stamp_shanghai

# 项目内代号 → 证据 ID 前缀（对齐 A-算力之王AK / B-中国算力出海）
PROJECT_PREFIX = {
    "ak": "A",
    "outbound": "B",
    "shared": "S",
}

PROJECT_ALIASES = {
    "a": "ak",
    "ak": "ak",
    "算力之王ak": "ak",
    "b": "outbound",
    "outbound": "outbound",
    "出海": "outbound",
    "中国算力出海": "outbound",
    "shared": "shared",
    "共享": "shared",
}

CLAIM_ID_RE = re.compile(r"^[A-Z]{1,3}-E\d{1,4}$")
PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
TICKET_ID_RE = re.compile(r"^T-\d{8}-\d{3}$")


def normalize_project_id(project: str) -> str:
    """把 ``A`` / ``ak`` / ``出海`` 等写法归一为项目 id。"""
    key = (project or "").strip().lower()
    if key in PROJECT_ALIASES:
        return PROJECT_ALIASES[key]
    if PROJECT_ID_RE.match(key):
        return key
    raise ValueError(f"无法识别的 project：{project!r}（可用 ak / outbound / shared）")


def claim_prefix(project_id: str) -> str:
    """项目对应的证据 ID 前缀；未知项目取项目名首字母大写。"""
    return PROJECT_PREFIX.get(project_id, project_id[:1].upper() or "X")


def content_doc_id(payload: bytes) -> str:
    """内容哈希 → doc_id（16 位十六进制）。"""
    return hashlib.sha256(payload).hexdigest()[:16]


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def new_request_id() -> str:
    """一次工具调用的追踪号。"""
    return f"req-{now_shanghai().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def new_ticket_id() -> str:
    """``T-YYYYMMDD-NNN``；NNN 由调用方按当日序号补入时使用随机兜底。"""
    return f"T-{stamp_shanghai()}-{secrets.randbelow(900) + 100}"


def sequence_suffix(seq: int) -> str:
    """证据序号补零：1 → ``01``，13 → ``13``，100 → ``100``。"""
    return f"{seq:02d}"


def timestamp_for_path(dt: datetime | None = None) -> tuple[str, str]:
    """返回 (YYYY, MM) 供 ``data/raw/{project}/{yyyy}/{mm}/`` 使用。"""
    moment = dt or now_shanghai()
    return moment.strftime("%Y"), moment.strftime("%m")
