"""
Audit-API 压测脚本

场景:
  1. 写入吞吐  — 并发 POST /audit/events (批量)
  2. 读取吞吐  — 并发 GET  /audit/events
  3. 混合负载  — 写入 70% + 读取 30%
  4. 队列打满  — 极高并发检验 queue_full / 207 响应

用法:
  python3 tests/bench_audit.py [--url http://localhost:8090] [--concurrency 20] [--duration 30]
"""

import argparse
import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import aiohttp

# ── 配置 ──────────────────────────────────────────────────────────────────────

SERVICE_TOKEN = "gateway-service-token"
ADMIN_TOKEN   = "admin-secret-token"

EVENT_TYPES = ["authz_decision", "token_issued", "token_consumed", "revoke_issued", "anomaly", "agent_registered"]
DECISIONS   = ["allow", "deny"]
AGENTS      = ["doc_assistant", "data_agent", "web_agent"]
ACTIONS     = ["feishu.doc.write", "feishu.bitable.read", "web.search"]

# ── 数据生成 ──────────────────────────────────────────────────────────────────

def _make_event() -> dict:
    etype = random.choice(EVENT_TYPES)
    return {
        "event_type": etype,
        "trace_id":   uuid.uuid4().hex,
        "span_id":    uuid.uuid4().hex[:16],
        "plan_id":    f"plan_{uuid.uuid4().hex[:8]}",
        "task_id":    f"task_{uuid.uuid4().hex[:8]}",
        "caller_sub": f"user_{random.randint(1, 100)}",
        "caller_agent": random.choice(AGENTS),
        "callee_agent": random.choice(AGENTS),
        "callee_action": random.choice(ACTIONS),
        "decision":   random.choice(DECISIONS) if etype == "authz_decision" else None,
        "deny_reasons": ["executor_mismatch"] if random.random() < 0.2 else [],
        "latency_ms": random.randint(1, 500),
    }


def _make_batch(size: int = 10) -> dict:
    return {"events": [_make_event() for _ in range(size)]}


# ── 统计 ──────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    name:       str
    ok:         int = 0
    err:        int = 0
    partial:    int = 0          # 207
    latencies:  list = field(default_factory=list)
    start_time: float = field(default_factory=time.monotonic)
    end_time:   float = 0.0

    def record(self, status: int, latency: float):
        self.latencies.append(latency)
        if status == 200:
            self.ok += 1
        elif status == 207:
            self.partial += 1
        else:
            self.err += 1

    def finish(self):
        self.end_time = time.monotonic()

    def report(self):
        total   = self.ok + self.partial + self.err
        elapsed = self.end_time - self.start_time or 1e-9
        rps     = total / elapsed
        lats    = sorted(self.latencies)
        p50  = lats[int(len(lats) * .50)] * 1000 if lats else 0
        p95  = lats[int(len(lats) * .95)] * 1000 if lats else 0
        p99  = lats[int(len(lats) * .99)] * 1000 if lats else 0
        avg  = (sum(lats) / len(lats)) * 1000     if lats else 0

        print(f"\n{'─'*55}")
        print(f"  场景: {self.name}")
        print(f"{'─'*55}")
        print(f"  总请求数  : {total:>8}")
        print(f"  成功 200  : {self.ok:>8}")
        print(f"  部分 207  : {self.partial:>8}")
        print(f"  失败      : {self.err:>8}")
        print(f"  耗时      : {elapsed:>8.2f}s")
        print(f"  RPS       : {rps:>8.1f}")
        print(f"  延迟 avg  : {avg:>8.1f}ms")
        print(f"  延迟 p50  : {p50:>8.1f}ms")
        print(f"  延迟 p95  : {p95:>8.1f}ms")
        print(f"  延迟 p99  : {p99:>8.1f}ms")
        print(f"{'─'*55}")


# ── 压测核心 ──────────────────────────────────────────────────────────────────

async def _worker(
    session: aiohttp.ClientSession,
    stats: Stats,
    task_fn: Callable,
    stop_event: asyncio.Event,
):
    while not stop_event.is_set():
        t0 = time.monotonic()
        try:
            status = await task_fn(session)
            stats.record(status, time.monotonic() - t0)
        except Exception as exc:
            stats.record(0, time.monotonic() - t0)


async def run_bench(
    base_url: str,
    name: str,
    task_fn: Callable,
    concurrency: int,
    duration: int,
) -> Stats:
    stats = Stats(name=name)
    stop  = asyncio.Event()

    connector = aiohttp.TCPConnector(limit=concurrency + 10)
    timeout   = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        workers = [asyncio.create_task(_worker(session, stats, task_fn, stop)) for _ in range(concurrency)]
        await asyncio.sleep(duration)
        stop.set()
        await asyncio.gather(*workers, return_exceptions=True)

    stats.finish()
    return stats


# ── 场景定义 ──────────────────────────────────────────────────────────────────

def write_task(base_url: str, batch_size: int):
    async def _t(session: aiohttp.ClientSession) -> int:
        async with session.post(
            f"{base_url}/audit/events",
            json=_make_batch(batch_size),
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        ) as resp:
            return resp.status
    return _t


def read_task(base_url: str):
    async def _t(session: aiohttp.ClientSession) -> int:
        params = {"limit": "20"}
        if random.random() < 0.3:
            params["decision"] = random.choice(DECISIONS)
        async with session.get(
            f"{base_url}/audit/events",
            params=params,
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        ) as resp:
            return resp.status
    return _t


def mixed_task(base_url: str):
    _write = write_task(base_url, batch_size=5)
    _read  = read_task(base_url)
    async def _t(session: aiohttp.ClientSession) -> int:
        if random.random() < 0.7:
            return await _write(session)
        return await _read(session)
    return _t


# ── 健康检查 + 预热 ────────────────────────────────────────────────────────────

async def warmup(base_url: str):
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(f"{base_url}/healthz", timeout=aiohttp.ClientTimeout(total=5)) as r:
                body = await r.json()
                print(f"[healthz] {body}")
        except Exception as e:
            print(f"[ERROR] audit-api 不可达: {e}")
            raise SystemExit(1)
        # 预热写入
        print("[warmup] 发送 50 条预热事件…")
        for _ in range(5):
            async with s.post(
                f"{base_url}/audit/events",
                json=_make_batch(10),
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            ) as r:
                pass
        print("[warmup] 完成\n")


# ── 主入口 ────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Audit-API 压测")
    parser.add_argument("--url",         default="http://localhost:8090")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration",    type=int, default=20,  help="每个场景秒数")
    parser.add_argument("--batch",       type=int, default=10,  help="写入批量大小")
    parser.add_argument("--scenario",    choices=["write", "read", "mixed", "all"], default="all")
    args = parser.parse_args()

    await warmup(args.url)

    results = []

    if args.scenario in ("write", "all"):
        print(f"[场景1] 写入吞吐  并发={args.concurrency} 批量={args.batch} 时长={args.duration}s")
        r = await run_bench(args.url, f"写入吞吐 (batch={args.batch})",
                            write_task(args.url, args.batch), args.concurrency, args.duration)
        results.append(r)

    if args.scenario in ("read", "all"):
        print(f"[场景2] 读取吞吐  并发={args.concurrency} 时长={args.duration}s")
        r = await run_bench(args.url, "读取吞吐 (GET /events)",
                            read_task(args.url), args.concurrency, args.duration)
        results.append(r)

    if args.scenario in ("mixed", "all"):
        print(f"[场景3] 混合负载  并发={args.concurrency} 时长={args.duration}s  写70%/读30%")
        r = await run_bench(args.url, "混合负载 (写70%/读30%)",
                            mixed_task(args.url), args.concurrency, args.duration)
        results.append(r)

    print("\n\n========== 压测汇总 ==========")
    for r in results:
        r.report()

    # 最终健康检查
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{args.url}/healthz") as resp:
            body = await resp.json()
    print(f"\n[压测后 healthz] {body}")


if __name__ == "__main__":
    asyncio.run(main())
