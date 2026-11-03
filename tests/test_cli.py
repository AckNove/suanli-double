"""CLI 冒烟测试（对应交接文档 §4 验收口令）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from compute_dual.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_db(clean_workspace_dbs):
    """每个 CLI 测试都从空库开始（CLI 走全局 settings）。"""
    return clean_workspace_dbs


def invoke(*args: str):
    return runner.invoke(app, list(args))


def test_version() -> None:
    result = invoke("version")
    assert result.exit_code == 0
    assert "compute-dual" in result.stdout


def test_db_init() -> None:
    result = invoke("db", "init")
    assert result.exit_code == 0
    assert "研究库就绪" in result.stdout


def test_evidence_add_then_get_json() -> None:
    invoke("db", "init")
    added = invoke(
        "evidence",
        "add",
        "--project",
        "ak",
        "--tag",
        "fact",
        "--statement",
        "NVIDIA GPU 市占估计",
        "--url",
        "https://example.com/oecd",
        "--source-name",
        "OECD",
        "--excerpt",
        "over 80% market share",
    )
    assert added.exit_code == 0, added.stdout
    assert "A-E01" in added.stdout

    fetched = invoke("evidence", "get", "A-E01", "--json")
    assert fetched.exit_code == 0
    payload = json.loads(fetched.stdout)
    assert payload["claim_id"] == "A-E01"
    assert payload["epistemic_tag"] == "fact"
    assert payload["retrieved_at_shanghai"].endswith("+08:00")


def test_evidence_add_fact_without_url_fails() -> None:
    invoke("db", "init")
    result = invoke(
        "evidence",
        "add",
        "--project",
        "ak",
        "--tag",
        "fact",
        "--statement",
        "没有来源的结论",
    )
    assert result.exit_code == 2
    assert "闸门" in result.stdout


def test_evidence_verify_and_illegal_transition() -> None:
    invoke("db", "init")
    invoke(
        "evidence",
        "add",
        "--project",
        "ak",
        "--tag",
        "fact",
        "--statement",
        "结论",
        "--url",
        "https://example.com/a",
        "--excerpt",
        "摘录",
    )

    ok = invoke("evidence", "verify", "A-E01", "--status", "rejected", "--note", "源不可靠")
    assert ok.exit_code == 0

    illegal = invoke("evidence", "verify", "A-E01", "--status", "cross_checked")
    assert illegal.exit_code == 2
    assert "非法" in illegal.stdout


def test_evidence_list() -> None:
    invoke("db", "init")
    invoke(
        "evidence",
        "add",
        "--project",
        "ak",
        "--tag",
        "fact",
        "--statement",
        "结论",
        "--url",
        "https://example.com/a",
        "--excerpt",
        "摘录",
    )
    result = invoke("evidence", "list", "--project", "ak", "--json")
    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["claim_id"] == "A-E01"


def test_evidence_import_real_table() -> None:
    invoke("db", "init")
    path = Path("docs/handoff/A-算力之王AK/证据表.md")
    result = invoke("evidence", "import-md", str(path), "--strict")
    assert result.exit_code == 0, result.stdout
    assert "新增" in result.stdout


def test_evidence_export_md(tmp_path: Path) -> None:
    invoke("db", "init")
    path = Path("docs/handoff/A-算力之王AK/证据表.md")
    invoke("evidence", "import-md", str(path))

    out = tmp_path / "exported.md"
    result = invoke("evidence", "export-md", "--out", str(out), "--project", "ak")
    assert result.exit_code == 0, result.stdout
    assert out.exists()
    assert "| ID | 结论内容 |" in out.read_text(encoding="utf-8")


def test_fetch_from_file_pdf() -> None:
    invoke("db", "init")
    result = invoke("fetch", "from-file", "tests/fixtures/sample.pdf", "--project", "ak")
    assert result.exit_code == 0, result.stdout
    assert "已解析" in result.stdout


def test_fetch_from_file_missing() -> None:
    invoke("db", "init")
    result = invoke("fetch", "from-file", "tests/fixtures/nope.pdf")
    assert result.exit_code == 1
    assert "解析失败" in result.stdout


def test_fetch_search_is_explicitly_unconfigured() -> None:
    result = invoke("fetch", "search", "AI 服务器")
    assert result.exit_code == 1
    assert "未配置搜索 Provider" in result.stdout


def test_ticket_and_draft_flow(tmp_path: Path) -> None:
    invoke("db", "init")
    created = invoke("ticket", "new", "--project", "ak", "--topic", "合成主题")
    assert created.exit_code == 0
    ticket_id = created.stdout.split("工单")[1].split("｜")[0].strip()

    asked = invoke("ticket", "ask", ticket_id, "合成问题")
    assert asked.exit_code == 0

    invoke(
        "evidence",
        "add",
        "--project",
        "ak",
        "--tag",
        "fact",
        "--statement",
        "结论",
        "--url",
        "https://example.com/a",
        "--excerpt",
        "摘录",
    )
    linked = invoke("research", "link", ticket_id, f"{ticket_id}-Q1", "A-E01")
    assert linked.exit_code == 0, linked.stdout

    out = tmp_path / "draft.md"
    drafted = invoke("research", "draft", ticket_id, "--out", str(out))
    assert drafted.exit_code == 0, drafted.stdout
    content = out.read_text(encoding="utf-8")
    assert "[@A-E01]" in content
    assert "结论（待人工撰写）" in content


def test_qa_check() -> None:
    invoke("db", "init")
    invoke(
        "evidence",
        "add",
        "--project",
        "ak",
        "--tag",
        "fact",
        "--statement",
        "结论",
        "--url",
        "https://example.com/a",
        "--excerpt",
        "摘录",
    )
    result = invoke("qa", "check", "--project", "ak", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total_claims"] == 1
    assert payload["publishable"] is True


def test_match_run_synthetic() -> None:
    result = invoke(
        "match",
        "run",
        "--demand",
        "SYN-DEM-01",
        "--suppliers",
        "tests/fixtures/suppliers_fixture.csv",
        "--demands",
        "tests/fixtures/demands_fixture.csv",
        "--json",
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload
    statuses = {item["supplier_id"]: item["status"] for item in payload}
    assert statuses["SYN-SUP-01"] == "match"
    assert statuses["SYN-SUP-02"] == "no_match"
    assert statuses["SYN-SUP-07"] == "undetermined"

    beta = next(item for item in payload if item["supplier_id"] == "SYN-SUP-02")
    assert any("REGION_MISMATCH" in reason for reason in beta["mismatch_reasons"])


def test_match_run_unknown_demand() -> None:
    result = invoke("match", "run", "--demand", "DEM-00000000-000")
    assert result.exit_code == 1
    assert "需求不存在" in result.stdout


def test_match_run_from_other_cwd_uses_repo_samples(tmp_path, monkeypatch) -> None:
    """在其他目录调用 ak 时，默认样例路径应回退到仓库根，而不是报文件不存在。"""
    monkeypatch.chdir(tmp_path)
    result = invoke("match", "run", "--demand", "DEM-20261102-001", "--json")
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload and all(item["status"] != "match" for item in payload)


def test_json_output_is_utf8_when_redirected() -> None:
    """重定向/管道输出必须是 UTF-8 且能直接解析（Windows 默认 GBK 会崩）。"""
    import os
    import subprocess
    import sys

    invoke("db", "init")
    invoke(
        "evidence",
        "add",
        "--project",
        "ak",
        "--tag",
        "fact",
        "--statement",
        "中文结论：液冷渗透率",
        "--url",
        "https://example.com/a",
        "--excerpt",
        "页面已开：中文摘录",
    )

    executable = Path(sys.executable).parent / ("ak.exe" if os.name == "nt" else "ak")
    if not executable.exists():  # pragma: no cover - 非常规安装方式
        pytest.skip("找不到 ak 可执行文件")

    completed = subprocess.run(
        [str(executable), "evidence", "get", "A-E01", "--json"],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": ""},
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")

    payload = json.loads(completed.stdout.decode("utf-8"))
    assert payload["claim_id"] == "A-E01"
    assert payload["statement"] == "中文结论：液冷渗透率"


def test_match_report_writes_files(tmp_path: Path) -> None:
    result = invoke(
        "match",
        "report",
        "--demand",
        "SYN-DEM-01",
        "--suppliers",
        "tests/fixtures/suppliers_fixture.csv",
        "--demands",
        "tests/fixtures/demands_fixture.csv",
        "--out-dir",
        str(tmp_path),
    )
    assert result.exit_code == 0, result.stdout
    summary = tmp_path / "match-summary-SYN-DEM-01.md"
    assert summary.exists()
    assert "不匹配原因" in summary.read_text(encoding="utf-8")
    assert list(tmp_path.glob("MATCH-SYN-DEM-01-*.md"))


def test_private_requires_purpose() -> None:
    result = invoke("private", "export", "--table", "contacts", "--out", "x.csv")
    assert result.exit_code != 0


def test_private_export_requires_allow_export(tmp_path: Path) -> None:
    init = invoke("private", "init", "--purpose", "初始化私密库")
    assert init.exit_code == 0, init.stdout

    added = invoke(
        "private",
        "add-contact",
        "--purpose",
        "录入合成联系人",
        "--subject",
        "SUP-20261102-001",
        "--name",
        "合成联系人",
        "--email",
        "syn@example.com",
    )
    assert added.exit_code == 0, added.stdout

    denied = invoke(
        "private",
        "export",
        "--purpose",
        "尝试导出",
        "--table",
        "contacts",
        "--out",
        str(tmp_path / "contacts.csv"),
    )
    assert denied.exit_code == 3
    assert "导出 L2 数据需要显式授权" in denied.stdout

    allowed = invoke(
        "private",
        "export",
        "--purpose",
        "向Arthur汇报",
        "--table",
        "contacts",
        "--out",
        str(tmp_path / "contacts.csv"),
        "--allow-export",
    )
    assert allowed.exit_code == 0, allowed.stdout
    assert (tmp_path / "contacts.csv").exists()

    audit = invoke("private", "audit", "--purpose", "检查审计日志")
    assert audit.exit_code == 0
    assert "export" in audit.stdout


def test_private_isolation_command() -> None:
    invoke("db", "init")
    invoke("private", "init", "--purpose", "隔离验证")
    result = invoke("private", "isolation")
    assert result.exit_code == 0, result.stdout
    assert '"isolated": true' in result.stdout


def test_doctor_runs() -> None:
    result = invoke("doctor")
    assert result.exit_code == 0
    assert "环境自检" in result.stdout
