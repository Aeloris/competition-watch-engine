# -*- coding: utf-8 -*-
"""简单面板（Phase 8，README2 §7.4 row 8）：人工放行 + 告警可视化的**极简 HTML 页**。

零构建（不用前端框架/打包器），服务端直出 HTML + 原生 JS 调同款 JSON API：
- GET /panel：总览——待办收件箱（未销案条目 + 跳转）+ 最近任务 + 最近告警台账；
- GET /panel/task/{task_id}：单任务页——每 run 的收件箱条目带 放行/驳回/备注 按钮
  （fetch POST /inbox/resolve，body 走 JSON）+ 发布视图（published/held/dismissed 计数与明细）。

数据全走 service 层（list_inbox_entries / published_view / TaskStore / alert 台账），
路由只负责把 JSON 渲染成 HTML；测试断言 200 + 关键标记 + 文本，不测浏览器行为。
诚实口径：面板是给"人工确认/放行"用的操作台，别指望它好看——能点按钮、能看到决策即达标。
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app.routers.deps import get_service_ctx
from service.context import ServiceContext
from service.publish import EntryNotFound, list_inbox_entries, published_view

router = APIRouter(tags=["panel"])

_CODE_COLOR = {
    "UNSOURCED_URL": "#b45309", "UNGROUNDED_FP": "#b91c1c", "UNGROUNDED_CLAIM": "#a21caf",
    "CONTRADICTION": "#be185d", "DUPLICATE": "#1d4ed8",
    "NO_EVIDENCE": "#57534e", "EMPTY_TITLE": "#57534e", "MISSING_DIMENSION": "#57534e",
    "MISSING_SEVERITY": "#57534e", "EMPTY_REASONS": "#57534e", "MISSING_FP": "#57534e",
}

_ACTION_LABEL = {"release": "放行 ✅", "dismiss": "驳回 🚫", "note": "备注 📝"}

_HEAD = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title><style>
body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;background:#0f172a;color:#e2e8f0}}
.wrap{{max-width:1080px;margin:0 auto;padding:20px}}
a{{color:#7dd3fc;text-decoration:none}} a:hover{{text-decoration:underline}}
h1{{font-size:20px}} h2{{font-size:16px;border-bottom:1px solid #334155;padding-bottom:6px;margin-top:28px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #334155;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#1e293b;position:sticky;top:0}} .tag{{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px}}
.ok{{color:#4ade80}} .warn{{color:#fbbf24}} .bad{{color:#f87171}}
.entry{{border:1px solid #334155;border-radius:8px;padding:10px;margin:8px 0;background:#0b1220}}
.code{{font-family:ui-monospace,monospace;color:#fbbf24}} .dim{{color:#94a3b8;font-size:12px}}
button{{cursor:pointer;border:1px solid #475569;border-radius:6px;padding:3px 10px;margin-right:6px;background:#1e293b;color:#e2e8f0}}
button:hover{{background:#334155}} input[type=text]{{background:#0f172a;border:1px solid #475569;color:#e2e8f0;border-radius:6px;padding:4px 8px}}
</style></head><body><div class="wrap">"""

_FOOT = """</div><script>
async function resolve(entryId, action){{
  const by = (document.getElementById('by')||{{value:'面板操作员'}}).value || '面板操作员';
  const note = action==='note' ? (prompt('备注内容：')||'') : '';
  const body = {{entry_id:entryId, action:action, by:by, note:note}};
  const res = await fetch('/inbox/resolve', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body)}});
  if(res.ok){{ location.reload(); }} else {{
    const j = await res.json().catch(()=>({{detail:res.status}}));
    alert('定案失败('+res.status+')：' + (j.detail || ''));
  }}
}}
</script></body></html>"""


def _resolution_cell(resolution) -> str:
    if not resolution:
        return '<span class="dim">未处理</span>'
    label = _ACTION_LABEL.get(resolution.get("action"), resolution.get("action"))
    return (f'<span class="code">{label}</span> <span class="dim">by {escape(resolution.get("by", ""))} · '
            f'{escape(str(resolution.get("note", "")))} · {escape(resolution.get("resolved_at", ""))}</span>')


def _entry_html(e: dict) -> str:
    color = _CODE_COLOR.get(e["code"], "#64748b")
    html_id = f'id="entry-{escape(e["entry_id"])}"'
    resolved = e["status"] == "resolved"
    controls = (f'<button onclick="resolve(\'{e["entry_id"]}\',\'release\')">放行</button>'
                f'<button onclick="resolve(\'{e["entry_id"]}\',\'dismiss\')">驳回</button>'
                f'<button onclick="resolve(\'{e["entry_id"]}\',\'note\')">备注</button>'
                ) if not resolved else ""
    status_tag = ('<span class="tag ok">resolved</span>' if resolved
                  else '<span class="tag warn">pending</span>')
    return (f'<div class="entry" {html_id}>'
            f'<span class="code" style="color:{color}">{escape(e["code"])}</span> {status_tag} '
            f'<span class="dim">item_index={e.get("item_index")} · fp={escape(str(e.get("fp")))} · '
            f'attempts={e.get("attempts")}</span>'
            f'<div style="margin:4px 0">{escape(e["detail"])}</div>'
            f'<div class="dim">entry_id={escape(e["entry_id"])}</div>'
            f'<div style="margin-top:6px">{controls}{_resolution_cell(e.get("resolution"))}</div>'
            f'</div>')


@router.get("/panel", response_class=HTMLResponse)
def panel_overview(ctx: ServiceContext = Depends(get_service_ctx)) -> str:
    pending = list_inbox_entries(ctx.task_store, status="pending")
    resolved = list_inbox_entries(ctx.task_store, status="resolved")
    tasks = ctx.task_store.list_tasks()[:10]
    alerts = list(ctx.list_alerts())[:8]  # 台账已倒序存（最新在前，见 alerts 路由）

    queue_html = "".join(_entry_html(e) for e in pending)
    if not pending:
        queue_html = '<div class="ok">✅ 待办已清空（无未处理收件箱条目）</div>'

    task_rows = "".join(
        f'<tr><td><a href="/panel/task/{m.task_id}">{m.task_id}</a></td>'
        f'<td>{m.period}</td><td>{m.status.value}</td>'
        f'<td>{m.summary.get("done", 0)}/{m.summary.get("runs", 0)} done</td>'
        f'<td>{m.summary.get("human", 0)}</td><td>{m.summary.get("inbox_total", 0)}</td></tr>'
        for m in tasks
    ) or '<tr><td colspan="6" class="dim">暂无任务（先 POST /tasks 跑一次巡检）</td></tr>'

    alert_rows = "".join(
        f'<tr><td><span class="tag">{escape(a["event_type"])}</span></td>'
        f'<td>{escape(a.get("competitor_id") or "-")}</td>'
        f'<td>{escape(a.get("period") or "-")}</td>'
        f'<td>{escape(a["summary"])}</td><td class="dim">{escape(a["ts"])}</td></tr>'
        for a in alerts
    ) or '<tr><td colspan="5" class="dim">告警台账为空（MockNotifier 落 data/service/alerts.json）</td></tr>'

    body = f"""
<h1>竞观 · 人工放行面板 <span class="dim">(Phase 8 简单面板)</span></h1>
<p class="dim">先审后发闭环：门卫转人工 → 这里放行/驳回/备注 → 发布视图随之过滤。逻辑全在 service/publish。</p>

<h2>人工收件箱待办 <span class="bad">({len(pending)} 未销案)</span> · 已处理 {len(resolved)}</h2>
{queue_html}

<h2>最近任务</h2>
<table><tr><th>task</th><th>period</th><th>状态</th><th>run(done/total)</th><th>human</th><th>inbox</th></tr>{task_rows}</table>

<h2>最近告警</h2>
<table><tr><th>类型</th><th>竞品</th><th>期</th><th>摘要</th><th>ts</th></tr>{alert_rows}</table>
"""
    return _HEAD.format(title="竞观 · 人工放行面板") + body + _FOOT


@router.get("/panel/task/{task_id}", response_class=HTMLResponse)
def panel_task(task_id: str, ctx: ServiceContext = Depends(get_service_ctx)) -> str:
    meta = ctx.task_store.get_task(task_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} 不存在")

    # 收件箱：该 task 全部 run 的条目（含已处理），按 run 分组展示
    run_blocks = ""
    for run in meta.runs:
        if run.verdict != "human" and not run.human_inbox:
            continue
        entries = "".join(_entry_html(e) for e in list_inbox_entries(ctx.task_store, status="all")
                          if e["run_id"] == run.run_id) or '<div class="dim">该 run 无收件箱条目</div>'
        run_blocks += (f'<h3>{run.competitor_id} {run.period} · run={run.run_id} · '
                       f'verdict=<span class="{"bad" if run.verdict=="human" else "ok"}">{run.verdict}</span> · '
                       f'trace={run.trace.get("collected")}→{run.trace.get("merged")}→{run.trace.get("diff")} · '
                       f'radar={run.threat_radar}</h3>{entries}')

    # 发布视图
    try:
        pv = published_view(ctx.task_store, ctx.settings, task_id)
    except EntryNotFound:
        raise HTTPException(status_code=404, detail=f"task {task_id} 不存在")
    pub_rows = ""
    for rv in pv["runs"]:
        state = rv["note"] or ""
        held_notes = "".join(
            f'<li><span class="code">{escape(h["item"].get("title", ""))}</span> — '
            + "；".join(escape(f'#{p.get("code")} {p.get("detail", "")[:90]}') for p in h["problems"])
            + '</li>' for h in rv["held"]) or '无'
        dismissed_notes = "".join(
            f'<li><span class="code">{escape(d["item"].get("title", ""))}</span> — '
            + "；".join(escape(f'#{p.get("code")} {p.get("detail", "")[:90]}') for p in d["problems"])
            + '</li>' for d in rv["dismissed"]) or '无'
        pub_rows += (
            f'<tr><td>{rv["competitor_id"]}</td><td>{rv["verdict"]}</td>'
            f'<td class="ok">{rv["published_count"]}</td><td class="warn">{rv["held_count"]}</td>'
            f'<td class="bad">{rv["dismissed_count"]}</td>'
            f'<td class="dim">{escape(state)}</td></tr>')
    summary = f"""
<h2>发布视图 <span class="dim">published {pv["published_total"]} · held {pv["held_total"]} · dismissed {pv["dismissed_total"]}</span></h2>
<table><tr><th>竞品</th><th>verdict</th><th>已发布</th><th>挂起(held)</th><th>剔除(dismissed)</th><th>备注</th></tr>{pub_rows}</table>
<h3>挂起条目（等人工定案）</h3><ul>{held_notes}</ul>
<h3>人工剔除条目（审计留痕）</h3><ul>{dismissed_notes}</ul>
"""

    summary_meta = meta.summary or {}
    body = f"""
<h1>Task <span class="code">{task_id}</span>
<a style="float:right" href="/panel">← 返回总览</a></h1>
<p>period <span class="code">{meta.period}</span> · status <span class="tag">{meta.status.value}</span> ·
runs={summary_meta.get("runs")} done={summary_meta.get("done")} human={summary_meta.get("human")} ·
inbox_total={summary_meta.get("inbox_total")}</p>
<div class="dim">处理人：<input type="text" id="by" value="面板操作员" size="14"></div>
{run_blocks or '<div class="dim">该 task 无转人工的 run（全 PASS 或全部失败）</div>'}
{summary}
"""
    return _HEAD.format(title=f"Task {task_id}") + body + _FOOT
