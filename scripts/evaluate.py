from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from app.workflow import Workflow


def main():
    root = Path(__file__).resolve().parents[1]
    cases = []
    scenarios = [
        ("渠道分析并审批", "按渠道分析退款率", "", "approve", "completed"),
        ("商品分析并审批", "按商品分析退款率", "", "approve", "completed"),
        ("渠道过滤", "分析广告投放退款率，按商品拆解", "", "approve", "completed"),
        ("商品过滤", "分析专业版退款率", "", "approve", "completed"),
        ("人工拒绝", "分析退款率", "", "reject", "rejected"),
        ("关键查询失败", "分析退款率", "query", "none", "failed"),
        ("查询故障恢复", "分析退款率", "query", "retry", "awaiting_approval"),
        ("瞬时查询重试", "分析退款率", "query_once", "approve", "completed"),
        ("规则检索降级", "分析退款率", "rules", "approve", "completed"),
        ("通知故障隔离", "分析退款率", "notify", "approve", "notification_failed"),
        ("通知单独重试", "分析退款率", "notify", "notify_retry", "completed"),
        ("不支持问题澄清", "查询公司利润", "", "none", "input_required"),
    ]
    with tempfile.TemporaryDirectory() as directory:
        workflow = Workflow(directory)
        try:
            for name, question, fault, action, expected in scenarios:
                start = time.perf_counter()
                try:
                    task = workflow.create(question, fault=fault, planner="rules")
                    if action in {"approve", "reject", "notify_retry"}:
                        task = workflow.approve(task["task_id"], action != "reject")
                    if action in {"retry", "notify_retry"}:
                        task = workflow.retry(task["task_id"])
                    passed = task["status"] == expected and task[
                        "notification_count"
                    ] == int(expected == "completed")
                    if task.get("analysis"):
                        totals = task["analysis"]["totals"]
                        passed = passed and all(
                            abs(total["rate"] - total["refunds"] / total["orders"])
                            < 1e-9
                            for total in totals.values()
                        )
                    cases.append(
                        {
                            "name": name,
                            "passed": passed,
                            "status": task["status"],
                            "latency_ms": round(
                                (time.perf_counter() - start) * 1000, 2
                            ),
                        }
                    )
                except Exception as error:
                    cases.append(
                        {
                            "name": name,
                            "passed": False,
                            "error": type(error).__name__,
                            "latency_ms": round(
                                (time.perf_counter() - start) * 1000, 2
                            ),
                        }
                    )
            with closing(sqlite3.connect(workflow.orders_path)) as db:
                db.row_factory = sqlite3.Row
                rows = [
                    dict(row) for row in db.execute("SELECT * FROM orders ORDER BY id")
                ]
            (root / "web/demo-data.json").write_text(
                json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        finally:
            workflow.close()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "规则规划器 + 真实 LangGraph/SQLite 的 12 个固定场景回归，包含故障注入；不是开放式 LLM 能力基准。",
        "total": len(cases),
        "passed": sum(c["passed"] for c in cases),
        "cases": cases,
    }
    for path in [root / "docs/evaluation.json", root / "web/evaluation.json"]:
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
