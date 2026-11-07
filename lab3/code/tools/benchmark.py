"""Repeatable local HTTP observation; this is not a human usability study."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import statistics
import threading
import time
import urllib.request

from waitress import create_server

from campus_market.seed import seed_performance
from campus_market.web import create_app


def main():
    """Warm up twenty requests, then retain all one hundred measured outcomes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    options = parser.parse_args()
    app = create_app(options.data_dir)
    auth = app.extensions["market"]["auth"]
    with app.extensions["database"].read() as connection:
        exists = connection.execute("SELECT id FROM users LIMIT 1").fetchone()
    if exists is None:
        auth.register({"username": "benchmark_user", "password": "Benchmark2026"})
    seed_performance(app.extensions["database"], 10000)
    server = create_server(app, host="127.0.0.1", port=0, threads=24)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.effective_port}/api/products?"
    conditions = [
        "category=%E6%95%B0%E7%A0%81%E7%94%B5%E5%AD%90&minimum=1&maximum=50",
        "keyword=%E5%9F%BA%E5%87%86&sort=price_asc&page_size=20",
        "category=%E4%B9%A6%E7%B1%8D%E6%95%99%E6%9D%90&sort=newest&page_size=20",
    ]

    def measure(index):
        started = time.perf_counter()
        error = None
        status = None
        try:
            with urllib.request.urlopen(base + conditions[index % 3], timeout=5) as response:
                status = response.status
                data = json.loads(response.read())
                assert len(data["items"]) <= 20
        except Exception as exc:  # Preserve failed observations in the denominator.
            error = type(exc).__name__
        seconds = time.perf_counter() - started
        return {
            "index": index,
            "condition": index % 3,
            "seconds": round(seconds, 6),
            "status": status,
            "error": error,
            "within_one_second": error is None and status == 200 and seconds <= 1,
        }

    try:
        for index in range(20):
            measure(index)
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(measure, range(100)))
    finally:
        server.close()
    times = sorted(row["seconds"] for row in results)
    summary = {
        "environment": {
            "os": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "python": platform.python_version(),
            "transport": "HTTP loopback",
        },
        "products": 10000,
        "concurrent_clients": 20,
        "warmup": 20,
        "measured": 100,
        "within_one_second": sum(row["within_one_second"] for row in results),
        "failed": sum(row["error"] is not None for row in results),
        "median_seconds": statistics.median(times),
        "p95_seconds": times[94],
        "max_seconds": max(times),
        "samples": results,
        "scope": "本机回环HTTP观测，未限制为4核8GB，也未在同局域网独立客户端验收",
    }
    output = Path(options.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "samples"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
