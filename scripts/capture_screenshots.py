"""Capture the current UI against an isolated API and worker for documentation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright


def main():
    repo = Path.cwd().name
    knowledge = repo == "ops-knowledge-rag"
    state = Path(tempfile.mkdtemp(prefix="ui-capture-"))
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = "sqlite:///" + (state / "capture.db").as_posix()
    os.environ["MODEL_BASE_URL"] = ""
    os.environ["MODEL_API_KEY"] = ""
    os.environ["MODEL_NAME"] = ""
    from app.main import create_app
    from app.platform import Base, Platform, User, password_hasher
    platform = Platform()
    create_app(platform)
    Base.metadata.create_all(platform.engine)
    email, password = "docs-admin@example.test", "Capture-Only-Account-2026"
    with platform.transaction() as db:
        db.add(User(tenant="project-docs", email=email, name="项目管理员",
                    role="admin", departments=["运维", "业务"],
                    password_hash=password_hasher.hash(password)))
    platform.engine.dispose()
    destination = Path("docs/screenshots")
    destination.mkdir(parents=True, exist_ok=True)
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:create_app",
                            "--factory", "--host", "127.0.0.1", "--port", "18080"])
    worker = subprocess.Popen([sys.executable, "-m", "app.worker"])
    try:
        client = httpx.Client(base_url="http://127.0.0.1:18080", timeout=30)
        for _ in range(120):
            try:
                if client.get("/api/ready").is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("API/worker not ready")
        login = client.post("/api/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        if knowledge:
            doc = "# 支付接口超时处理\n\n开发样例资料，用于文档接入测试。\n\n## PAY_TIMEOUT\n支付超时后，先使用原业务订单号查询支付状态。确认失败后再执行幂等重试，不直接创建新的扣款请求。\n\n## 幂等要求\n同一业务订单重复提交时返回已有支付结果。"
            uploaded = client.post("/api/documents", data={"department": "运维", "version": "v1.0"},
                                   files={"file": ("支付接口处理规范.md", doc.encode(), "text/markdown")})
            uploaded.raise_for_status()
            for _ in range(120):
                rows = client.get("/api/documents").json()
                if any(row.get("searchable") and row.get("status") == "ready" for row in rows):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Document indexing not complete")
        else:
            csv = "order_id,ordered_at,channel,product,refunded\n"
            for i in range(60):
                csv += f"order-{i},{'2026-08-10' if i < 30 else '2026-09-10'},{'线上渠道' if i % 2 else '门店渠道'},标准产品,{int(i % 7 == 0)}\n"
            response = client.post("/api/datasets", files={"file": ("开发样例订单.csv", csv.encode(), "text/csv")})
            response.raise_for_status()
            rule = "# 退款分析规则\n\n开发样例规则。\n\n## 统计口径\n按订单日期计算订单退款率，对比基准期与当前期，先按渠道拆分，再检查产品构成变化。退款率上升不直接证明渠道存在质量问题。"
            response = client.post("/api/rules", files={"file": ("退款分析规范.md", rule.encode(), "text/markdown")})
            response.raise_for_status()
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1512, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://127.0.0.1:18080", wait_until="networkidle")
            assert page.locator("#backend").count() == 0
            page.screenshot(path=str(destination / "login.png"), full_page=True)
            page.locator("#email").fill(email)
            page.locator("#password").fill(password)
            page.locator("#login button[type=submit]").click()
            page.locator(".sidebar").wait_for()
            views = (["documents", "ask", "users", "audit"] if knowledge else
                     ["datasets", "rules", "agents", "tasks", "users", "audit", "notifications"])
            for view in views:
                page.locator(f'a[href="#{view}"]').click()
                page.wait_for_load_state("networkidle")
                page.locator("#view h1").wait_for()
                assert page.locator("#feedback .error").count() == 0
                if view == "tasks":
                    page.locator('select[name="dataset_id"]').select_option(index=1)
                    page.locator('textarea[name="question"]').fill("对比两期订单退款率，按渠道拆分，结合退款规则给出排查建议。")
                    for field, value in {"baseline_start": "2026-08-01", "baseline_end": "2026-08-31",
                                         "current_start": "2026-09-01", "current_end": "2026-09-15"}.items():
                        page.locator(f'[name="{field}"]').fill(value)
                    page.screenshot(path=str(destination / "create-task.png"), full_page=True)
                page.screenshot(path=str(destination / (view + ".png")), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), view
                page.set_viewport_size({"width": 1512, "height": 1100})
            page.locator(f'a[href="#{"ask" if knowledge else "tasks"}"]').click()
            page.wait_for_load_state("networkidle")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(destination / "mobile.png"), full_page=True)
            assert not errors, errors
            browser.close()
        report = {"project": repo, "commit": os.getenv("GITHUB_SHA", ""),
                  "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "database": "isolated SQLite", "model_generation": "not exercised",
                  "javascript_errors": errors, "files": {}}
        for path in destination.glob("*.png"):
            if path.name in {"login.png", "documents.png", "ask.png", "users.png", "audit.png",
                             "datasets.png", "rules.png", "agents.png", "tasks.png", "notifications.png",
                             "create-task.png", "mobile.png"}:
                report["files"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (destination / "capture-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        for process in (api, worker):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
