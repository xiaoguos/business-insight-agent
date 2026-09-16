from contextlib import asynccontextmanager
import os
from pathlib import Path
import secrets
from typing import Literal
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from .workflow import Workflow

load_dotenv()


class TaskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    request_key: str | None = Field(default=None, max_length=120)
    fault: Literal["", "query", "query_once", "rules", "notify"] = ""


class Approval(BaseModel):
    approved: bool


def create_app(directory=None):
    workflow = Workflow(directory or os.getenv("INSIGHT_DATA_DIR", "data/runtime"))

    @asynccontextmanager
    async def lifespan(app):
        yield
        workflow.close()

    app = FastAPI(title="Business Insight Agent", version="0.1.0", lifespan=lifespan)
    app.state.workflow = workflow
    app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "https://xiaoguos.github.io").split(","), allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Authorization"])

    def authorize(authorization: str | None = Header(default=None)):
        token = os.getenv("API_TOKEN", "")
        if token and not secrets.compare_digest(authorization or "", "Bearer " + token):
            raise HTTPException(401, "访问令牌不正确")

    def perform(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as error:
            raise HTTPException(404, "任务不存在") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/health")
    def health():
        return {"status": "ok", "planner": os.getenv("INSIGHT_PLANNER", "rules"), "notifications": "local_outbox", "authorization": bool(os.getenv("API_TOKEN"))}

    @app.post("/api/tasks", dependencies=[Depends(authorize)])
    def create(request: TaskRequest):
        return perform(workflow.create, **request.model_dump())

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(authorize)])
    def get(task_id: str):
        return perform(workflow.get, task_id)

    @app.post("/api/tasks/{task_id}/approval", dependencies=[Depends(authorize)])
    def approve(task_id: str, approval: Approval):
        return perform(workflow.approve, task_id, approval.approved)

    @app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(authorize)])
    def retry(task_id: str):
        return perform(workflow.retry, task_id)

    app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True), name="web")
    return app


app = create_app()
