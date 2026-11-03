"""路径与运行设置。

数据目录默认 ``<repo>/data``，可用环境变量 ``COMPUTE_DATA_DIR`` 覆盖（便于测试隔离）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# src/compute_dual/config.py -> parents[2] == 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """一次性解析出的全部路径，避免各处重复拼装。"""

    repo_root: Path
    data_dir: Path
    raw_dir: Path
    text_dir: Path
    private_dir: Path
    logs_dir: Path
    exports_dir: Path
    research_db: Path
    private_db: Path

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.raw_dir,
            self.text_dir,
            self.private_dir,
            self.logs_dir,
            self.exports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def get_settings(data_dir: str | os.PathLike[str] | None = None) -> Settings:
    """构造 Settings。

    ``data_dir`` 优先级：显式参数 > 环境变量 COMPUTE_DATA_DIR > ``<repo>/data``。
    """
    if data_dir is None:
        data_dir = os.environ.get("COMPUTE_DATA_DIR") or None

    root = Path(data_dir).expanduser().resolve() if data_dir else REPO_ROOT / "data"

    return Settings(
        repo_root=REPO_ROOT,
        data_dir=root,
        raw_dir=root / "raw",
        text_dir=root / "text",
        private_dir=root / "private",
        logs_dir=root / "logs",
        exports_dir=root / "exports",
        research_db=root / "research.db",
        private_db=root / "private" / "biz_private.db",
    )
