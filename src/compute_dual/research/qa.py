"""质量评测（设计 §12）。

自动指标：无摘录结论占比、epistemic 标签覆盖、冲突待消数、待核验占比。
人工项（准确性、是否夸大）无法自动判定，故 ``publishable=False`` 时一律禁止对外。

不达标的判定写入 ``qa_reviews`` 表，可回溯。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..evidence.db import count_by, list_claims, records_qa
from ..evidence.models import EpistemicTag, VerificationStatus


@dataclass
class QaMetric:
    key: str
    label: str
    value: float
    display: str
    passed: bool
    threshold: float | None = None
    blocking: bool = True
    detail: str | None = None


@dataclass
class QaReport:
    project_id: str
    ticket_id: str | None
    metrics: list[QaMetric] = field(default_factory=list)
    tag_counts: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    total_claims: int = 0

    @property
    def publishable(self) -> bool:
        return self.total_claims > 0 and all(
            metric.passed for metric in self.metrics if metric.blocking
        )

    @property
    def blocking_reasons(self) -> list[str]:
        reasons = []
        if self.total_claims == 0:
            reasons.append("无任何证据条目")
        for metric in self.metrics:
            if metric.blocking and not metric.passed:
                reasons.append(f"{metric.label} 不达标（{metric.display}）")
        return reasons

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "ticket_id": self.ticket_id,
            "total_claims": self.total_claims,
            "publishable": self.publishable,
            "metrics": [
                {
                    "key": m.key,
                    "label": m.label,
                    "value": m.value,
                    "display": m.display,
                    "passed": m.passed,
                    "threshold": m.threshold,
                }
                for m in self.metrics
            ],
            "tag_counts": self.tag_counts,
            "status_counts": self.status_counts,
            "blocking_reasons": self.blocking_reasons,
        }


def run_qa(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    ticket_id: str | None = None,
    persist: bool = False,
    no_excerpt_threshold: float = 0.0,
    unverified_threshold: float = 0.6,
) -> QaReport:
    """对某项目（或工单引用集合）计算质量指标。"""
    if ticket_id:
        claims = _claims_for_ticket(conn, project_id, ticket_id)
    else:
        claims = list_claims(conn, project_id=project_id)

    total = len(claims)
    report = QaReport(
        project_id=project_id,
        ticket_id=ticket_id,
        total_claims=total,
        tag_counts=_count(claims, lambda c: c.epistemic_tag.value),
        status_counts=_count(claims, lambda c: c.verification_status.value),
    )

    substantive = [c for c in claims if c.epistemic_tag is not EpistemicTag.MISSING]
    missing = [c for c in claims if c.epistemic_tag is EpistemicTag.MISSING]
    no_excerpt = [c for c in substantive if not (c.excerpt or "").strip()]
    disputed = [c for c in claims if c.verification_status is VerificationStatus.DISPUTED]
    unverified = [c for c in claims if c.verification_status is VerificationStatus.UNVERIFIED]
    no_source_name = [c for c in claims if not (c.source_name or "").strip()]

    no_excerpt_ratio = (len(no_excerpt) / len(substantive)) if substantive else 0.0
    unverified_ratio = (len(unverified) / total) if total else 0.0

    report.metrics = [
        QaMetric(
            key="no_excerpt_ratio",
            label="无摘录结论占比",
            value=round(no_excerpt_ratio, 4),
            display=f"{no_excerpt_ratio:.0%}（{len(no_excerpt)}/{len(substantive)}）",
            passed=no_excerpt_ratio <= no_excerpt_threshold,
            threshold=no_excerpt_threshold,
            blocking=True,
            detail="、".join(c.claim_id for c in no_excerpt[:10]) or None,
        ),
        QaMetric(
            key="disputed_count",
            label="冲突待消条数",
            value=float(len(disputed)),
            display=f"{len(disputed)} 条",
            passed=len(disputed) == 0,
            threshold=0.0,
            blocking=True,
            detail="、".join(c.claim_id for c in disputed) or None,
        ),
        QaMetric(
            key="missing_count",
            label="缺失占位数",
            value=float(len(missing)),
            display=f"{len(missing)} 条",
            passed=len(missing) == 0,
            threshold=0.0,
            blocking=True,
            detail="、".join(c.claim_id for c in missing) or None,
        ),
        QaMetric(
            key="unverified_ratio",
            label="待核验占比",
            value=round(unverified_ratio, 4),
            display=f"{unverified_ratio:.0%}（{len(unverified)}/{total}）",
            passed=unverified_ratio <= unverified_threshold,
            threshold=unverified_threshold,
            blocking=False,
        ),
        QaMetric(
            key="no_source_name_count",
            label="无来源名称条数",
            value=float(len(no_source_name)),
            display=f"{len(no_source_name)} 条",
            passed=len(no_source_name) == 0,
            threshold=0.0,
            blocking=False,
            detail="、".join(c.claim_id for c in no_source_name[:10]) or None,
        ),
    ]

    if persist:
        records_qa(conn, report)

    return report


def _count(claims, key_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in claims:
        key = key_fn(claim)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _claims_for_ticket(conn: sqlite3.Connection, project_id: str, ticket_id: str):
    from ..evidence.db import get_claim  # 局部导入避免循环

    rows = conn.execute(
        "SELECT DISTINCT claim_id FROM ticket_claims WHERE ticket_id = ?", (ticket_id,)
    ).fetchall()
    claims = [c for c in (get_claim(conn, row["claim_id"]) for row in rows) if c is not None]
    return [c for c in claims if c.project_id == project_id] or claims


def distribution(conn: sqlite3.Connection, project_id: str) -> dict[str, dict[str, int]]:
    """标签/状态分布（CLI 展示用）。"""
    return {
        "epistemic_tag": count_by(conn, "epistemic_tag", project_id),
        "verification_status": count_by(conn, "verification_status", project_id),
    }
