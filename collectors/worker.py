# -*- coding: utf-8 -*-
"""CollectorWorker 占位类（Phase 2 实现）。

职责：包一个 SourceAdapter（sources/ 里），按采集任务并发抓取并归一化成统一
FactCard（URL + 原文时间）；单源失败记原因、继续跑完，报告标注该源失败（README2 §5.3）。
"""


class CollectorWorker:
    """单个信源的采集 Worker（Phase 2 实现：asyncio + 信号量 + 失败隔离）。"""

    # 输入：采集任务（竞品 + 信源配置 + since）。输出：list[FactCard]（统一带 url/published_at）。
    pass
