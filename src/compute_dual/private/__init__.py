"""L2 商务敏感数据：独立库 + 访问闸门 + 审计日志。

隔离要求（设计 §7 §14）：客户联系人 / 报价 / 合同 / 成交意向**只**存在
``data/private/biz_private.db``，研究库里没有任何相关表或字段。
"""

from .guard import Level2Context, require_l2_access
from .store import PrivateStore, verify_isolation

__all__ = ["Level2Context", "PrivateStore", "require_l2_access", "verify_isolation"]
