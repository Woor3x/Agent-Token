"""Performance benchmark — target: p99 < 50ms @ 500 QPS.

Usage:
    python bench.py --url http://localhost:8080 --qps 500 --duration 30
"""
import argparse
import asyncio
import json
import statistics
import time

import httpx


async def _single_request(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    body: bytes,
) -> float | None:
    start = time.perf_counter()
    try:
        r = await client.post(url, headers=headers, content=body)
        return (time.perf_counter() - start) * 1000
    except Exception:
        return None


async def run_benchmark(base_url: str, qps: int, duration: int, token: str) -> None:
    url = f"{base_url}/a2a/invoke"
    headers = {
        "Authorization": f"DPoP {token}",
        "DPoP": "placeholder",
        "X-Target-Agent": "data_agent",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "intent": {"action": "feishu.bitable.read", "resource": "app:test/table:t1"}
    }).encode()

    interval = 1.0 / qps
    end_time = time.time() + duration
    latencies: list[float] = []
    errors = 0

    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.time() < end_time:
            batch_start = time.time()
            tasks = [_single_request(client, url, headers, body)]
            results = await asyncio.gather(*tasks)
            for r in results:
                if r is None:
                    errors += 1
                else:
                    latencies.append(r)
            elapsed = time.time() - batch_start
            wait = interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)

    if not latencies:
        print("No successful requests.")
        return

    latencies.sort()
    n = len(latencies)
    p50 = latencies[n // 2]
    p95 = latencies[int(n * 0.95)]
    p99 = latencies[int(n * 0.99)]
    mean = statistics.mean(latencies)

    print(f"\n=== Benchmark Results ===")
    print(f"Requests:  {n + errors} ({errors} errors)")
    print(f"Success:   {n}")
    print(f"Mean:      {mean:.1f} ms")
    print(f"p50:       {p50:.1f} ms")
    print(f"p95:       {p95:.1f} ms")
    print(f"p99:       {p99:.1f} ms  (target: < 50ms)")
    print(f"Pass:      {'YES ✓' if p99 < 50 else 'NO ✗'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gateway benchmark")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--qps", type=int, default=500)
    parser.add_argument("--duration", type=int, default=30, help="seconds")
    parser.add_argument("--token", default="", help="DPoP bearer token for requests")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.url, args.qps, args.duration, args.token))


if __name__ == "__main__":
    main()
