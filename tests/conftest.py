"""pytest 全局夹具。

所有测试都在临时数据目录里跑（``COMPUTE_DATA_DIR``），不污染仓库 ``data/``。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from compute_dual.config import REPO_ROOT, get_settings
from compute_dual.evidence import db as evdb

FIXTURES = REPO_ROOT / "tests" / "fixtures"
HANDOFF = REPO_ROOT / "docs" / "handoff"


@pytest.fixture(scope="session", autouse=True)
def isolated_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """整个测试会话使用独立数据目录。"""
    data_dir = tmp_path_factory.mktemp("compute-data")
    previous = os.environ.get("COMPUTE_DATA_DIR")
    os.environ["COMPUTE_DATA_DIR"] = str(data_dir)
    yield data_dir
    if previous is None:
        os.environ.pop("COMPUTE_DATA_DIR", None)
    else:
        os.environ["COMPUTE_DATA_DIR"] = previous


@pytest.fixture()
def settings():
    current = get_settings()
    current.ensure_dirs()
    return current


@pytest.fixture()
def research_db(tmp_path: Path) -> Path:
    """每个测试独立的空研究库文件路径。"""
    return tmp_path / "research.db"


@pytest.fixture()
def conn(research_db: Path) -> sqlite3.Connection:
    connection = evdb.connect(research_db)
    evdb.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture()
def clean_workspace_dbs(settings) -> Path:
    """清掉会话数据目录里的研究库/私密库（CLI 测试用，因为 CLI 走全局 settings）。"""
    for path in (
        settings.research_db,
        Path(str(settings.research_db) + "-wal"),
        Path(str(settings.research_db) + "-shm"),
        settings.private_db,
    ):
        if path.exists():
            path.unlink()
    settings.ensure_dirs()
    return settings.data_dir


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture()
def handoff_dir() -> Path:
    return HANDOFF


@pytest.fixture()
def sample_claim(conn):
    """一条可复用的事实类证据（写入该测试专属的研究库）。"""
    from compute_dual.common.timeutil import now_shanghai_str
    from compute_dual.evidence.models import EvidenceClaim

    claim = EvidenceClaim(
        claim_id="A-E01",
        project_id="ak",
        statement="测试用事实结论",
        epistemic_tag="fact",
        source_name="测试来源",
        url="https://example.com/report",
        retrieved_at_shanghai=now_shanghai_str(),
        excerpt="原文摘录",
        source_type="测试",
        trust_level="高",
    )
    evdb.add_claim(conn, claim)
    return claim
