"""Synthetic, reproducible order fixtures. Not real enterprise data."""

from datetime import date, timedelta
from pathlib import Path
import random
import sqlite3
from contextlib import closing


def seed_database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, created_at TEXT, channel TEXT, product TEXT, amount REAL, refunded INTEGER)"
        )
        if db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]:
            return
        rows = []
        identity = 0
        for day in range(14):
            current = (date(2026, 9, 1) + timedelta(days=day)).isoformat()
            for channel in ["自然流量", "广告投放", "合作渠道"]:
                for product in ["标准版", "专业版"]:
                    for index in range(40):
                        identity += 1
                        probability = 0.06
                        if day >= 7 and channel == "广告投放" and product == "专业版":
                            probability = 0.32
                        rows.append(
                            (
                                identity,
                                current,
                                channel,
                                product,
                                99 if product == "标准版" else 299,
                                int(rng.random() < probability),
                            )
                        )
        db.executemany("INSERT INTO orders VALUES(?,?,?,?,?,?)", rows)
