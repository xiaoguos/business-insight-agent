PATH = "/api/tasks"
PAYLOAD = {"question": "按渠道分析退款率"}
"""Small HTTP concurrency probe. Results are environment-specific, not capacity claims."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
import httpx


async def run(args):
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=90, trust_env=False
    ) as client:
        health = (await client.get("/api/health")).json()

        async def one(index):
            async with semaphore:
                start = time.perf_counter()
                try:
                    response = await client.post(PATH, json=PAYLOAD)
                    return {
                        "status": response.status_code,
                        "latency_ms": (time.perf_counter() - start) * 1000,
                    }
                except httpx.HTTPError:
                    return {
                        "status": 0,
                        "latency_ms": (time.perf_counter() - start) * 1000,
                    }

        started = time.perf_counter()
        rows = await asyncio.gather(*(one(i) for i in range(args.requests)))
        elapsed = time.perf_counter() - started
    values = sorted(row["latency_ms"] for row in rows)
    errors = sum(row["status"] != 200 for row in rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "本地单进程、无生成模型的小规模 HTTP 并发模拟，不是生产容量测试",
        "health": health,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "error_count": errors,
        "error_rate": errors / args.requests,
        "p50_ms": round(statistics.median(values), 2),
        "p95_ms": round(values[min(len(values) - 1, int(len(values) * 0.95))], 2),
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(args.requests / elapsed, 2),
    }
    Path("docs/load-test.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.requests <= 100 or not 1 <= args.concurrency <= 10:
        parser.error("requests 1..100; concurrency 1..10")
    asyncio.run(run(args))
