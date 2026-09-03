"""采集层（Phase 2 Researchers）。

adapter 模式屏蔽来源差异：每个信源一个 SourceAdapter，统一产出 FactCard；
asyncio + 信号量并发、超时 + 重试退避、单源失败隔离（不影响全局）。
"""
from collectors.worker import CollectorWorker

__all__ = ["CollectorWorker"]
