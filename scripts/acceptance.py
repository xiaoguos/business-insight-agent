"""Opt-in LIVE model/business acceptance. Writes real task/data and consumes quota."""

import argparse
import getpass
import json
import os
from pathlib import Path
import time
import uuid
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True, help="HTTPS service base URL, no /api")
    parser.add_argument("--orders", required=True, type=Path)
    parser.add_argument("--rule", required=True, type=Path)
    parser.add_argument("--baseline-start", required=True)
    parser.add_argument("--baseline-end", required=True)
    parser.add_argument("--current-start", required=True)
    parser.add_argument("--current-end", required=True)
    parser.add_argument(
        "--question", default="按渠道分析退款率变化，结合业务规则给出复核建议。"
    )
    parser.add_argument("--output", type=Path, default=Path("live-acceptance.json"))
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Confirm importing data, running models and publishing a report",
    )
    args = parser.parse_args()
    if not args.allow_write:
        parser.error(
            "This checks real services and writes data; explicitly pass --allow-write"
        )
    base = args.api.rstrip("/")
    if not base.startswith("https://") and not base.startswith(
        ("http://127.0.0.1:", "http://localhost:")
    ):
        parser.error("Remote API must use HTTPS")
    with httpx.Client(base_url=base + "/api", timeout=30) as client:

        def call(method, path, token=None, **kwargs):
            response = client.request(
                method,
                path,
                headers={"Authorization": "Bearer " + token} if token else {},
                **kwargs,
            )
            response.raise_for_status()
            return response.json()

        def login(kind):
            email = os.getenv("ACCEPTANCE_" + kind + "_EMAIL") or input(
                kind + " email: "
            )
            password = os.getenv("ACCEPTANCE_" + kind + "_PASSWORD") or getpass.getpass(
                kind + " password: "
            )
            return call(
                "POST", "/auth/login", json={"email": email, "password": password}
            )

        admin = login("ADMIN")
        analyst = login("ANALYST")
        reviewer = login("REVIEWER")
        if analyst["user"]["id"] == reviewer["user"]["id"]:
            raise RuntimeError("Reviewer must differ from report creator")
        if len({u["user"]["tenant"] for u in [admin, analyst, reviewer]}) != 1:
            raise RuntimeError("Acceptance accounts must belong to the same tenant")
        admin_token, owner_token, review_token = [
            u["access_token"] for u in [admin, analyst, reviewer]
        ]
        dataset = call(
            "POST",
            "/datasets",
            admin_token,
            files={"file": (args.orders.name, args.orders.read_bytes())},
        )
        rule = call(
            "POST",
            "/rules",
            admin_token,
            files={"file": (args.rule.name, args.rule.read_bytes())},
        )
        task = call(
            "POST",
            "/tasks",
            owner_token,
            json={
                "question": args.question,
                "dataset_id": dataset["id"],
                "rule_ids": [rule["id"]],
                "require_rules": True,
                "baseline_start": args.baseline_start,
                "baseline_end": args.baseline_end,
                "current_start": args.current_start,
                "current_end": args.current_end,
                "request_key": "live-acceptance-" + uuid.uuid4().hex,
            },
        )
        path = "/tasks/" + task["id"]

        def wait_for(states, timeout):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                value = call("GET", path, owner_token)
                if value["state"] in states:
                    return value
                if value["state"] in {"failed", "notification_failed", "rejected"}:
                    raise RuntimeError("Live task failed: " + value["error"])
                time.sleep(2)
            raise TimeoutError("Live task did not reach required state")

        generated = wait_for({"awaiting_approval"}, 600)
        runs = call("GET", path + "/agents", owner_token)
        if {r["agent"] for r in runs if r["state"] == "succeeded"} != {
            "conductor",
            "analysis",
            "knowledge",
            "report",
        }:
            raise RuntimeError("Not all four agents succeeded")
        if not generated["result"]["evidence"]:
            raise RuntimeError("No business rule evidence in report")
        print("Task:", task["id"])
        print("Review this exact report before publishing:")
        print(generated["result"]["report"])
        if (
            input("Type APPROVE to confirm this report as the reviewer: ").strip()
            != "APPROVE"
        ):
            raise RuntimeError(
                "Stopped with report awaiting human review; nothing published"
            )
        call(
            "POST",
            path + "/approval",
            review_token,
            json={
                "approved": True,
                "report_hash": generated["report_hash"],
                "note": "真实服务验收：人工检查报告与来源后批准",
            },
        )
        done = wait_for({"completed"}, 120)
        notifications = call("GET", "/notifications", owner_token)
        if sum(n["task_id"] == task["id"] for n in notifications) != 1:
            raise RuntimeError("Published notification missing or duplicated")
        evidence = {
            "checked_at": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
            "api": base,
            "task_id": task["id"],
            "state": done["state"],
            "report_hash": done["report_hash"],
            "agents": [
                {
                    "agent": r["agent"],
                    "model_calls": r["model_calls"],
                    "total_tokens": r["total_tokens"],
                }
                for r in runs
            ],
            "notification_count": 1,
            "model_mode": "live",
        }
        args.output.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            "PASS: live multi-agent generation, human review and publication verified."
        )
        for token in [admin_token, owner_token, review_token]:
            call("POST", "/auth/logout", token)


if __name__ == "__main__":
    main()
