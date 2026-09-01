import asyncio
import math
import os
import sys
import time
from typing import List, Tuple

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set UTF-8 encoding for stdout on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx
from app.main import create_app
from app.schemas.auth import APIKeyCreateRequest, ClientTier
from app.services.auth import api_key_manager


def calculate_percentile(sorted_samples: List[float], percentile: float) -> float:
    if not sorted_samples:
        return 0.0
    k = (len(sorted_samples) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(sorted_samples[int(k)], 2)
    d0 = sorted_samples[int(f)] * (c - k)
    d1 = sorted_samples[int(c)] * (k - f)
    return round(d0 + d1, 2)


async def run_load_benchmark(total_requests: int = 100, concurrency: int = 10):
    print("=" * 80)
    print(f"  AegisGateway Concurrency Load Benchmark ({total_requests} Requests, Concurrency: {concurrency})")
    print("=" * 80)

    # Generate multi-tier API keys for load test
    key_free = api_key_manager.create_key(APIKeyCreateRequest(name="Bench Free Client", tier=ClientTier.FREE))
    key_pro = api_key_manager.create_key(APIKeyCreateRequest(name="Bench Pro Client", tier=ClientTier.PRO))
    key_ent = api_key_manager.create_key(APIKeyCreateRequest(name="Bench Enterprise Client", tier=ClientTier.ENTERPRISE))
    keys = [key_free.api_key, key_pro.api_key, key_ent.api_key]

    # Create in-memory ASGI client transport for offline benchmark
    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as client:
        semaphore = asyncio.Semaphore(concurrency)
        latencies: List[float] = []
        status_counts = {200: 0, 429: 0, "other": 0}
        cache_hits = 0

        async def worker(request_idx: int) -> Tuple[int, float, str]:
            api_key = keys[request_idx % len(keys)]
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": f"Benchmark request {request_idx} containing email_{request_idx}@test.org"}],
                "stream": False
            }

            async with semaphore:
                start_time = time.perf_counter()
                try:
                    res = await client.post("/v1/chat/completions", json=payload, headers=headers)
                    duration_ms = (time.perf_counter() - start_time) * 1000.0
                    cache_status = res.headers.get("x-aegis-cache", "MISS")
                    return res.status_code, duration_ms, cache_status
                except Exception:
                    duration_ms = (time.perf_counter() - start_time) * 1000.0
                    return 500, duration_ms, "MISS"

        start_total = time.perf_counter()
        tasks = [worker(i) for i in range(total_requests)]
        results = await asyncio.gather(*tasks)
        total_duration_sec = time.perf_counter() - start_total

        for status_code, latency, cache_status in results:
            latencies.append(latency)
            if status_code in status_counts:
                status_counts[status_code] += 1
            else:
                status_counts["other"] += 1

            if cache_status == "HIT":
                cache_hits += 1

        sorted_latencies = sorted(latencies)
        p50 = calculate_percentile(sorted_latencies, 50.0)
        p95 = calculate_percentile(sorted_latencies, 95.0)
        p99 = calculate_percentile(sorted_latencies, 99.0)
        rps = total_requests / total_duration_sec if total_duration_sec > 0 else 0.0
        cache_ratio = (cache_hits / total_requests * 100.0) if total_requests > 0 else 0.0

        print(f"\nBenchmark Performance Results:")
        print("-" * 80)
        print(f" Total Requests Processed : {total_requests}")
        print(f" Concurrency Workers      : {concurrency}")
        print(f" Total Duration           : {total_duration_sec:.2f} seconds")
        print(f" Throughput (RPS)         : {rps:.2f} req/sec")
        print(f" Successful 200 OK        : {status_counts[200]}")
        print(f" Rate Limited 429         : {status_counts[429]}")
        print(f" Cache Hits               : {cache_hits} ({cache_ratio:.1f}%)")
        print("-" * 80)
        print(f" Latency p50              : {p50:.2f} ms")
        print(f" Latency p95              : {p95:.2f} ms")
        print(f" Latency p99              : {p99:.2f} ms")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_load_benchmark(total_requests=100, concurrency=10))
