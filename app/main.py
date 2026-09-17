from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .platform import Platform, attach_common
from .production import Insight

load_dotenv()


def create_app(platform=None, agent_model=None):
    platform = platform or Platform()
    insight = Insight(platform, agent_model)

    @asynccontextmanager
    async def lifespan(app):
        yield
        platform.engine.dispose()

    app = FastAPI(title="企业多智能体业务分析与审批平台", version="1.0.0", lifespan=lifespan)
    app.state.platform = platform
    app.state.insight = insight
    app.state.handle_job = insight.handle_job
    attach_common(app, platform)
    app.include_router(insight.router())
    app.mount(
        "/",
        StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True),
        name="web",
    )
    return app
