"""时区工具：全项目时间字段统一 Asia/Shanghai。

Windows 上 ``zoneinfo`` 需要 ``tzdata`` 包（已列入依赖）；缺失时给出明确指引而非静默用 UTC。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TZ_NAME = "Asia/Shanghai"

try:
    SHANGHAI = ZoneInfo(_TZ_NAME)
except ZoneInfoNotFoundError as exc:  # pragma: no cover - 依赖缺失时的兜底
    raise RuntimeError(
        "找不到时区数据 Asia/Shanghai。Windows 请先安装 tzdata：pip install tzdata"
    ) from exc

SHANGHAI_OFFSET = timedelta(hours=8)


def now_shanghai() -> datetime:
    """当前时间（带 Asia/Shanghai 时区）。"""
    return datetime.now(tz=SHANGHAI)


def now_shanghai_str() -> str:
    """ISO-8601 字符串，秒级精度，形如 ``2026-11-02T15:33:00+08:00``。"""
    return now_shanghai().replace(microsecond=0).isoformat()


def today_shanghai() -> str:
    """``YYYY-MM-DD``。"""
    return now_shanghai().strftime("%Y-%m-%d")


def stamp_shanghai() -> str:
    """用于 ID 的紧凑时间戳 ``YYYYMMDD``。"""
    return now_shanghai().strftime("%Y%m%d")


def parse_shanghai(value: str) -> datetime:
    """解析时间字符串并校验/转换为 +08:00。

    接受带偏移的 ISO 字符串；不带时区的按上海时区解释。解析失败抛 ValueError。
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("时间字段为空")

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"无法解析时间：{value!r}") from None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def to_shanghai_str(value: str | datetime) -> str:
    """把任意可解析时间规范化为 ``+08:00`` ISO 字符串。"""
    parsed = parse_shanghai(value) if isinstance(value, str) else value.astimezone(SHANGHAI)
    return parsed.replace(microsecond=0).isoformat()


def is_shanghai_iso(value: str) -> bool:
    """校验字符串是否为带 +08:00 偏移的 ISO 时间。"""
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return parsed.utcoffset() == SHANGHAI_OFFSET


def utc_now_str() -> str:
    """UTC ISO（仅供日志对照）。"""
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
