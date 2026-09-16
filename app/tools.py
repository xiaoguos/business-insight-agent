import json
import sqlite3
from contextlib import closing
import time
from pathlib import Path
import sqlglot
from sqlglot import exp


class UnsafeQuery(ValueError):
    pass


def query_readonly(path, sql, params=(), timeout=0.5):
    """Allow one SELECT over orders, then enforce read-only at SQLite level.

    AST validation is defense in depth; a read-only URI and progress handler are
    authoritative. The graph uses fixed templates, not arbitrary generated SQL.
    """
    try:
        expressions = sqlglot.parse(sql, read="sqlite")
    except sqlglot.errors.ParseError as error:
        raise UnsafeQuery("SQL 解析失败") from error
    if len(expressions) != 1 or not isinstance(expressions[0], exp.Select):
        raise UnsafeQuery("只允许单条 SELECT")
    tree = expressions[0]
    forbidden = (
        exp.Insert,
        exp.Delete,
        exp.Update,
        exp.Create,
        exp.Drop,
        exp.Command,
        exp.Union,
    )
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise UnsafeQuery("查询包含禁止操作")
    tables = list(tree.find_all(exp.Table))
    if not tables or any(
        table.name != "orders" or table.db or table.catalog for table in tables
    ):
        raise UnsafeQuery("只允许访问 orders 表")
    if any(
        node.name.lower() in {"load_extension", "readfile", "writefile"}
        for node in tree.find_all(exp.Anonymous)
    ):
        raise UnsafeQuery("查询包含禁止函数")
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + timeout
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        return [dict(row) for row in db.execute(sql, params).fetchmany(201)][:200]


def summarize(path, dimension="channel", channel=None, product=None):
    if dimension not in {"channel", "product"}:
        raise ValueError("不支持的拆解维度")
    clause, params = "", []
    for field, value in [("channel", channel), ("product", product)]:
        if value:
            clause += f" AND {field} = ?"
            params.append(value)
    sql = f"""SELECT CASE WHEN created_at < '2026-09-08' THEN 'baseline' ELSE 'current' END AS period,
              {dimension} AS segment, COUNT(*) AS orders, SUM(refunded) AS refunds,
              SUM(amount) AS revenue
              FROM orders WHERE created_at >= '2026-09-01' AND created_at <= '2026-09-14' {clause}
              GROUP BY period, {dimension} ORDER BY period, {dimension}"""
    return {
        "sql": sql,
        "params": params,
        "rows": query_readonly(path, sql, params),
        "source": "orders",
        "window": {
            "baseline": "2026-09-01—2026-09-07",
            "current": "2026-09-08—2026-09-14",
        },
    }


def analyze(result):
    rows = result["rows"]
    by_period = {
        p: [row for row in rows if row["period"] == p] for p in ["baseline", "current"]
    }
    if not all(by_period.values()):
        raise ValueError("对比周期缺少数据，停止生成结论")
    totals = {}
    for period, records in by_period.items():
        orders = sum(row["orders"] for row in records)
        refunds = sum(row["refunds"] for row in records)
        if orders == 0:
            raise ValueError("分母为零，不能计算退款率")
        totals[period] = {
            "orders": orders,
            "refunds": refunds,
            "rate": refunds / orders,
        }
    segments = []
    for row in by_period["current"]:
        before = next(
            (r for r in by_period["baseline"] if r["segment"] == row["segment"]), None
        )
        if not before or not before["orders"] or not row["orders"]:
            continue
        baseline = before["refunds"] / before["orders"]
        current = row["refunds"] / row["orders"]
        # Excess refunds at baseline segment rate; descriptive, not causal.
        excess = row["refunds"] - row["orders"] * baseline
        segments.append(
            {
                "segment": row["segment"],
                "baseline_rate": baseline,
                "current_rate": current,
                "delta_pp": (current - baseline) * 100,
                "excess_refunds": excess,
                "orders": row["orders"],
            }
        )
    return {
        "totals": totals,
        "delta_pp": (totals["current"]["rate"] - totals["baseline"]["rate"]) * 100,
        "segments": sorted(
            segments, key=lambda row: row["excess_refunds"], reverse=True
        ),
        "method": "分组退款率与基准率下的超额退款量；描述性分析，不证明因果",
    }


def retrieve_rules(query):
    path = Path(__file__).resolve().parents[1] / "data" / "rules.json"
    rules = json.loads(path.read_text(encoding="utf-8"))
    terms = {query[i : i + 2] for i in range(len(query) - 1)}
    scored = [
        (len(terms & {r["text"][i : i + 2] for i in range(len(r["text"]) - 1)}), r)
        for r in rules
    ]
    return [r for score, r in sorted(scored, key=lambda pair: -pair[0]) if score > 0][
        :3
    ]


def compose_report(question, plan, result, analysis, rules):
    totals = analysis["totals"]
    lead = analysis["segments"][0] if analysis["segments"] else None
    lines = [
        "# 退款率异常分析",
        "",
        "数据来源：固定种子生成的模拟订单，不代表真实企业经营。",
        f"问题：{question}",
        f"口径：退款订单数 / 已创建订单数；按 {plan['dimension']} 拆解。",
        f"基准周期：{result['window']['baseline']}；当前周期：{result['window']['current']}。",
        "",
        f"- 基准期：{totals['baseline']['refunds']}/{totals['baseline']['orders']}，退款率 {totals['baseline']['rate']:.2%}。",
        f"- 当前期：{totals['current']['refunds']}/{totals['current']['orders']}，退款率 {totals['current']['rate']:.2%}。",
        f"- 变化：{analysis['delta_pp']:+.2f} 个百分点。",
    ]
    if lead:
        lines += [
            f"- 超额退款量最高的分组：{lead['segment']}，退款率变化 {lead['delta_pp']:+.2f} 个百分点。"
        ]
    lines += [
        "",
        "## 证据与限制",
        "上述数值由 SQL 结果和 Python 计算产生。当前证据只能定位异常分组，不能证明退款的业务原因。",
    ]
    lines += [f"- [{rule['id']}] {rule['text']}" for rule in rules] or [
        "- 业务规则检索暂不可用，报告仅包含数据事实。"
    ]
    lines += [
        "",
        "## 下一步核查",
        "核对异常分组的退款原因码、活动记录和订单明细，再决定是否调整投放。",
        "",
        "```sql",
        result["sql"],
        "```",
        "参数：" + json.dumps(result["params"], ensure_ascii=False),
    ]
    return "\n".join(lines)
