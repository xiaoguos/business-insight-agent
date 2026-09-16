from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import TypedDict
import uuid
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, interrupt
from .data import seed_database
from .planner import plan_question
from .tools import summarize, analyze, retrieve_rules, compose_report


class State(TypedDict, total=False):
    task_id: str
    question: str
    planner: str
    fault: str
    plan: dict
    result: dict
    analysis: dict
    rules: list
    report: str
    status: str
    error: str
    warning: str
    approved: bool
    events: list


def event(state, node, event_status, detail="", **updates):
    return {
        **updates,
        "events": state.get("events", [])
        + [
            {
                "node": node,
                "status": event_status,
                "detail": detail,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }


class Workflow:
    def __init__(self, directory="data/runtime"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.orders_path = self.directory / "orders.db"
        self.meta_path = self.directory / "tasks.db"
        seed_database(self.orders_path)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, request_key TEXT UNIQUE, question TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS outbox(task_id TEXT PRIMARY KEY, report_hash TEXT, body TEXT, created_at TEXT);
            """)
        self.stack = ExitStack()
        self.checkpointer = self.stack.enter_context(
            SqliteSaver.from_conn_string(str(self.directory / "checkpoints.db"))
        )
        # Single-process serialized writer: explicit prototype boundary, no unsafe
        # concurrent resumption of the same checkpoint during repeated approvals.
        self.lock = threading.RLock()
        graph = StateGraph(State)
        for name, fn in [
            ("plan", self.plan),
            ("query", self.query),
            ("analyze", self.analyze),
            ("retrieve", self.retrieve),
            ("report", self.report),
            ("approval", self.approval),
            ("notify", self.notify),
        ]:
            graph.add_node(name, fn)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges(
            "plan", lambda s: "query" if s["status"] == "running" else END
        )
        graph.add_conditional_edges(
            "query", lambda s: "analyze" if s["status"] == "running" else END
        )
        graph.add_conditional_edges(
            "analyze", lambda s: "retrieve" if s["status"] == "running" else END
        )
        graph.add_edge("retrieve", "report")
        graph.add_edge("report", "approval")
        graph.add_conditional_edges(
            "approval", lambda s: "notify" if s.get("approved") else END
        )
        graph.add_edge("notify", END)
        self.graph = graph.compile(checkpointer=self.checkpointer)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.meta_path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def close(self):
        self.stack.close()

    def config(self, task_id):
        return {"configurable": {"thread_id": task_id}}

    def plan(self, state):
        try:
            plan = plan_question(state["question"], state["planner"]).model_dump()
            return event(
                state,
                "plan",
                "ok" if plan["supported"] else "input_required",
                plan["clarification"],
                plan=plan,
                status="running" if plan["supported"] else "input_required",
            )
        except Exception as error:
            return event(
                state,
                "plan",
                "failed",
                type(error).__name__,
                status="failed",
                error="规划失败，未执行数据查询",
            )

    def query(self, state):
        for attempt in range(1, 3):
            try:
                if state.get("fault") == "query" or (
                    state.get("fault") == "query_once" and attempt == 1
                ):
                    raise TimeoutError("injected query timeout")
                plan = state["plan"]
                result = summarize(
                    self.orders_path,
                    plan["dimension"],
                    plan["channel"],
                    plan["product"],
                )
                return event(state, "query", "ok", f"attempts={attempt}", result=result)
            except (TimeoutError, sqlite3.OperationalError) as error:
                if attempt == 2:
                    return event(
                        state,
                        "query",
                        "failed",
                        f"attempts=2; {type(error).__name__}",
                        status="failed",
                        error="关键数据查询失败，停止分析和通知",
                    )
                time.sleep(0.02)

    def analyze(self, state):
        try:
            return event(state, "analyze", "ok", analysis=analyze(state["result"]))
        except ValueError as error:
            return event(
                state,
                "analyze",
                "failed",
                str(error),
                status="failed",
                error=str(error),
            )

    def retrieve(self, state):
        if state.get("fault") == "rules":
            return event(
                state,
                "retrieve",
                "degraded",
                "规则检索故障；仅生成数据事实",
                rules=[],
                warning="未取得业务规则，原因解释受限",
            )
        return event(state, "retrieve", "ok", rules=retrieve_rules(state["question"]))

    def report(self, state):
        report = compose_report(
            state["question"],
            state["plan"],
            state["result"],
            state["analysis"],
            state["rules"],
        )
        return event(state, "report", "ok", report=report, status="awaiting_approval")

    def approval(self, state):
        # No side effect before interrupt: this node is replayed upon resume.
        approved = interrupt(
            {
                "task_id": state["task_id"],
                "action": "将报告写入本地通知发件箱",
                "report_hash": hashlib.sha256(state["report"].encode()).hexdigest(),
            }
        )
        return event(
            state,
            "approval",
            "approved" if approved else "rejected",
            approved=bool(approved),
            status="running" if approved else "rejected",
        )

    def notify(self, state):
        if state.get("fault") == "notify":
            return event(
                state,
                "notify",
                "failed",
                "模拟通知故障；报告和审批保留",
                status="notification_failed",
                error="通知失败，可以单独重试",
            )
        digest = hashlib.sha256(state["report"].encode()).hexdigest()
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO outbox VALUES(?,?,?,?)",
                (
                    state["task_id"],
                    digest,
                    state["report"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return event(
            state,
            "notify",
            "ok",
            "本地发件箱；未向外部联系人发送",
            status="completed",
            error="",
        )

    def create(self, question, request_key=None, fault="", planner=None):
        with self.lock:
            request_key = request_key or uuid.uuid4().hex
            with self.db() as db:
                old = db.execute(
                    "SELECT id,question FROM tasks WHERE request_key=?", (request_key,)
                ).fetchone()
                if old:
                    if old[1] != question:
                        raise ValueError("同一幂等键不能用于不同问题")
                    return self.get(old[0])
                task_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?)",
                    (
                        task_id,
                        request_key,
                        question,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            self.graph.invoke(
                {
                    "task_id": task_id,
                    "question": question,
                    "planner": planner or os.getenv("INSIGHT_PLANNER", "rules"),
                    "fault": fault,
                    "status": "running",
                    "events": [],
                },
                self.config(task_id),
            )
            return self.get(task_id)

    def get(self, task_id):
        with self.lock:
            with self.db() as db:
                row = db.execute(
                    "SELECT id,question FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                count = db.execute(
                    "SELECT COUNT(*) FROM outbox WHERE task_id=?", (task_id,)
                ).fetchone()[0]
            if not row:
                raise KeyError(task_id)
            snapshot = self.graph.get_state(self.config(task_id))
            return {
                "task_id": task_id,
                "question": row[1],
                "status": "interrupted",
                **dict(snapshot.values),
                "next": list(snapshot.next),
                "notification_count": count,
            }

    def approve(self, task_id, approved):
        with self.lock:
            state = self.get(task_id)
            if state["status"] in {"completed", "rejected", "notification_failed"}:
                return state
            if state["status"] != "awaiting_approval":
                raise ValueError("当前任务不处于待审批状态")
            self.graph.invoke(Command(resume=approved), self.config(task_id))
            return self.get(task_id)

    def retry(self, task_id):
        with self.lock:
            state = self.get(task_id)
            if state["status"] == "notification_failed":
                # Start from approval's successor with the existing approved report.
                self.graph.update_state(
                    self.config(task_id),
                    {"fault": "", "status": "running", "error": ""},
                    as_node="approval",
                )
                self.graph.invoke(None, self.config(task_id))
            elif state["status"] == "failed":
                self.graph.invoke(
                    {
                        "task_id": task_id,
                        "question": state["question"],
                        "planner": state["planner"],
                        "fault": "",
                        "status": "running",
                        "error": "",
                        "events": state.get("events", []),
                    },
                    self.config(task_id),
                )
            elif state["status"] == "interrupted":
                if self.graph.get_state(self.config(task_id)).next:
                    self.graph.invoke(None, self.config(task_id))
                else:
                    self.graph.invoke(
                        {
                            "task_id": task_id,
                            "question": state["question"],
                            "planner": os.getenv("INSIGHT_PLANNER", "rules"),
                            "fault": "",
                            "status": "running",
                            "events": [],
                        },
                        self.config(task_id),
                    )
            else:
                raise ValueError("只有失败或中断任务可以重试")
            return self.get(task_id)
