"""L2 访问闸门。

规则（简化自《基础设施设计 §14》与《权限与目录摘要》）：

- L2 = 联系人 / 报价 / 合同 / 成交意向，仅 Arthur 与**明示授权**的 agent 可读。
- 每次 L2 读写必须带 ``purpose``（用途说明），否则拒绝；这是「导出必留审计」的入口。
- ``allow_export`` 必须显式为真才能导出（默认 False），防止误把敏感库内容推到共享区。

平台层没有 ACL，因此闸门写在这里，**在代码里做实**。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..common.errors import Level2AccessDenied

MIN_PURPOSE_LEN = 4


@dataclass(frozen=True)
class Level2Context:
    """一次 L2 操作的身份与用途。"""

    actor: str
    purpose: str
    allow_export: bool = False

    def describe(self) -> str:
        suffix = "（含导出授权）" if self.allow_export else ""
        return f"{self.actor}／{self.purpose}{suffix}"


def require_l2_access(
    purpose: str | None,
    *,
    actor: str | None = None,
    allow_export: bool = False,
) -> Level2Context:
    """校验 L2 访问许可，不通过直接抛 ``Level2AccessDenied``。

    ``actor`` 默认取环境变量 ``COMPUTE_OPERATOR``，再退化为 ``local-operator``。
    设置 ``COMPUTE_L2_LOCKED=1`` 可让本机整体禁用 L2 访问（例如借用机器演示时）。
    """
    if os.environ.get("COMPUTE_L2_LOCKED") == "1":
        raise Level2AccessDenied(
            "COMPUTE_L2_LOCKED=1：本机已禁用 L2 商务敏感数据访问"
        )

    text = (purpose or "").strip()
    if len(text) < MIN_PURPOSE_LEN:
        raise Level2AccessDenied(
            "L2 访问必须声明用途：--purpose \"...\"（至少 4 个字符），否则拒绝，且不会写审计日志"
        )

    resolved_actor = (actor or os.environ.get("COMPUTE_OPERATOR") or "local-operator").strip()
    return Level2Context(actor=resolved_actor, purpose=text, allow_export=allow_export)
