"""Read-only release check; never prints credentials or model responses."""

import os
import sys
import httpx
from sqlalchemy import text
from app.platform import Platform


def main():
    failures = []
    try:
        platform = Platform()
        with platform.Session() as db:
            db.execute(text("SELECT 1"))
            revision = db.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
            if not revision:
                failures.append("Database migrations have not been applied")
        platform.engine.dispose()
    except Exception as error:
        failures.append("Database: " + type(error).__name__)
    base = os.getenv("MODEL_BASE_URL", "").rstrip("/")
    model = os.getenv("MODEL_NAME", "")
    if not base or not model:
        failures.append("Model endpoint/name missing")
    else:
        try:
            with httpx.Client(timeout=45) as client:
                result = client.post(
                    base + "/chat/completions",
                    headers={
                        "Authorization": "Bearer " + os.getenv("MODEL_API_KEY", "")
                    },
                    json={
                        "model": model,
                        "temperature": 0,
                        "max_tokens": 32,
                        "messages": [
                            {"role": "user", "content": 'Return JSON: {"ok":true}'}
                        ],
                        "response_format": {"type": "json_object"},
                    },
                )
                result.raise_for_status()
                import json

                value = json.loads(result.json()["choices"][0]["message"]["content"])
                if value.get("ok") is not True:
                    failures.append("Model JSON contract failed")
        except Exception as error:
            failures.append("Model: " + type(error).__name__)
    for failure in failures:
        print("FAIL:", failure)
    if failures:
        sys.exit(1)
    print("Database migration and live model JSON checks passed.")


if __name__ == "__main__":
    main()
