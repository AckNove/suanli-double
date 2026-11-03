"""证据模型与硬闸门。

字段与《目录规范与接口约定 v0.2 §2》《基础设施设计 §6》一致：

    claim_id, project_id, statement, epistemic_tag(fact|inference|hypothesis|missing),
    source_name, url, published_at, retrieved_at_shanghai, excerpt,
    source_type, trust_level, verification_status, conflict_notes, doc_id

**实现补充字段（可空，不改变上述必需字段语义）**——为了让既有 markdown 证据表可无损往返：

    epistemic_note     原始口径括注，如「机构估计·转引」→ 渲染为「事实（机构估计·转引）」
    verification_note  原始核验状态原文，如「部分核验」「未核验（单源但权威）」
    extra_urls         一条结论的其余来源链接（分号分隔，url 保留首个可点链接）

硬闸门：``epistemic_tag == fact`` 且无 URL → 拒写（模型层 + DB CHECK 双保险）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..common.errors import EvidenceGateError, InvalidTransition
from ..common.ids import CLAIM_ID_RE, normalize_project_id
from ..common.timeutil import is_shanghai_iso, to_shanghai_str


class EpistemicTag(str, Enum):
    """认知标签：推断与假设不得当事实写进产品。"""

    FACT = "fact"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    MISSING = "missing"


class VerificationStatus(str, Enum):
    """核验状态机：unverified → cross_checked → disputed → rejected。"""

    UNVERIFIED = "unverified"
    CROSS_CHECKED = "cross_checked"
    DISPUTED = "disputed"
    REJECTED = "rejected"


#: 允许的状态迁移（rejected 为终态；disputed 与 cross_checked 可互相回摆但必须留痕）
ALLOWED_TRANSITIONS: dict[VerificationStatus, frozenset[VerificationStatus]] = {
    VerificationStatus.UNVERIFIED: frozenset(
        {VerificationStatus.CROSS_CHECKED, VerificationStatus.DISPUTED, VerificationStatus.REJECTED}
    ),
    VerificationStatus.CROSS_CHECKED: frozenset(
        {VerificationStatus.DISPUTED, VerificationStatus.REJECTED}
    ),
    VerificationStatus.DISPUTED: frozenset(
        {VerificationStatus.CROSS_CHECKED, VerificationStatus.REJECTED}
    ),
    VerificationStatus.REJECTED: frozenset(),
}

#: markdown 证据表里的中文标签 → 枚举
TAG_ALIASES: dict[str, EpistemicTag] = {
    "fact": EpistemicTag.FACT,
    "事实": EpistemicTag.FACT,
    "inference": EpistemicTag.INFERENCE,
    "推断": EpistemicTag.INFERENCE,
    "hypothesis": EpistemicTag.HYPOTHESIS,
    "假设": EpistemicTag.HYPOTHESIS,
    "missing": EpistemicTag.MISSING,
    "【缺失】": EpistemicTag.MISSING,
    "缺失": EpistemicTag.MISSING,
}

#: 核验状态原文 → 枚举（宁可低报，不把「已打开」当成交叉核验）
STATUS_ALIASES: dict[str, VerificationStatus] = {
    "cross_checked": VerificationStatus.CROSS_CHECKED,
    "已对照": VerificationStatus.CROSS_CHECKED,
    "已核验": VerificationStatus.CROSS_CHECKED,
    "已打开/互引一致": VerificationStatus.CROSS_CHECKED,
    "disputed": VerificationStatus.DISPUTED,
    "冲突待消": VerificationStatus.DISPUTED,
    "冲突": VerificationStatus.DISPUTED,
    "rejected": VerificationStatus.REJECTED,
    "已否决": VerificationStatus.REJECTED,
    "unverified": VerificationStatus.UNVERIFIED,
    "未核验": VerificationStatus.UNVERIFIED,
    "待核验": VerificationStatus.UNVERIFIED,
    "部分核验": VerificationStatus.UNVERIFIED,
    "已打开": VerificationStatus.UNVERIFIED,
}

TAG_LABELS: dict[EpistemicTag, str] = {
    EpistemicTag.FACT: "事实",
    EpistemicTag.INFERENCE: "推断",
    EpistemicTag.HYPOTHESIS: "假设",
    EpistemicTag.MISSING: "【缺失】",
}

STATUS_LABELS: dict[VerificationStatus, str] = {
    VerificationStatus.UNVERIFIED: "未核验",
    VerificationStatus.CROSS_CHECKED: "已对照",
    VerificationStatus.DISPUTED: "冲突待消",
    VerificationStatus.REJECTED: "已否决",
}


def _match_alias(text: str, aliases: dict[str, Any]) -> Any | None:
    """按「最长前缀优先」匹配别名，避免「已打开」抢先命中「已打开/互引一致」。"""
    raw = (text or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    for key in sorted(aliases, key=len, reverse=True):
        if lowered.startswith(key.lower()):
            return aliases[key]
    return None


def coerce_tag(value: str) -> EpistemicTag:
    """把 ``事实（机构估计）`` ``【缺失】`` 等写法归一到枚举（前缀匹配）。"""
    if not (value or "").strip():
        raise ValueError("epistemic_tag 不能为空")
    matched = _match_alias(value, TAG_ALIASES)
    if matched is None:
        raise ValueError(
            f"无法识别的 epistemic_tag：{value!r}（可用 fact / inference / hypothesis / missing）"
        )
    return matched


def coerce_status(value: str) -> VerificationStatus:
    """把 ``未核验`` ``部分核验`` ``冲突待消`` 等写法归一到枚举。"""
    matched = _match_alias(value, STATUS_ALIASES)
    return matched if matched is not None else VerificationStatus.UNVERIFIED


def looks_like_status(value: str) -> bool:
    """判断某格内容是否是「核验状态」而非「认知标签」（用于识别证据表的列错位）。"""
    return _match_alias(value, STATUS_ALIASES) is not None


def coerce_epistemic_note(value: str) -> str | None:
    """从 ``事实（机构估计·转引）`` 里抽出括注部分的原文（取第一个括号内容）。"""
    text = (value or "").strip()
    for opener, closer in (("（", "）"), ("(", ")")):
        start = text.find(opener)
        end = text.find(closer, start + 1)
        if start != -1 and end > start:
            note = text[start + 1 : end].strip()
            return note or None
    return None


class EvidenceClaim(BaseModel):
    """一条可引用、可核验的证据结论。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    claim_id: str = Field(..., min_length=4, max_length=12)
    project_id: str
    statement: str = Field(..., min_length=1)
    epistemic_tag: EpistemicTag
    source_name: str | None = None
    url: str | None = None
    published_at: str | None = None
    retrieved_at_shanghai: str
    excerpt: str | None = None
    source_type: str | None = None
    trust_level: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    conflict_notes: str | None = None
    doc_id: str | None = None

    # 实现补充字段（可空）
    epistemic_note: str | None = None
    verification_note: str | None = None
    extra_urls: str | None = None

    # ---------------- 校验 ----------------

    @field_validator("claim_id")
    @classmethod
    def _check_claim_id(cls, value: str) -> str:
        if not CLAIM_ID_RE.match(value):
            raise ValueError(f"claim_id 格式应为 A-E01 / B-E13，收到 {value!r}")
        return value

    @field_validator("project_id")
    @classmethod
    def _check_project(cls, value: str) -> str:
        return normalize_project_id(value)

    @field_validator("epistemic_tag", mode="before")
    @classmethod
    def _coerce_tag(cls, value: Any) -> Any:
        return coerce_tag(value) if isinstance(value, str) else value

    @field_validator("verification_status", mode="before")
    @classmethod
    def _coerce_status(cls, value: Any) -> Any:
        return coerce_status(value) if isinstance(value, str) else value

    @field_validator("retrieved_at_shanghai")
    @classmethod
    def _check_retrieved(cls, value: str) -> str:
        if not value:
            raise ValueError("retrieved_at_shanghai 不能为空")
        if is_shanghai_iso(value):
            return value
        return to_shanghai_str(value)

    @field_validator("published_at")
    @classmethod
    def _check_published(cls, value: str | None) -> str | None:
        if not value:
            return None
        if value.strip() in {"—", "-", "无", "N/A", "n/a"}:
            return None
        try:
            return to_shanghai_str(value)
        except ValueError:
            # 原始表存在「页面标注2026相关」「2025-11」等模糊写法：保留原文，不臆造精确日期
            return value

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or cleaned in {"—", "-", "无", "同上", "N/A", "n/a"}:
            return None
        return cleaned

    @model_validator(mode="after")
    def _gate(self) -> "EvidenceClaim":
        """硬闸门：fact 必须可打开 URL；missing 为缺口占位不强制 URL。"""
        if self.epistemic_tag is EpistemicTag.FACT:
            if not self.url:
                raise ValueError("闸门：epistemic_tag=fact 必须有可打开 URL")
            if not self.url.lower().startswith(("http://", "https://")):
                raise ValueError(
                    f"闸门：fact 的 URL 必须是 http(s) 绝对地址，收到 {self.url!r}"
                )
        return self

    # ---------------- 便捷方法 ----------------

    @property
    def citation(self) -> str:
        """报告引用格式。"""
        return f"[@{self.claim_id}]"

    @property
    def all_urls(self) -> list[str]:
        parts = [self.url] if self.url else []
        if self.extra_urls:
            parts.extend(
                chunk.strip() for chunk in self.extra_urls.replace("；", ";").split(";") if chunk.strip()
            )
        return parts

    def to_row(self) -> dict[str, Any]:
        data = self.model_dump()
        data["epistemic_tag"] = self.epistemic_tag.value
        data["verification_status"] = self.verification_status.value
        return data

    @classmethod
    def from_row(cls, row: Any) -> "EvidenceClaim":
        payload = {key: row[key] for key in row.keys()}
        payload.pop("created_at", None)
        payload.pop("updated_at", None)
        return cls(**payload)

    def assert_can_transition(self, target: VerificationStatus) -> None:
        """校验状态迁移是否合法，不合法抛 InvalidTransition。"""
        if target == self.verification_status:
            return
        allowed = ALLOWED_TRANSITIONS[self.verification_status]
        if target not in allowed:
            raise InvalidTransition(
                f"{self.claim_id}: {self.verification_status.value} → {target.value} 非法"
                f"（允许：{', '.join(sorted(s.value for s in allowed)) or '无，已终态'}）"
            )

    def require_url_for_fact(self) -> None:
        """供 DB 层重复校验（防止绕过模型直接写库）。"""
        if self.epistemic_tag is EpistemicTag.FACT and not self.url:
            raise EvidenceGateError(f"{self.claim_id}: fact 必须有 URL")
