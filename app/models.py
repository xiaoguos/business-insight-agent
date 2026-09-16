from datetime import date, datetime
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from .platform import Base, now, uid


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    owner_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    digest: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("dataset_id", "order_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(100))
    ordered_at: Mapped[date] = mapped_column(Date, index=True)
    channel: Mapped[str] = mapped_column(String(80))
    product: Mapped[str] = mapped_column(String(80))
    refunded: Mapped[int] = mapped_column(Integer)


class Task(Base):
    __tablename__ = "analysis_tasks"
    __table_args__ = (UniqueConstraint("tenant", "owner_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    request_key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    job_id: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(30), default="queued")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    report_hash: Mapped[str] = mapped_column(String(64), default="")
    reviewer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("analysis_tasks.id"), unique=True)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    recipient_id: Mapped[str] = mapped_column(String(32), index=True)
    report_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BusinessRule(Base):
    __tablename__ = "business_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    owner_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("job_id", "job_attempt", "agent"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("analysis_tasks.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    job_attempt: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(30))
    goal: Mapped[str] = mapped_column(String(600))
    state: Mapped[str] = mapped_column(String(20), default="running")
    events: Mapped[list] = mapped_column(JSON, default=list)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(160), default="")
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
