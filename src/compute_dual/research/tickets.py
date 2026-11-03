"""研究 ticket 与问题分解（设计 §10）。

Orchestrator 建 ResearchTicket → 分解 ResearchQuestion[] → 派 Search/Fetch/Extract
→ Analyst → Verifier → Writer。本期只实现「建单 / 提问 / 挂证据 / 出草稿」四步，
不实现自动派活与 LLM 写作。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..common.ids import normalize_project_id, stamp_shanghai
from ..common.timeutil import now_shanghai_str
from ..evidence.db import get_claim
from ..evidence.models import EvidenceClaim


@dataclass
class Question:
    question_id: str
    ticket_id: str
    seq: int
    question: str
    status: str
    claims: list[EvidenceClaim] = field(default_factory=list)


@dataclass
class Ticket:
    ticket_id: str
    project_id: str
    topic: str
    scope: str | None
    status: str
    created_at_shanghai: str
    questions: list[Question] = field(default_factory=list)

    @property
    def claim_count(self) -> int:
        seen: set[str] = set()
        for question in self.questions:
            for claim in question.claims:
                seen.add(claim.claim_id)
        return len(seen)


def _next_ticket_seq(conn: sqlite3.Connection, day: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE ticket_id LIKE ?", (f"T-{day}-%",)
    ).fetchone()
    return (row["n"] or 0) + 1


def create_ticket(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    topic: str,
    scope: str | None = None,
) -> Ticket:
    """新建工单，ID 形如 ``T-20261102-001``。"""
    project = normalize_project_id(project_id)
    if not (topic or "").strip():
        raise ValueError("topic 不能为空")

    day = stamp_shanghai()
    seq = _next_ticket_seq(conn, day)
    ticket_id = f"T-{day}-{seq:03d}"
    created = now_shanghai_str()

    conn.execute(
        "INSERT INTO tickets (ticket_id, project_id, topic, scope, status, created_at_shanghai)"
        " VALUES (?,?,?,?,?,?)",
        (ticket_id, project, topic.strip(), scope, "open", created),
    )
    conn.commit()
    return Ticket(
        ticket_id=ticket_id,
        project_id=project,
        topic=topic.strip(),
        scope=scope,
        status="open",
        created_at_shanghai=created,
    )


def add_question(conn: sqlite3.Connection, ticket_id: str, question: str) -> Question:
    """给工单追加一个研究问题，ID 形如 ``T-20261102-001-Q1``。"""
    ticket = get_ticket(conn, ticket_id)
    if ticket is None:
        raise ValueError(f"工单不存在：{ticket_id}")
    if not (question or "").strip():
        raise ValueError("question 不能为空")

    seq = len(ticket.questions) + 1
    question_id = f"{ticket_id}-Q{seq}"
    conn.execute(
        "INSERT INTO ticket_questions (question_id, ticket_id, seq, question, status)"
        " VALUES (?,?,?,?,?)",
        (question_id, ticket_id, seq, question.strip(), "open"),
    )
    conn.commit()
    return Question(
        question_id=question_id,
        ticket_id=ticket_id,
        seq=seq,
        question=question.strip(),
        status="open",
    )


def link_claim(conn: sqlite3.Connection, ticket_id: str, question_id: str, claim_id: str) -> None:
    """把一条证据挂到某个问题下（报告引用据此生成）。"""
    if get_ticket(conn, ticket_id) is None:
        raise ValueError(f"工单不存在：{ticket_id}")
    row = conn.execute(
        "SELECT question_id FROM ticket_questions WHERE question_id = ? AND ticket_id = ?",
        (question_id, ticket_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"问题不属于该工单：{question_id} / {ticket_id}")
    if get_claim(conn, claim_id) is None:
        raise ValueError(f"证据不存在：{claim_id}（先 ak evidence add 或 import-md）")

    conn.execute(
        "INSERT OR IGNORE INTO ticket_claims (ticket_id, question_id, claim_id, linked_at_shanghai)"
        " VALUES (?,?,?,?)",
        (ticket_id, question_id, claim_id, now_shanghai_str()),
    )
    conn.commit()


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket | None:
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None:
        return None

    question_rows = conn.execute(
        "SELECT * FROM ticket_questions WHERE ticket_id = ? ORDER BY seq", (ticket_id,)
    ).fetchall()

    questions: list[Question] = []
    for question_row in question_rows:
        claim_rows = conn.execute(
            "SELECT claim_id FROM ticket_claims WHERE question_id = ? ORDER BY claim_id",
            (question_row["question_id"],),
        ).fetchall()
        claims = [c for c in (get_claim(conn, r["claim_id"]) for r in claim_rows) if c is not None]
        questions.append(
            Question(
                question_id=question_row["question_id"],
                ticket_id=question_row["ticket_id"],
                seq=question_row["seq"],
                question=question_row["question"],
                status=question_row["status"],
                claims=claims,
            )
        )

    return Ticket(
        ticket_id=row["ticket_id"],
        project_id=row["project_id"],
        topic=row["topic"],
        scope=row["scope"],
        status=row["status"],
        created_at_shanghai=row["created_at_shanghai"],
        questions=questions,
    )


def list_tickets(conn: sqlite3.Connection, project_id: str | None = None) -> list[sqlite3.Row]:
    if project_id:
        return conn.execute(
            "SELECT * FROM tickets WHERE project_id = ? ORDER BY ticket_id DESC", (project_id,)
        ).fetchall()
    return conn.execute("SELECT * FROM tickets ORDER BY ticket_id DESC").fetchall()
