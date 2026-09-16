"""Production workflow: immutable imports, bounded model plans, four-eyes review."""

import csv
from datetime import date
import hashlib
import io
import json
import os
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from .models import AgentRun, BusinessRule, Dataset, Notification, Order, Task
from .agents import AgentTeam, CAPABILITIES, JsonModel
from .platform import Job, User, uid, iso_time


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=2000)
    dataset_id: str = Field(min_length=32, max_length=32)
    baseline_start: date
    baseline_end: date
    current_start: date
    current_end: date
    request_key: str = Field(min_length=8, max_length=100)
    rule_ids: list[str] = Field(default_factory=list, max_length=30)
    require_rules: bool = False

    @model_validator(mode="after")
    def periods(self):
        if self.require_rules and not self.rule_ids:
            raise ValueError("要求规则支撑时必须至少选择一份业务规则")
        if (
            not self.baseline_start
            <= self.baseline_end
            < self.current_start
            <= self.current_end
        ):
            raise ValueError("对比期必须早于当前期且不能重叠")
        if (self.current_end - self.baseline_start).days > 730:
            raise ValueError("分析范围不能超过730天")
        return self


class Insight:
    def __init__(self, platform, agent_model=None):
        self.platform = platform
        self.agent_model = agent_model or JsonModel()

    def catalog(self, tenant, dataset_id):
        with self.platform.Session() as db:
            dataset = db.scalar(
                select(Dataset).where(
                    Dataset.id == dataset_id, Dataset.tenant == tenant
                )
            )
            if not dataset:
                raise ValueError("数据集不可用")
            catalog = {
                name: list(
                    db.scalars(
                        select(getattr(Order, name))
                        .where(Order.dataset_id == dataset.id)
                        .distinct()
                        .limit(201)
                    )
                )
                for name in ["channel", "product"]
            }
        if any(len(values) > 200 for values in catalog.values()):
            raise ValueError("维度基数超过200，请缩小数据范围")
        return catalog

    def query_node(self, state):
        plan = state["plan"]
        payload = state["payload"]
        dimension = getattr(Order, plan["dimension"])
        rows = []
        with self.platform.Session() as db:
            for period in ["baseline", "current"]:
                stmt = (
                    select(dimension, func.count(Order.id), func.sum(Order.refunded))
                    .join(Dataset)
                    .where(
                        Dataset.id == payload["dataset_id"],
                        Dataset.tenant == state["tenant"],
                        Order.ordered_at
                        >= date.fromisoformat(payload[period + "_start"]),
                        Order.ordered_at
                        <= date.fromisoformat(payload[period + "_end"]),
                    )
                )
                for name in ["channel", "product"]:
                    if plan[name] is not None:
                        stmt = stmt.where(getattr(Order, name) == plan[name])
                values = db.execute(stmt.group_by(dimension).order_by(dimension)).all()
                if not values:
                    raise ValueError("所选时间段或过滤条件缺少数据：" + period)
                rows.extend(
                    {
                        "period": period,
                        "dimension": key,
                        "orders": count,
                        "refunds": refunds,
                    }
                    for key, count, refunds in values
                )
        return {"rows": rows}

    def analyze_node(self, state):
        totals = {
            period: {"orders": 0, "refunds": 0} for period in ["baseline", "current"]
        }
        for row in state["rows"]:
            for metric in ["orders", "refunds"]:
                totals[row["period"]][metric] += row[metric]
        for value in totals.values():
            value["rate"] = value["refunds"] / value["orders"]
        segments = []
        for key in sorted({r["dimension"] for r in state["rows"]}):
            by_period = {r["period"]: r for r in state["rows"] if r["dimension"] == key}
            baseline, current = by_period.get("baseline"), by_period.get("current")
            comparable = bool(baseline and current)
            segments.append(
                {
                    "name": key,
                    "baseline": baseline,
                    "current": current,
                    "comparable": comparable,
                    "excess_refunds": round(
                        current["refunds"]
                        - current["orders"] * baseline["refunds"] / baseline["orders"],
                        4,
                    )
                    if comparable
                    else None,
                }
            )
        return {
            "analysis": {
                "totals": totals,
                "delta_pp": 100
                * (totals["current"]["rate"] - totals["baseline"]["rate"]),
                "segments": segments,
            }
        }

    def report_node(self, state):
        a = state["analysis"]
        p = state["payload"]
        lines = [
            "# 退款率分析报告",
            f"数据快照：{p['dataset_id']}",
            f"基准期：{p['baseline_start']} 至 {p['baseline_end']}；当前期：{p['current_start']} 至 {p['current_end']}",
            f"基准期订单退款率 {a['totals']['baseline']['rate']:.2%}；当前期 {a['totals']['current']['rate']:.2%}；变动 {a['delta_pp']:+.2f} 个百分点。",
            "口径：退款标记为1的订单数 / 订单总数，不是退款金额率；按订单日期归属。",
            "## 分组结果",
        ]
        for segment in a["segments"]:
            lines.append(
                f"- {segment['name']}："
                + (
                    f"超额退款订单 {segment['excess_refunds']:+.2f}"
                    if segment["comparable"]
                    else "缺少可比较期间，未计算贡献"
                )
            )
        lines += [
            "## 解读边界",
            "超额退款是当前退款数减去按该组基准退款率预计的退款数，是排查线索，不证明因果关系。分组构成变化、样本不足和退款滞后均可能影响结果。报告数字由程序计算，模型不编写数值结论。",
        ]
        return {
            "result": {
                "plan": state["plan"],
                "analysis": a,
                "report": "\n\n".join(lines),
            }
        }

    def handle_job(self, job):
        with self.platform.Session() as db:
            task = db.scalar(
                select(Task).where(
                    Task.id == job.payload["task_id"], Task.tenant == job.tenant
                )
            )
            if not task:
                raise ValueError("Task not found")
            payload = task.payload
        if job.kind == "analyze":
            result = AgentTeam(self, job, payload, self.agent_model).invoke()
            with self.platform.transaction() as db:
                self.platform.fenced(db, job)
                task = db.scalar(
                    select(Task).where(Task.id == task.id).with_for_update()
                )
                if task.job_id != job.id:
                    raise ValueError("Superseded task job")
                task.result = result
                task.report_hash = hashlib.sha256(
                    json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                task.state = "awaiting_approval"
                self.platform.finish(db, job)
        elif job.kind == "notify":
            with self.platform.transaction() as db:
                self.platform.fenced(db, job)
                task = db.scalar(
                    select(Task).where(Task.id == task.id).with_for_update()
                )
                if (
                    task.job_id != job.id
                    or task.state != "publishing"
                    or not task.reviewer_id
                    or task.report_hash != job.payload["report_hash"]
                ):
                    raise ValueError("Approval binding mismatch")
                if not db.scalar(
                    select(Notification.id).where(Notification.task_id == task.id)
                ):
                    db.add(
                        Notification(
                            task_id=task.id,
                            tenant=task.tenant,
                            recipient_id=task.owner_id,
                            report_hash=task.report_hash,
                        )
                    )
                task.state = "completed"
                self.platform.finish(db, job)
        else:
            raise ValueError("Unknown job kind")

    def router(self):
        router = APIRouter(prefix="/api")
        authorized = self.platform.require()
        admin = self.platform.require("admin")
        analyst = self.platform.require("admin", "analyst")
        reviewer = self.platform.require("admin", "reviewer")

        def visible(user):
            filters = [Task.tenant == user.tenant]
            if user.role not in {"admin", "reviewer"}:
                filters.append(Task.owner_id == user.id)
            return filters

        def public_task(db, t):
            job = db.get(Job, t.job_id)
            state = t.state
            if job and job.state in {"failed", "queued", "running"}:
                state = ("notification_" if t.state == "publishing" else "") + job.state
            return {
                "id": t.id,
                "owner_id": t.owner_id,
                "state": state,
                "payload": t.payload,
                "result": t.result,
                "report_hash": t.report_hash,
                "reviewer_id": t.reviewer_id,
                "review_note": t.review_note,
                "job_id": t.job_id,
                "error": job.error if job else "",
                "created_at": iso_time(t.created_at),
            }

        @router.get("/agents")
        def agent_catalog(user: User = Depends(authorized)):
            return [
                {
                    "id": role,
                    "tools": list(capabilities),
                    "max_model_turns": 4,
                    "max_tool_calls": 3,
                    "isolated_context": True,
                    "model_configured": bool(
                        os.getenv("MODEL_BASE_URL")
                        and (
                            os.getenv("MODEL_" + role.upper())
                            or os.getenv("MODEL_NAME")
                        )
                    ),
                }
                for role, capabilities in CAPABILITIES.items()
            ]

        @router.get("/datasets")
        def datasets(user: User = Depends(authorized)):
            with self.platform.Session() as db:
                return [
                    {
                        "id": d.id,
                        "name": d.name,
                        "row_count": d.row_count,
                        "created_at": iso_time(d.created_at),
                    }
                    for d in db.scalars(
                        select(Dataset)
                        .where(Dataset.tenant == user.tenant)
                        .order_by(Dataset.created_at.desc())
                        .limit(100)
                    )
                ]

        @router.get("/rules")
        def rules(user: User = Depends(authorized)):
            with self.platform.Session() as db:
                return [
                    {
                        "id": r.id,
                        "title": r.title,
                        "digest": r.digest,
                        "created_at": iso_time(r.created_at),
                    }
                    for r in db.scalars(
                        select(BusinessRule)
                        .where(BusinessRule.tenant == user.tenant)
                        .order_by(BusinessRule.created_at.desc())
                        .limit(200)
                    )
                ]

        @router.post("/rules", status_code=201)
        def import_rule(file: UploadFile = File(), user: User = Depends(admin)):
            if not (file.filename or "").lower().endswith((".md", ".txt")):
                raise HTTPException(422, "业务规则支持Markdown和TXT")
            content = file.file.read(32 * 1024 + 1)
            if not content or len(content) > 32 * 1024:
                raise HTTPException(413, "业务规则文件需在1字节至32KB之间")
            try:
                decoded = content.decode("utf-8-sig").strip()
            except UnicodeError as error:
                raise HTTPException(422, "文件必须为UTF-8编码") from error
            if not decoded or len(decoded) > 6000:
                raise HTTPException(
                    422, "规则内容需在1至6000字符之间，请按业务主题拆分"
                )
            with self.platform.transaction() as db:
                rule = BusinessRule(
                    tenant=user.tenant,
                    owner_id=user.id,
                    title=(file.filename or "rule.md")[:200],
                    content=decoded,
                    digest=hashlib.sha256(content).hexdigest(),
                )
                db.add(rule)
                db.flush()
                self.platform.audit(
                    db, user, "rule.import", rule.id, {"digest": rule.digest}
                )
                return {"id": rule.id, "title": rule.title, "digest": rule.digest}

        @router.post("/datasets", status_code=201)
        def upload(file: UploadFile = File(), user: User = Depends(admin)):
            payload = file.file.read(10 * 1024 * 1024 + 1)
            if not payload or len(payload) > 10 * 1024 * 1024:
                raise HTTPException(413, "CSV不能超过10MB")
            try:
                reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
                if reader.fieldnames != [
                    "order_id",
                    "ordered_at",
                    "channel",
                    "product",
                    "refunded",
                ]:
                    raise ValueError(
                        "表头必须依次为 order_id,ordered_at,channel,product,refunded"
                    )
                rows = []
                seen = set()
                for i, row in enumerate(reader, 2):
                    if len(rows) >= 50000:
                        raise ValueError("单次导入最多50000行")
                    if None in row or any(
                        v is None or not v.strip() for v in row.values()
                    ):
                        raise ValueError(f"第{i}行缺少字段或列数错误")
                    row = {k: v.strip() for k, v in row.items()}
                    if row["order_id"] in seen:
                        raise ValueError(f"第{i}行订单号重复")
                    if len(row["order_id"]) > 100 or any(
                        len(row[k]) > 80 for k in ["channel", "product"]
                    ):
                        raise ValueError(f"第{i}行字段过长")
                    if row["refunded"] not in {"0", "1"}:
                        raise ValueError(f"第{i}行refunded只能为0或1")
                    row["ordered_at"] = date.fromisoformat(row["ordered_at"])
                    row["refunded"] = int(row["refunded"])
                    seen.add(row["order_id"])
                    rows.append(row)
                if not rows:
                    raise ValueError("CSV没有数据")
            except (UnicodeError, ValueError, csv.Error) as error:
                raise HTTPException(422, str(error)) from error
            with self.platform.transaction() as db:
                dataset = Dataset(
                    tenant=user.tenant,
                    owner_id=user.id,
                    name=(file.filename or "orders.csv")[:200],
                    digest=hashlib.sha256(payload).hexdigest(),
                    row_count=len(rows),
                )
                db.add(dataset)
                db.flush()
                db.add_all([Order(dataset_id=dataset.id, **row) for row in rows])
                self.platform.audit(
                    db,
                    user,
                    "dataset.import",
                    dataset.id,
                    {"rows": len(rows), "digest": dataset.digest},
                )
                return {"id": dataset.id, "row_count": len(rows)}

        @router.post("/tasks", status_code=202)
        def create(data: TaskInput, user: User = Depends(analyst)):
            payload = data.model_dump(mode="json", exclude={"request_key"})
            with self.platform.transaction() as db:
                # Serialize each creator to make concurrent idempotent retries atomic.
                db.scalar(select(User).where(User.id == user.id).with_for_update())
                existing = db.scalar(
                    select(Task).where(
                        Task.tenant == user.tenant,
                        Task.owner_id == user.id,
                        Task.request_key == data.request_key,
                    )
                )
                if existing:
                    if existing.payload != payload:
                        raise HTTPException(409, "幂等键已用于不同请求")
                    return public_task(db, existing)
                if not db.scalar(
                    select(Dataset.id).where(
                        Dataset.id == data.dataset_id, Dataset.tenant == user.tenant
                    )
                ):
                    raise HTTPException(404, "数据集不存在")
                rules = list(
                    db.scalars(
                        select(BusinessRule.id).where(
                            BusinessRule.tenant == user.tenant,
                            BusinessRule.id.in_(data.rule_ids),
                        )
                    )
                )
                if len(set(data.rule_ids)) != len(data.rule_ids) or set(rules) != set(
                    data.rule_ids
                ):
                    raise HTTPException(422, "业务规则不存在、重复或无权访问")
                task_id = uid()
                job = Job(
                    tenant=user.tenant,
                    owner_id=user.id,
                    kind="analyze",
                    payload={"task_id": task_id},
                )
                db.add(job)
                db.flush()
                task = Task(
                    id=task_id,
                    tenant=user.tenant,
                    owner_id=user.id,
                    request_key=data.request_key,
                    payload=payload,
                    job_id=job.id,
                )
                db.add(task)
                db.flush()
                self.platform.audit(db, user, "task.create", task.id)
                return public_task(db, task)

        @router.get("/tasks")
        def tasks(user: User = Depends(authorized)):
            with self.platform.Session() as db:
                return [
                    public_task(db, t)
                    for t in db.scalars(
                        select(Task)
                        .where(*visible(user))
                        .order_by(Task.created_at.desc())
                        .limit(100)
                    )
                ]

        @router.get("/tasks/{task_id}")
        def get(task_id: str, user: User = Depends(authorized)):
            with self.platform.Session() as db:
                task = db.scalar(select(Task).where(Task.id == task_id, *visible(user)))
                if not task:
                    raise HTTPException(404, "任务不存在")
                return public_task(db, task)

        @router.get("/tasks/{task_id}/agents")
        def agent_runs(task_id: str, user: User = Depends(authorized)):
            with self.platform.Session() as db:
                if not db.scalar(
                    select(Task.id).where(Task.id == task_id, *visible(user))
                ):
                    raise HTTPException(404, "任务不存在")
                runs = db.scalars(
                    select(AgentRun)
                    .where(AgentRun.task_id == task_id)
                    .order_by(AgentRun.started_at)
                    .limit(200)
                ).all()
                return [
                    {
                        "id": r.id,
                        "agent": r.agent,
                        "goal": r.goal,
                        "state": r.state,
                        "job_id": r.job_id,
                        "job_attempt": r.job_attempt,
                        "events": r.events,
                        "model_calls": r.model_calls,
                        "total_tokens": r.total_tokens,
                        "error": r.error,
                        "started_at": iso_time(r.started_at),
                        "finished_at": iso_time(r.finished_at)
                        if r.finished_at
                        else None,
                    }
                    for r in runs
                ]

        class Review(BaseModel):
            approved: bool
            report_hash: str = Field(min_length=64, max_length=64)
            note: str = Field(min_length=2, max_length=1000)

        @router.post("/tasks/{task_id}/approval")
        def approve(task_id: str, data: Review, user: User = Depends(reviewer)):
            with self.platform.transaction() as db:
                task = db.scalar(
                    select(Task)
                    .where(Task.id == task_id, Task.tenant == user.tenant)
                    .with_for_update()
                )
                if not task:
                    raise HTTPException(404, "任务不存在")
                if task.owner_id == user.id:
                    raise HTTPException(403, "四眼审核：不能审核自己发起的报告")
                if (
                    task.state != "awaiting_approval"
                    or task.report_hash != data.report_hash
                ):
                    raise HTTPException(
                        409, "报告已变更或不处于待审核状态，请刷新后重试"
                    )
                task.reviewer_id = user.id
                task.review_note = data.note
                task.state = "publishing" if data.approved else "rejected"
                if data.approved:
                    job = Job(
                        tenant=task.tenant,
                        owner_id=task.owner_id,
                        kind="notify",
                        payload={"task_id": task.id, "report_hash": task.report_hash},
                    )
                    db.add(job)
                    db.flush()
                    task.job_id = job.id
                self.platform.audit(
                    db,
                    user,
                    "task.approve" if data.approved else "task.reject",
                    task.id,
                    {"report_hash": task.report_hash},
                )
                return public_task(db, task)

        @router.post("/tasks/{task_id}/retry", status_code=202)
        def retry(task_id: str, user: User = Depends(analyst)):
            with self.platform.transaction() as db:
                task = db.scalar(
                    select(Task)
                    .where(Task.id == task_id, Task.tenant == user.tenant)
                    .with_for_update()
                )
                if not task or (task.owner_id != user.id and user.role != "admin"):
                    raise HTTPException(404, "任务不存在")
                old = db.get(Job, task.job_id)
                if not old or old.state != "failed":
                    raise HTTPException(409, "仅失败任务可以重试")
                job = Job(
                    tenant=task.tenant,
                    owner_id=task.owner_id,
                    kind=old.kind,
                    payload=old.payload,
                )
                db.add(job)
                db.flush()
                task.job_id = job.id
                self.platform.audit(db, user, "task.retry", task.id, {"kind": old.kind})
                return public_task(db, task)

        @router.get("/notifications")
        def notifications(user: User = Depends(authorized)):
            with self.platform.Session() as db:
                return [
                    {
                        "id": n.id,
                        "task_id": n.task_id,
                        "report_hash": n.report_hash,
                        "created_at": iso_time(n.created_at),
                    }
                    for n in db.scalars(
                        select(Notification)
                        .where(
                            Notification.tenant == user.tenant,
                            Notification.recipient_id == user.id,
                        )
                        .order_by(Notification.created_at.desc())
                        .limit(100)
                    )
                ]

        return router
