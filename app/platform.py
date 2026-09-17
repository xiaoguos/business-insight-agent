"""Shared production foundation; copied into each independently deployed repository."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import secrets
import time
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from pwdlib import PasswordHash
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    JSON,
    String,
    Text,
    create_engine,
    select,
    update,
    or_,
    and_,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

log = logging.getLogger(__name__)
password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))


def now():
    return datetime.now(timezone.utc)


def iso_time(value):
    return (
        value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    ).isoformat()


def uid():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="member")
    departments: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    identity: Mapped[str] = mapped_column(String(64), index=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(100), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LeaseLost(RuntimeError):
    pass


class Platform:
    def __init__(self, url=None):
        url = url or os.getenv("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "DATABASE_URL is required; use scripts.bootstrap and Docker Compose"
            )
        if not url.startswith("postgresql") and os.getenv("APP_ENV") != "test":
            raise RuntimeError(
                "Production requires PostgreSQL; SQLite is allowed only in isolated tests"
            )
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            **(
                {"connect_args": {"check_same_thread": False}}
                if url.startswith("sqlite")
                else {"pool_size": 5, "max_overflow": 10}
            ),
        )
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        with self.Session() as db, db.begin():
            yield db

    def bootstrap(self, tenant, email, password):
        if len(password) < 14:
            raise ValueError(
                "Initial administrator password must contain at least 14 characters"
            )
        with self.transaction() as db:
            if db.scalar(select(User.id).where(User.email == email.lower())):
                return
            db.add(
                User(
                    tenant=tenant,
                    email=email.lower(),
                    name="系统管理员",
                    role="admin",
                    departments=[],
                    password_hash=password_hasher.hash(password),
                )
            )

    def audit(self, db, user, action, resource="", detail=None):
        db.add(
            Audit(
                tenant=user.tenant,
                user_id=user.id,
                action=action,
                resource_id=resource,
                detail=detail or {},
            )
        )

    def authenticate(self, authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "请先登录")
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with self.Session() as db:
            session = db.scalar(
                select(LoginSession).where(
                    LoginSession.token_hash == digest, LoginSession.expires_at > now()
                )
            )
            user = db.get(User, session.user_id) if session else None
            if not user or not user.active:
                raise HTTPException(401, "会话已过期或账号已停用，请重新登录")
            return user

    def require(self, *roles):
        def dependency(authorization: str | None = Header(default=None)):
            user = self.authenticate(authorization)
            if roles and user.role not in roles:
                raise HTTPException(403, "当前角色无权执行此操作")
            return user

        return dependency

    def claim(self):
        with self.transaction() as db:
            exhausted = db.scalars(
                select(Job)
                .where(
                    Job.state == "running", Job.lease_until < now(), Job.attempts >= 3
                )
                .with_for_update(skip_locked=True)
            ).all()
            for job in exhausted:
                job.state = "failed"
                job.error = "Worker lease expired repeatedly"
                job.updated_at = now()
            job = db.scalar(
                select(Job)
                .where(
                    or_(
                        Job.state == "queued",
                        and_(
                            Job.state == "running",
                            Job.lease_until < now(),
                            Job.attempts < 3,
                        ),
                    )
                )
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not job:
                return None
            job.state = "running"
            job.attempts += 1
            job.lease_token = uid()
            job.lease_until = now() + timedelta(seconds=120)
            job.updated_at = now()
            db.flush()
            return job

    def fenced(self, db, job):
        current = db.scalar(select(Job).where(Job.id == job.id).with_for_update())
        if (
            not current
            or current.state != "running"
            or current.lease_token != job.lease_token
        ):
            raise LeaseLost("worker no longer owns this job")
        return current

    def finish(self, db, job):
        current = self.fenced(db, job)
        current.state = "succeeded"
        current.error = ""
        current.updated_at = now()
        current.lease_until = None

    def fail(self, job, error):
        with self.transaction() as db:
            try:
                current = self.fenced(db, job)
            except LeaseLost:
                return
            current.state = "failed"
            current.error = str(error)[:500]
            current.updated_at = now()
            current.lease_until = None

    def heartbeat(self, worker_id, job=None):
        with self.transaction() as db:
            row = db.get(WorkerHeartbeat, worker_id)
            if row:
                row.at = now()
            else:
                db.add(WorkerHeartbeat(id=worker_id, at=now()))
            if job:
                db.execute(
                    update(Job)
                    .where(
                        Job.id == job.id,
                        Job.lease_token == job.lease_token,
                        Job.state == "running",
                    )
                    .values(lease_until=now() + timedelta(seconds=120))
                )

    def router(self):
        router = APIRouter(prefix="/api")
        authorized = self.require()
        admin = self.require("admin")

        class Login(BaseModel):
            email: str = Field(min_length=3, max_length=254)
            password: str = Field(min_length=1, max_length=256)

        class NewUser(BaseModel):
            email: str = Field(min_length=3, max_length=254)
            name: str = Field(min_length=1, max_length=100)
            password: str = Field(min_length=14, max_length=256)
            role: Literal["admin", "member", "analyst", "reviewer"] = "member"
            departments: list[str] = Field(default_factory=list, max_length=30)

        class EditUser(BaseModel):
            active: bool
            role: Literal["admin", "member", "analyst", "reviewer"]
            departments: list[str] = Field(default_factory=list, max_length=30)

        def public_user(user):
            return {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "tenant": user.tenant,
                "departments": user.departments,
                "active": user.active,
            }

        @router.post("/auth/login")
        def login(data: Login, request: Request):
            email = data.email.strip().lower()
            identity = hashlib.sha256(email.encode()).hexdigest()
            with self.transaction() as db:
                # Per-account database throttle. Same response for missing account.
                if self.engine.dialect.name == "postgresql":
                    db.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": int(identity[:15], 16)},
                    )
                recent = db.scalars(
                    select(LoginAttempt).where(
                        LoginAttempt.identity == identity,
                        LoginAttempt.at > now() - timedelta(minutes=10),
                    )
                ).all()
                if len(recent) >= 8:
                    raise HTTPException(429, "登录尝试过多，请十分钟后重试")
                user = db.scalar(select(User).where(User.email == email))
                verified = password_hasher.verify(
                    data.password, user.password_hash if user else dummy_password_hash
                )
                valid = user and user.active and verified
                if not valid:
                    db.add(LoginAttempt(identity=identity))
                else:
                    token = secrets.token_urlsafe(48)
                    db.add(
                        LoginSession(
                            token_hash=hashlib.sha256(token.encode()).hexdigest(),
                            user_id=user.id,
                            expires_at=now() + timedelta(minutes=60),
                        )
                    )
                    self.audit(db, user, "auth.login")
                    return {
                        "access_token": token,
                        "token_type": "bearer",
                        "expires_in": 3600,
                        "user": public_user(user),
                    }
            raise HTTPException(401, "账号或密码不正确")

        @router.get("/auth/me")
        def me(user: User = Depends(authorized)):
            return public_user(user)

        @router.post("/auth/logout")
        def logout(authorization: str = Header(), user: User = Depends(authorized)):
            with self.transaction() as db:
                session = db.get(
                    LoginSession, hashlib.sha256(authorization[7:].encode()).hexdigest()
                )
                if session:
                    db.delete(session)
                self.audit(db, user, "auth.logout")
            return {"ok": True}

        @router.get("/users")
        def users(user: User = Depends(admin)):
            with self.Session() as db:
                return [
                    public_user(u)
                    for u in db.scalars(
                        select(User)
                        .where(User.tenant == user.tenant)
                        .order_by(User.created_at)
                    ).all()
                ]

        @router.post("/users")
        def create_user(data: NewUser, user: User = Depends(admin)):
            with self.transaction() as db:
                if db.scalar(
                    select(User.id).where(User.email == data.email.strip().lower())
                ):
                    raise HTTPException(409, "账号已存在")
                created = User(
                    tenant=user.tenant,
                    email=data.email.strip().lower(),
                    name=data.name,
                    role=data.role,
                    departments=data.departments,
                    password_hash=password_hasher.hash(data.password),
                )
                db.add(created)
                db.flush()
                self.audit(db, user, "user.create", created.id, {"role": created.role})
                return public_user(created)

        @router.put("/users/{user_id}")
        def edit_user(user_id: str, data: EditUser, user: User = Depends(admin)):
            if user_id == user.id:
                raise HTTPException(409, "不能在此修改自己的角色或停用自己")
            with self.transaction() as db:
                target = db.scalar(
                    select(User)
                    .where(User.id == user_id, User.tenant == user.tenant)
                    .with_for_update()
                )
                if not target:
                    raise HTTPException(404, "账号不存在")
                target.active = data.active
                target.role = data.role
                target.departments = data.departments
                self.audit(
                    db,
                    user,
                    "user.update",
                    target.id,
                    {"role": target.role, "active": target.active},
                )
            return {"ok": True}

        @router.get("/audit")
        def audit(limit: int = 100, user: User = Depends(admin)):
            with self.Session() as db:
                rows = db.scalars(
                    select(Audit)
                    .where(Audit.tenant == user.tenant)
                    .order_by(Audit.at.desc())
                    .limit(max(1, min(limit, 200)))
                ).all()
                return [
                    {
                        "id": r.id,
                        "user_id": r.user_id,
                        "action": r.action,
                        "resource_id": r.resource_id,
                        "detail": r.detail,
                        "at": iso_time(r.at),
                    }
                    for r in rows
                ]

        @router.get("/jobs/{job_id}")
        def job_status(job_id: str, user: User = Depends(authorized)):
            with self.Session() as db:
                job = db.scalar(
                    select(Job).where(Job.id == job_id, Job.tenant == user.tenant)
                )
                if not job or (job.owner_id != user.id and user.role != "admin"):
                    raise HTTPException(404, "任务不存在")
                return {
                    "id": job.id,
                    "state": job.state,
                    "attempts": job.attempts,
                    "error": job.error,
                    "updated_at": iso_time(job.updated_at),
                }

        @router.get("/health")
        def health():
            return {"status": "ok", "service": "production", "version": "1.0.0"}

        @router.get("/ready")
        def ready():
            try:
                with self.Session() as db:
                    worker = db.scalar(
                        select(WorkerHeartbeat.id)
                        .where(WorkerHeartbeat.at > now() - timedelta(seconds=60))
                        .limit(1)
                    )
                    if not worker:
                        raise HTTPException(503, "后台处理服务尚未就绪")
                return {"status": "ready"}
            except HTTPException:
                raise
            except Exception as error:
                log.exception("readiness failed")
                raise HTTPException(503, "数据库不可用") from error

        return router


def attach_common(app, platform):
    from fastapi.middleware.cors import CORSMiddleware

    app.include_router(platform.router())
    origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_observability(request, call_next):
        request_id = uid()
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled request %s", request_id)
            from fastapi.responses import JSONResponse

            response = JSONResponse(
                {"detail": "服务处理失败", "request_id": request_id}, status_code=500
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = (
            "no-store"
            if request.url.path.startswith("/api")
            or "no-store" in response.headers.get("Cache-Control", "")
            else "no-cache"
        )
        log.info(
            "request id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response
