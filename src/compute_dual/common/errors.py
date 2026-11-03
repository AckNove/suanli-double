"""失败分类与异常。

设计文档 §15：失败分类 network / parse / policy_block / budget；降级跳过该源、标记缺口。
"""

from __future__ import annotations


class ComputeError(Exception):
    """所有本包异常的基类。"""


class EvidenceGateError(ComputeError):
    """证据硬闸门拒绝写入（例如 fact 缺 URL）。"""


class InvalidTransition(ComputeError):
    """核验状态机非法迁移。"""


class Level2AccessDenied(ComputeError):
    """L2 商务敏感数据访问被拒（缺少授权或用途声明）。"""


class ParseError(ComputeError):
    """文档解析失败。"""

    def __init__(self, message: str, *, error_kind: str = "parse") -> None:
        super().__init__(message)
        self.error_kind = error_kind


class FetchError(ComputeError):
    """抓取失败。``error_kind`` 取值 network / policy_block / budget / parse。"""

    def __init__(self, message: str, *, error_kind: str = "network", status_code: int | None = None) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.status_code = status_code
