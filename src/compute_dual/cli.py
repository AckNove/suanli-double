"""``ak`` 命令行入口（Typer）。

设计取舍：前期 CLI 优先，不做 FastAPI（《后续开发计划》§3）。
每个子命令都能单独验收，失败给出可读原因与退出码。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .common.errors import (
    ComputeError,
    EvidenceGateError,
    InvalidTransition,
    Level2AccessDenied,
)
from .common.ids import normalize_project_id
from .common.timeutil import SHANGHAI, now_shanghai_str
from .config import REPO_ROOT, get_settings
from .evidence import db as evdb
from .evidence.markdown_io import import_markdown, render_markdown
from .evidence.models import EvidenceClaim, EpistemicTag, VerificationStatus
from .matching.csvio import load_demands, load_suppliers
from .matching.hardfilter import STATUS_LABELS, match_demand, match_one
from .matching.report import render_clarify, render_match_record, render_summary, write_text
from .private.guard import require_l2_access
from .private.store import PrivateStore, verify_isolation
from .research.draft import write_draft
from .research.ingest import ingest_file, ingest_url, read_stored_text
from .research.qa import run_qa
from .research.tickets import add_question, create_ticket, get_ticket, link_claim, list_tickets

console = Console()
app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="算力双项目共享工具层 MVP：证据 / 抓取解析 / 研究工单 / 供需匹配 / 敏感隔离",
)

evidence_app = typer.Typer(no_args_is_help=True, help="证据管理（T12）")
fetch_app = typer.Typer(no_args_is_help=True, help="抓取与解析（T13）")
ticket_app = typer.Typer(no_args_is_help=True, help="研究工单（T14）")
research_app = typer.Typer(no_args_is_help=True, help="研究工作流（T14）")
qa_app = typer.Typer(no_args_is_help=True, help="质量评测")
match_app = typer.Typer(no_args_is_help=True, help="供需匹配（T15）")
private_app = typer.Typer(no_args_is_help=True, help="L2 私密库与审计（T16）")

app.add_typer(evidence_app, name="evidence")
app.add_typer(fetch_app, name="fetch")
app.add_typer(ticket_app, name="ticket")
app.add_typer(research_app, name="research")
app.add_typer(qa_app, name="qa")
app.add_typer(match_app, name="match")
app.add_typer(private_app, name="private")

DEFAULT_SUPPLIERS = "data/samples/suppliers_sample.csv"
DEFAULT_DEMANDS = "data/samples/demands_sample.csv"


# --------------------------------------------------------------------------- 通用工具


def _fail(message: str, code: int = 2) -> None:
    console.print(f"[bold red]失败[/] {message}")
    raise typer.Exit(code=code)


def _ok(message: str) -> None:
    console.print(f"[bold green]完成[/] {message}")


def _echo_json(payload: object) -> None:
    """输出机器可读 JSON。

    刻意不用 ``console.print_json``：rich 会按终端宽度换行并加颜色，
    会在字符串值里插入真实换行，从而破坏 JSON（管道给 jq / json.tool 会失败）。
    """
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _ensure_utf8_stdio() -> None:
    """Windows 上输出被重定向时，Python 默认用 GBK 写 stdout，中文会 UnicodeEncodeError。

    这里只在**非终端**（重定向到文件/管道）时把编码切到 UTF-8，
    保证 ``ak ... --json > out.json`` 与 ``| jq`` 可用；真实终端仍走 rich。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure") and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 某些包装流不支持
            continue


def _research_conn(init: bool = True) -> sqlite3.Connection:
    settings = get_settings()
    settings.ensure_dirs()
    connection = evdb.connect(settings.research_db)
    if init:
        evdb.init_schema(connection)
    return connection


def _read_maybe_stdin(value: Optional[str], file_path: Optional[str]) -> Optional[str]:
    if file_path:
        if file_path == "-":
            return sys.stdin.read().strip()
        return Path(file_path).read_text(encoding="utf-8").strip()
    if value == "-":
        return sys.stdin.read().strip()
    return value


def _print_claim(claim: EvidenceClaim, as_json: bool = False) -> None:
    if as_json:
        _echo_json((claim.model_dump(mode="json")))
        return

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="cyan", width=22)
    table.add_column()
    rows = [
        ("claim_id", claim.claim_id),
        ("project_id", claim.project_id),
        ("statement", claim.statement),
        ("epistemic_tag", f"{claim.epistemic_tag.value}（{claim.epistemic_note}）" if claim.epistemic_note else claim.epistemic_tag.value),
        ("verification_status", claim.verification_status.value),
        ("source_name", claim.source_name or "—"),
        ("url", claim.url or "—"),
        ("extra_urls", claim.extra_urls or "—"),
        ("published_at", claim.published_at or "—"),
        ("retrieved_at_shanghai", claim.retrieved_at_shanghai),
        ("source_type", claim.source_type or "—"),
        ("trust_level", claim.trust_level or "—"),
        ("excerpt", claim.excerpt or "—"),
        ("conflict_notes", claim.conflict_notes or "—"),
        ("doc_id", claim.doc_id or "—"),
    ]
    for key, value in rows:
        table.add_row(key, str(value))
    console.print(table)


# --------------------------------------------------------------------------- 顶层命令


@app.command("version")
def version_command() -> None:
    """打印版本。"""
    console.print(f"compute-dual {__version__}")


@app.command("doctor")
def doctor() -> None:
    """环境自检：Python / 时区 / 依赖 / 数据库 / 隔离状态。"""
    settings = get_settings()
    table = Table(title="环境自检", show_lines=False)
    table.add_column("项", style="cyan")
    table.add_column("值")
    table.add_column("结论")

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    table.add_row("Python", python_version, "OK" if sys.version_info >= (3, 11) else "需 3.11+")
    table.add_row("时区", now_shanghai_str(), "OK" if SHANGHAI else "缺失 tzdata")

    for module in ("typer", "rich", "httpx", "pydantic", "pymupdf", "docx", "openpyxl", "trafilatura"):
        try:
            __import__(module)
            table.add_row(f"依赖 {module}", "已安装", "OK")
        except ImportError:
            table.add_row(f"依赖 {module}", "缺失", "需 pip install")

    table.add_row("研究库", str(settings.research_db), "存在" if settings.research_db.exists() else "未初始化")
    table.add_row("私密库", str(settings.private_db), "存在" if settings.private_db.exists() else "未初始化")
    table.add_row("数据目录", str(settings.data_dir), "OK")

    if settings.research_db.exists():
        connection = _research_conn()
        try:
            total = connection.execute("SELECT COUNT(*) AS n FROM evidence_claims").fetchone()["n"]
            docs = connection.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
            table.add_row("证据条数", str(total), "OK")
            table.add_row("已抓文档", str(docs), "OK")
        finally:
            connection.close()

    isolation = verify_isolation(settings.research_db, settings.private_db)
    table.add_row("研究库/私密库隔离", "已隔离" if isolation["isolated"] else "未验证", "OK" if isolation["isolated"] else "注意")

    console.print(table)


@app.command("db")
def db_command(
    action: str = typer.Argument("init", help="init：建表（幂等）"),
    force: bool = typer.Option(False, "--force", help="删除已有研究库后重建（危险）"),
) -> None:
    """研究库维护。"""
    settings = get_settings()
    settings.ensure_dirs()

    if force and settings.research_db.exists():
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(settings.research_db) + suffix)
            if candidate.exists():
                candidate.unlink()
        console.print("[yellow]已删除旧研究库[/]")

    if action != "init":
        _fail(f"不支持的动作：{action}")

    connection = _research_conn()
    try:
        tables = sorted(evdb.table_names(connection))
    finally:
        connection.close()
    _ok(f"研究库就绪：{settings.research_db}")
    console.print("表：" + ", ".join(tables))


# --------------------------------------------------------------------------- 证据


@evidence_app.command("add")
def evidence_add(
    project: str = typer.Option(..., "--project", "-p", help="ak / outbound / shared"),
    tag: str = typer.Option(..., "--tag", "-t", help="fact / inference / hypothesis / missing"),
    statement: str = typer.Option(..., "--statement", "-s", help="结论内容"),
    url: Optional[str] = typer.Option(None, "--url", help="来源链接（fact 必填）"),
    source_name: Optional[str] = typer.Option(None, "--source-name", help="来源名称"),
    source_type: Optional[str] = typer.Option(None, "--source-type", help="来源类型"),
    trust_level: Optional[str] = typer.Option(None, "--trust-level", help="可信度"),
    published_at: Optional[str] = typer.Option(None, "--published-at", help="发布时间"),
    excerpt: Optional[str] = typer.Option(None, "--excerpt", "-e", help="原文摘录；- 表示 stdin"),
    excerpt_file: Optional[str] = typer.Option(None, "--excerpt-file", help="从文件读取摘录"),
    conflict_notes: Optional[str] = typer.Option(None, "--conflict-notes", help="冲突说明"),
    doc_id: Optional[str] = typer.Option(None, "--doc-id", help="关联原始资料 ID"),
    claim_id: Optional[str] = typer.Option(None, "--claim-id", help="指定 ID；默认自动递增"),
) -> None:
    """写入一条证据（fact 无 URL 会被硬闸门拒绝）。"""
    try:
        project_id = normalize_project_id(project)
    except ValueError as exc:
        _fail(str(exc))

    connection = _research_conn()
    try:
        resolved_id = claim_id or evdb.next_claim_id(connection, project_id)
        payload_excerpt = _read_maybe_stdin(excerpt, excerpt_file)
        claim = EvidenceClaim(
            claim_id=resolved_id,
            project_id=project_id,
            statement=statement,
            epistemic_tag=tag,
            source_name=source_name,
            url=url,
            published_at=published_at,
            retrieved_at_shanghai=now_shanghai_str(),
            excerpt=payload_excerpt,
            source_type=source_type,
            trust_level=trust_level,
            conflict_notes=conflict_notes,
            doc_id=doc_id,
        )
        evdb.add_claim(connection, claim)
    except EvidenceGateError as exc:
        _fail(str(exc))
    except Exception as exc:  # pydantic ValidationError 等
        reason = str(exc)
        if "闸门" in reason:
            _fail(reason)
        _fail(f"{type(exc).__name__}: {reason}")
    else:
        _ok(f"已写入证据 {resolved_id}")
        _print_claim(claim)
    finally:
        connection.close()


@evidence_app.command("get")
def evidence_get(
    claim_id: str = typer.Argument(..., help="如 A-E01"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """按 ID 取回证据（T12 验收动作）。"""
    connection = _research_conn()
    try:
        claim = evdb.get_claim(connection, claim_id.upper())
        history = evdb.status_history(connection, claim_id.upper())
    finally:
        connection.close()

    if claim is None:
        _fail(f"证据不存在：{claim_id}", code=1)

    _print_claim(claim, as_json=as_json)
    if history and not as_json:
        console.print("\n[cyan]核验状态历史：[/]")
        for row in history:
            console.print(
                f"  {row['occurred_at_shanghai']}  {row['from_status'] or '∅'} → {row['to_status']}"
                f"  ({row['actor']}) {row['note'] or ''}"
            )


@evidence_app.command("list")
def evidence_list(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t"),
    status: Optional[str] = typer.Option(None, "--status"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """列出证据。"""
    connection = _research_conn()
    try:
        claims = evdb.list_claims(
            connection,
            project_id=normalize_project_id(project) if project else None,
            tag=EpistemicTag(tag) if tag else None,
            status=VerificationStatus(status) if status else None,
        )
    finally:
        connection.close()

    if as_json:
        _echo_json(([c.model_dump(mode="json") for c in claims]))
        return

    table = Table(title=f"证据列表（{len(claims)} 条）")
    table.add_column("ID", style="cyan")
    table.add_column("标签")
    table.add_column("核验")
    table.add_column("结论", overflow="fold", max_width=60)
    table.add_column("来源")
    for claim in claims:
        table.add_row(
            claim.claim_id,
            claim.epistemic_tag.value,
            claim.verification_status.value,
            claim.statement,
            claim.source_name or "—",
        )
    console.print(table)


@evidence_app.command("verify")
def evidence_verify(
    claim_id: str = typer.Argument(...),
    status: str = typer.Option(..., "--status", help="cross_checked / disputed / rejected / unverified"),
    note: Optional[str] = typer.Option(None, "--note", help="迁移理由"),
    actor: str = typer.Option("local", "--actor"),
) -> None:
    """推进核验状态机（非法迁移会被拒绝）。"""
    connection = _research_conn()
    try:
        updated = evdb.transition_status(
            connection,
            claim_id.upper(),
            VerificationStatus(status),
            note=note,
            actor=actor,
        )
    except (InvalidTransition, EvidenceGateError) as exc:
        _fail(str(exc))
    except ValueError as exc:
        _fail(f"状态取值非法：{exc}")
    else:
        _ok(f"{updated.claim_id} → {updated.verification_status.value}")
    finally:
        connection.close()


@evidence_app.command("import-md")
def evidence_import_md(
    path: str = typer.Argument(..., help="证据表 markdown 路径"),
    project: Optional[str] = typer.Option(None, "--project", help="强制指定项目；默认按 ID 前缀推断"),
    strict: bool = typer.Option(False, "--strict", help="有跳过的行即视为失败"),
) -> None:
    """把既有 markdown 证据表导入研究库。"""
    connection = _research_conn()
    try:
        report = import_markdown(connection, path, project_override=project)
    except FileNotFoundError:
        _fail(f"文件不存在：{path}")
    else:
        _ok(
            f"导入 {report.source}：新增 {report.inserted}，更新 {report.updated}，跳过 {len(report.skipped)}"
        )
        if report.skipped:
            table = Table(title="跳过的行（降级处理，未编造字段）")
            table.add_column("行号")
            table.add_column("ID")
            table.add_column("原因", overflow="fold")
            for item in report.skipped:
                table.add_row(str(item.line_no), item.raw_id, item.reason[:160])
            console.print(table)
            if strict:
                raise typer.Exit(code=1)
    finally:
        connection.close()


@evidence_app.command("export-md")
def evidence_export_md(
    out: str = typer.Option(..., "--out", help="输出 markdown 路径"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    title: Optional[str] = typer.Option(None, "--title"),
) -> None:
    """从研究库再生成 markdown 证据表。"""
    connection = _research_conn()
    try:
        claims = evdb.list_claims(
            connection, project_id=normalize_project_id(project) if project else None
        )
    finally:
        connection.close()

    if not claims:
        _fail("没有可导出的证据", code=1)

    markdown = render_markdown(claims, title=title or "证据表（由 research.db 再生成）")
    path = write_text(out, markdown)
    _ok(f"已导出 {len(claims)} 条 → {path}")


# --------------------------------------------------------------------------- 抓取 / 解析


@fetch_app.command("ingest")
def fetch_ingest(
    url: str = typer.Option(..., "--url", help="要抓取的 URL"),
    project: str = typer.Option("ak", "--project", "-p"),
    source_name: Optional[str] = typer.Option(None, "--source-name"),
) -> None:
    """抓取 URL → 落原件 + 抽正文 + 登记 documents。失败只记状态码，不造正文。"""
    connection = _research_conn()
    try:
        project_id = normalize_project_id(project)
        result = ingest_url(connection, url, project_id=project_id, source_name=source_name)
    except ValueError as exc:
        _fail(str(exc))
    else:
        if result.ok:
            _ok(
                f"已入库 doc_id={result.doc_id}｜类型 {result.kind}｜{result.chars} 字"
                f"｜页数 {result.pages}｜表 {result.tables}"
            )
            console.print(f"原件：{result.raw_path}\n正文：{result.text_path}")
        else:
            _echo_json((result.summary()))
            _fail(
                f"抓取/解析失败（{result.error_kind}）：{result.error_detail}。"
                "已记录状态，未生成任何正文或证据。",
                code=1,
            )
    finally:
        connection.close()


@fetch_app.command("from-file")
def fetch_from_file(
    path: str = typer.Argument(..., help="本地文件（PDF/DOCX/XLSX/HTML/TXT）"),
    project: str = typer.Option("ak", "--project", "-p"),
) -> None:
    """解析本地文件入库（T13 验收动作：对 tests/fixtures 跑通解析）。"""
    connection = _research_conn()
    try:
        result = ingest_file(connection, path, project_id=normalize_project_id(project))
    except ValueError as exc:
        _fail(str(exc))
    else:
        if result.ok:
            _ok(
                f"已解析 doc_id={result.doc_id}｜类型 {result.kind}｜{result.chars} 字"
                f"｜页数 {result.pages}｜表 {result.tables}"
            )
            console.print(f"原件：{result.raw_path}\n正文：{result.text_path}")
            if result.document and result.document.text:
                preview = " ".join(result.document.text.split())[:220]
                console.print(Panel(preview, title="正文预览（前 220 字）", border_style="dim"))
        else:
            _fail(f"解析失败（{result.error_kind}）：{result.error_detail}", code=1)
    finally:
        connection.close()


@fetch_app.command("show-text")
def fetch_show_text(doc_id: str = typer.Argument(...), limit: int = typer.Option(2000, "--limit")) -> None:
    """读回已落盘正文（人工核对、摘录用）。"""
    text = read_stored_text(doc_id)
    if text is None:
        _fail(f"找不到正文：{doc_id}", code=1)
    console.print(text[:limit])


@fetch_app.command("search")
def fetch_search(query: str = typer.Argument(...)) -> None:
    """搜索（本期未接任何 API，显式失败，不返回伪造命中）。"""
    from .tools.fetch import UnconfiguredSearchProvider

    try:
        UnconfiguredSearchProvider().search(query)
    except ComputeError as exc:
        _fail(f"{exc}（error_kind={getattr(exc, 'error_kind', 'unknown')}）", code=1)


# --------------------------------------------------------------------------- 工单 / 研究


@ticket_app.command("new")
def ticket_new(
    project: str = typer.Option("ak", "--project", "-p"),
    topic: str = typer.Option(..., "--topic", "-t"),
    scope: Optional[str] = typer.Option(None, "--scope", help="范围与禁令"),
) -> None:
    """新建研究工单。"""
    connection = _research_conn()
    try:
        ticket = create_ticket(connection, project_id=project, topic=topic, scope=scope)
    except ValueError as exc:
        _fail(str(exc))
    else:
        _ok(f"工单 {ticket.ticket_id}｜项目 {ticket.project_id}｜{ticket.topic}")
    finally:
        connection.close()


@ticket_app.command("ask")
def ticket_ask(
    ticket_id: str = typer.Argument(...),
    question: str = typer.Argument(...),
) -> None:
    """给工单添加研究问题。"""
    connection = _research_conn()
    try:
        item = add_question(connection, ticket_id.upper(), question)
    except ValueError as exc:
        _fail(str(exc))
    else:
        _ok(f"{item.question_id} ← {item.question}")
    finally:
        connection.close()


@ticket_app.command("list")
def ticket_list(project: Optional[str] = typer.Option(None, "--project", "-p")) -> None:
    """列出工单。"""
    connection = _research_conn()
    try:
        rows = list_tickets(connection, normalize_project_id(project) if project else None)
    finally:
        connection.close()

    table = Table(title=f"研究工单（{len(rows)}）")
    table.add_column("工单", style="cyan")
    table.add_column("项目")
    table.add_column("主题")
    table.add_column("状态")
    table.add_column("创建")
    for row in rows:
        table.add_row(
            row["ticket_id"], row["project_id"], row["topic"], row["status"], row["created_at_shanghai"]
        )
    console.print(table)


@ticket_app.command("show")
def ticket_show(ticket_id: str = typer.Argument(...)) -> None:
    """查看工单及其问题与已挂证据。"""
    connection = _research_conn()
    try:
        ticket = get_ticket(connection, ticket_id.upper())
    finally:
        connection.close()

    if ticket is None:
        _fail(f"工单不存在：{ticket_id}", code=1)

    console.print(Panel(f"{ticket.topic}\n范围：{ticket.scope or '—'}", title=f"{ticket.ticket_id}"))
    for question in ticket.questions:
        console.print(f"\n[cyan]Q{question.seq}[/] {question.question}  ({question.question_id})")
        if not question.claims:
            console.print("  [yellow]证据：无（数据缺口）[/]")
        for claim in question.claims:
            console.print(
                f"  - {claim.citation} [{claim.epistemic_tag.value}/{claim.verification_status.value}] "
                f"{claim.statement[:70]}"
            )


@research_app.command("link")
def research_link(
    ticket_id: str = typer.Argument(...),
    question_id: str = typer.Argument(...),
    claim_id: str = typer.Argument(...),
) -> None:
    """把证据挂到某问题下。"""
    connection = _research_conn()
    try:
        link_claim(connection, ticket_id.upper(), question_id.upper(), claim_id.upper())
    except ValueError as exc:
        _fail(str(exc))
    else:
        _ok(f"{claim_id.upper()} → {question_id.upper()}")
    finally:
        connection.close()


@research_app.command("draft")
def research_draft(
    ticket_id: str = typer.Argument(...),
    out: Optional[str] = typer.Option(None, "--out", help="输出 md 路径"),
    title: Optional[str] = typer.Option(None, "--title"),
) -> None:
    """生成研究草稿（含 [@claim_id] 引用与缺口标注）。"""
    connection = _research_conn()
    try:
        if out:
            result = write_draft(connection, ticket_id.upper(), out, title=title)
        else:
            from .research.draft import build_draft

            result = build_draft(connection, ticket_id.upper(), title=title)
    except ValueError as exc:
        _fail(str(exc))
        return
    else:
        if out:
            _ok(f"草稿已写入 {out}")
        else:
            console.print(result.markdown)
        console.print(
            f"\n引用证据：{len(result.cited_claim_ids)} 条｜缺口：{len(result.gaps)} 项"
            f"｜可对外：{'是' if result.qa.publishable else '否'}"
        )
        for reason in result.qa.blocking_reasons:
            console.print(f"  [yellow]- {reason}[/]")
    finally:
        connection.close()


@qa_app.command("check")
def qa_check(
    project: str = typer.Option("ak", "--project", "-p"),
    ticket: Optional[str] = typer.Option(None, "--ticket"),
    as_json: bool = typer.Option(False, "--json"),
    persist: bool = typer.Option(False, "--persist", help="写入 qa_reviews 表"),
) -> None:
    """自动质量检查（无摘录占比 / 冲突 / 缺失 / 待核验）。"""
    connection = _research_conn()
    try:
        report = run_qa(
            connection,
            normalize_project_id(project),
            ticket_id=ticket.upper() if ticket else None,
            persist=persist,
        )
    finally:
        connection.close()

    if as_json:
        _echo_json((report.as_dict()))
        return

    table = Table(title=f"QA：{report.project_id}（{report.total_claims} 条证据）")
    table.add_column("指标")
    table.add_column("值")
    table.add_column("阈值")
    table.add_column("结论")
    for metric in report.metrics:
        table.add_row(
            metric.label,
            metric.display,
            "" if metric.threshold is None else f"{metric.threshold:g}",
            "通过" if metric.passed else "[red]不通过[/]",
        )
    console.print(table)
    console.print(f"标签分布：{report.tag_counts}")
    console.print(f"核验分布：{report.status_counts}")
    console.print(f"可对外：{'[green]是[/]' if report.publishable else '[red]否[/]'}")
    for reason in report.blocking_reasons:
        console.print(f"  - {reason}")


# --------------------------------------------------------------------------- 匹配


def _resolve_data_path(value: str) -> str:
    """相对路径既可相对当前目录，也可相对仓库根（便于在任意目录调用 ak）。"""
    path = Path(value)
    if path.is_absolute() or path.exists():
        return str(path)
    candidate = REPO_ROOT / path
    return str(candidate) if candidate.exists() else str(path)


def _load_supply_demand(suppliers_path: str, demands_path: str):
    suppliers = load_suppliers(_resolve_data_path(suppliers_path))
    demands = load_demands(_resolve_data_path(demands_path))
    return suppliers, demands


@match_app.command("run")
def match_run(
    demand_id: str = typer.Option(..., "--demand", "-d", help="需求 ID，如 DEM-20261102-001"),
    suppliers_path: str = typer.Option(DEFAULT_SUPPLIERS, "--suppliers"),
    demands_path: str = typer.Option(DEFAULT_DEMANDS, "--demands"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """对一份需求跑硬过滤，输出匹配与「不匹配原因」。"""
    try:
        suppliers, demands = _load_supply_demand(suppliers_path, demands_path)
    except FileNotFoundError as exc:
        _fail(str(exc), code=1)

    demand = next((d for d in demands if d.demand_id.upper() == demand_id.upper()), None)
    if demand is None:
        _fail(f"需求不存在：{demand_id}（可用：{', '.join(d.demand_id for d in demands)}）", code=1)

    results = match_demand(demand, suppliers)

    if as_json:
        payload = [
            {
                "supplier_id": r.supplier.supplier_id,
                "supplier_name": r.supplier.name,
                "demand_id": r.demand.demand_id,
                "status": r.status,
                "status_label": STATUS_LABELS[r.status],
                "score": r.score,
                "subjective_score": r.subjective_score,
                "matched_reasons": r.matched_reasons,
                "mismatch_reasons": r.mismatch_reasons(),
                "gaps": r.gaps,
                "next_step": r.next_step,
            }
            for r in results
        ]
        _echo_json((payload))
        return

    table = Table(title=f"{demand.demand_id}（{demand.name}）候选供方")
    table.add_column("供方", style="cyan")
    table.add_column("名称")
    table.add_column("结论")
    table.add_column("算法分", justify="right")
    table.add_column("主观分", justify="right")
    table.add_column("不匹配原因 / 缺口", overflow="fold", max_width=70)
    for result in results:
        if result.hard_failures:
            note = "；".join(f"{f.label}" for f in result.hard_failures)
        elif result.gaps:
            note = "缺口：" + "；".join(result.gaps[:3])
        else:
            note = "硬条件全部通过"
        table.add_row(
            result.supplier.supplier_id,
            result.supplier.name,
            STATUS_LABELS[result.status],
            f"{result.score:g}",
            result.display_score,
            note,
        )
    console.print(table)

    for result in results:
        if result.hard_failures:
            console.print(f"\n[red]{result.supplier.supplier_id} 不匹配原因[/]")
            for reason in result.mismatch_reasons():
                console.print(f"  - {reason}")


@match_app.command("report")
def match_report(
    demand_id: str = typer.Option(..., "--demand", "-d"),
    out_dir: str = typer.Option("data/exports", "--out-dir", help="输出目录"),
    suppliers_path: str = typer.Option(DEFAULT_SUPPLIERS, "--suppliers"),
    demands_path: str = typer.Option(DEFAULT_DEMANDS, "--demands"),
    only: Optional[str] = typer.Option(None, "--only", help="只输出某个供方记录"),
) -> None:
    """生成匹配汇总 + 每条候选记录（脱敏，不含联系方式/报价）。"""
    try:
        suppliers, demands = _load_supply_demand(suppliers_path, demands_path)
    except FileNotFoundError as exc:
        _fail(str(exc), code=1)

    demand = next((d for d in demands if d.demand_id.upper() == demand_id.upper()), None)
    if demand is None:
        _fail(f"需求不存在：{demand_id}", code=1)

    results = match_demand(demand, suppliers)
    if only:
        results = [r for r in results if r.supplier.supplier_id.upper() == only.upper()]
        if not results:
            _fail(f"该需求下没有供方 {only}", code=1)

    out_path = Path(out_dir)
    summary_path = write_text(
        out_path / f"match-summary-{demand.demand_id}.md", render_summary(results, demand_label=f"{demand.demand_id}（{demand.name}）")
    )
    written = [summary_path]
    for index, result in enumerate(results, start=1):
        record_path = write_text(
            out_path / f"MATCH-{demand.demand_id}-{index:03d}-{result.supplier.supplier_id}.md",
            render_match_record(result, match_id=f"MATCH-{demand.demand_id}-{index:03d}"),
        )
        written.append(record_path)

    _ok(f"已生成 {len(written)} 个文件到 {out_path}")
    for path in written:
        console.print(f"  - {path}")


@match_app.command("clarify")
def match_clarify(
    demand_id: str = typer.Option(..., "--demand", "-d"),
    supplier_id: str = typer.Option(..., "--supplier", "-s"),
    out: Optional[str] = typer.Option(None, "--out"),
    suppliers_path: str = typer.Option(DEFAULT_SUPPLIERS, "--suppliers"),
    demands_path: str = typer.Option(DEFAULT_DEMANDS, "--demands"),
) -> None:
    """生成商务澄清清单（只列问题，不含答案与联系方式）。"""
    try:
        suppliers, demands = _load_supply_demand(suppliers_path, demands_path)
    except FileNotFoundError as exc:
        _fail(str(exc), code=1)

    demand = next((d for d in demands if d.demand_id.upper() == demand_id.upper()), None)
    supplier = next((s for s in suppliers if s.supplier_id.upper() == supplier_id.upper()), None)
    if demand is None or supplier is None:
        _fail(f"供需方不存在：{demand_id} / {supplier_id}", code=1)

    result = match_one(demand, supplier)
    content = render_clarify(result)
    if out:
        path = write_text(out, content)
        _ok(f"澄清清单 → {path}")
    else:
        console.print(content)


@match_app.command("samples")
def match_samples(
    suppliers_path: str = typer.Option(DEFAULT_SUPPLIERS, "--suppliers"),
    demands_path: str = typer.Option(DEFAULT_DEMANDS, "--demands"),
) -> None:
    """查看当前样例档案的字段完整度（识别「规格缺口」）。"""
    try:
        suppliers, demands = _load_supply_demand(suppliers_path, demands_path)
    except FileNotFoundError as exc:
        _fail(str(exc), code=1)

    table = Table(title=f"供应侧（{len(suppliers)}）")
    table.add_column("ID", style="cyan")
    table.add_column("名称")
    table.add_column("区域")
    table.add_column("卡型")
    table.add_column("交付周")
    table.add_column("卡数")
    table.add_column("未披露", overflow="fold")
    for supplier in suppliers:
        table.add_row(
            supplier.supplier_id,
            supplier.name,
            ",".join(supplier.serves_regions) or "—",
            ",".join(supplier.gpu_models) or "—",
            str(supplier.max_delivery_weeks) if supplier.max_delivery_weeks is not None else "—",
            str(supplier.gpu_quantity) if supplier.gpu_quantity is not None else "—",
            ",".join(supplier.unknown_fields()) or "无",
        )
    console.print(table)

    table2 = Table(title=f"需求侧（{len(demands)}）")
    table2.add_column("ID", style="cyan")
    table2.add_column("名称")
    table2.add_column("目标区域")
    table2.add_column("要求卡型")
    table2.add_column("交付周")
    table2.add_column("卡数")
    table2.add_column("未披露", overflow="fold")
    for demand in demands:
        table2.add_row(
            demand.demand_id,
            demand.name,
            ",".join(demand.target_regions) or "—",
            ",".join(demand.required_gpu_models) or "—",
            str(demand.max_delivery_weeks) if demand.max_delivery_weeks is not None else "—",
            str(demand.required_gpu_quantity) if demand.required_gpu_quantity is not None else "—",
            ",".join(demand.unknown_fields()) or "无",
        )
    console.print(table2)


# --------------------------------------------------------------------------- 私密库


@private_app.command("init")
def private_init(purpose: str = typer.Option(..., "--purpose", help="用途说明（必填）")) -> None:
    """初始化私密库（L2）。"""
    try:
        context = require_l2_access(purpose)
        with PrivateStore() as store:
            store.init(context)
            path = store.db_path
    except Level2AccessDenied as exc:
        _fail(str(exc), code=3)
    else:
        _ok(f"私密库就绪：{path}（已写审计）")


@private_app.command("add-contact")
def private_add_contact(
    purpose: str = typer.Option(..., "--purpose"),
    subject: str = typer.Option(..., "--subject", help="SUP-… / DEM-…"),
    name: str = typer.Option(..., "--name"),
    title: Optional[str] = typer.Option(None, "--title"),
    email: Optional[str] = typer.Option(None, "--email"),
    phone: Optional[str] = typer.Option(None, "--phone"),
    wechat: Optional[str] = typer.Option(None, "--wechat"),
    org: Optional[str] = typer.Option(None, "--org"),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    """写入联系人（L2，仅私密库）。"""
    try:
        context = require_l2_access(purpose)
        with PrivateStore() as store:
            contact_id = store.add_contact(
                context,
                subject_id=subject,
                name=name,
                title=title,
                email=email,
                phone=phone,
                wechat=wechat,
                org=org,
                notes=notes,
            )
    except Level2AccessDenied as exc:
        _fail(str(exc), code=3)
    except ValueError as exc:
        _fail(str(exc))
    else:
        _ok(f"已写入 {contact_id}（仅存于 biz_private.db，研究库不可见）")


@private_app.command("add-quote")
def private_add_quote(
    purpose: str = typer.Option(..., "--purpose"),
    subject: str = typer.Option(..., "--subject"),
    gpu_model: Optional[str] = typer.Option(None, "--gpu-model"),
    unit: Optional[str] = typer.Option(None, "--unit", help="如 卡/小时、卡/月"),
    unit_price: Optional[float] = typer.Option(None, "--unit-price"),
    currency: str = typer.Option("USD", "--currency"),
    quantity: Optional[int] = typer.Option(None, "--quantity"),
    valid_until: Optional[str] = typer.Option(None, "--valid-until"),
) -> None:
    """写入报价（L2）。"""
    try:
        context = require_l2_access(purpose)
        with PrivateStore() as store:
            quote_id = store.add_quote(
                context,
                subject_id=subject,
                gpu_model=gpu_model,
                unit=unit,
                unit_price=unit_price,
                currency=currency,
                quantity=quantity,
                valid_until=valid_until,
            )
    except Level2AccessDenied as exc:
        _fail(str(exc), code=3)
    else:
        _ok(f"已写入 {quote_id}")


@private_app.command("export")
def private_export(
    purpose: str = typer.Option(..., "--purpose"),
    table: str = typer.Option(..., "--table", help="contacts / quotes / deal_logs / matches_private"),
    out: str = typer.Option(..., "--out"),
    fmt: str = typer.Option("csv", "--fmt", help="csv / json"),
    allow_export: bool = typer.Option(
        False, "--allow-export", help="显式授权导出（否则拒绝）"
    ),
) -> None:
    """导出 L2 表：必须显式授权 + 必留审计。"""
    try:
        context = require_l2_access(purpose, allow_export=allow_export)
        with PrivateStore() as store:
            path = store.export_rows(context, table, out_path=out, fmt=fmt)
    except Level2AccessDenied as exc:
        _fail(str(exc), code=3)
    except ValueError as exc:
        _fail(str(exc))
    else:
        _ok(f"已导出 {table} → {path}（审计已记录）")


@private_app.command("audit")
def private_audit(
    purpose: str = typer.Option(..., "--purpose"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """查看 L2 审计日志。"""
    try:
        context = require_l2_access(purpose)
        with PrivateStore() as store:
            rows = store.audit_entries(context, limit=limit)
    except Level2AccessDenied as exc:
        _fail(str(exc), code=3)
        return

    table = Table(title=f"L2 审计日志（最近 {len(rows)} 条）")
    table.add_column("时间", style="cyan")
    table.add_column("操作者")
    table.add_column("动作")
    table.add_column("表")
    table.add_column("行数", justify="right")
    table.add_column("导出授权")
    table.add_column("用途", overflow="fold", max_width=40)
    for row in rows:
        table.add_row(
            row["ts_shanghai"],
            row["actor"],
            row["action"],
            row["table_name"] or "—",
            str(row["row_count"]) if row["row_count"] is not None else "—",
            "是" if row["allow_export"] else "否",
            row["purpose"],
        )
    console.print(table)


@private_app.command("isolation")
def private_isolation() -> None:
    """验证研究库与私密库隔离（T16 验收动作）。"""
    settings = get_settings()
    report = verify_isolation(settings.research_db, settings.private_db)
    _echo_json((report))
    if report["isolated"]:
        _ok("隔离成立：研究库无 L2 表/列，跨库查询被拒")
    else:
        _fail("隔离不成立，检查上面 forbidden_* 字段", code=1)


def main() -> None:
    """控制台入口。"""
    _ensure_utf8_stdio()
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
