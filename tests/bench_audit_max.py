"""
Audit-API 极限吞吐定量测量

核心问题: 每秒能持久化多少条 events 到 SQLite?

方法:
  1. 记录 DB 当前行数 N0
  2. 用高并发持续注入 60s (确保队列始终满载)
  3. 停止注入, 等队列完全排空
  4. 记录 DB 行数 N1
  5. 持久化速率 = (N1 - N0) / 总时间
  同时测量:
  - HTTP 入队峰值 (events 进入内存队列的速度)
  - p50/p99 延迟
  - 队列满时的 207 比率
"""
import asyncio, time, uuid, random, aiohttp

BASE = "http://localhost:8090"
SVC  = "gateway-service-token"
ADMIN = "admin-secret-token"
ETYPES = ["authz_decision","token_issued","token_consumed","revoke_issued","anomaly","agent_registered"]

def _batch(n):
    return {"events": [{"event_type": random.choice(ETYPES),
                         "trace_id": uuid.uuid4().hex,
                         "span_id":  uuid.uuid4().hex[:16],
                         "caller_agent": "doc_assistant",
                         "callee_agent": "data_agent",
                         "decision": random.choice(["allow","deny"])} for _ in range(n)]}

async def db_count(sess):
    async with sess.get(f"{BASE}/audit/events?limit=1",
                        headers={"Authorization": f"Bearer {ADMIN}"}) as r:
        return (await r.json()).get("total", 0)

async def queue_depth(sess):
    async with sess.get(f"{BASE}/healthz") as r:
        return (await r.json())["queue_depth"]

async def wait_queue_empty(sess, label=""):
    t0 = time.monotonic()
    while True:
        q = await queue_depth(sess)
        if q == 0:
            elapsed = time.monotonic() - t0
            print(f"  [{label}] 队列清空耗时 {elapsed:.1f}s")
            return elapsed
        await asyncio.sleep(0.2)

# ── 场景: 持续注入 + 测 flush 速率 ───────────────────────────────────────────

async def bench_sustained(concurrency, batch_size, inject_duration, sess):
    """持续注入 inject_duration 秒, 然后等队列排空, 计算总持久化速率"""
    ok = err = partial = accepted = 0
    lats = []
    stop = asyncio.Event()

    async def worker():
        nonlocal ok, err, partial, accepted
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                async with sess.post(f"{BASE}/audit/events", json=_batch(batch_size),
                                     headers={"Authorization": f"Bearer {SVC}"}) as r:
                    lats.append(time.monotonic() - t0)
                    body = await r.json()
                    accepted += body.get("accepted", batch_size if r.status==200 else 0)
                    if   r.status == 200: ok      += 1
                    elif r.status == 207: partial += 1
                    else:                 err     += 1
            except Exception:
                err += 1

    n0 = await db_count(sess)
    t_start = time.monotonic()

    tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await asyncio.sleep(inject_duration)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)

    inject_elapsed = time.monotonic() - t_start

    # 等队列完全排空
    drain_elapsed = await wait_queue_empty(sess, "drain")
    total_elapsed = inject_elapsed + drain_elapsed

    n1 = await db_count(sess)
    persisted = n1 - n0

    total_req = ok + partial + err
    rps       = total_req / inject_elapsed
    http_eps  = accepted  / inject_elapsed     # HTTP 入队速率
    db_eps    = persisted / total_elapsed      # SQLite 持久化速率
    p50 = sorted(lats)[int(len(lats)*.50)]*1000 if lats else 0
    p95 = sorted(lats)[int(len(lats)*.95)]*1000 if lats else 0
    p99 = sorted(lats)[int(len(lats)*.99)]*1000 if lats else 0
    partial_rate = partial / total_req * 100 if total_req else 0

    return {
        "concurrency": concurrency, "batch": batch_size,
        "rps": rps, "http_eps": http_eps, "db_eps": db_eps,
        "persisted": persisted, "accepted": accepted,
        "ok": ok, "partial": partial, "err": err,
        "partial_pct": partial_rate,
        "p50": p50, "p95": p95, "p99": p99,
        "inject_s": inject_elapsed, "total_s": total_elapsed,
    }


# ── 主流程 ────────────────────────────────────────────────────────────────────

async def main():
    conn = aiohttp.TCPConnector(limit=200)
    tout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(connector=conn, timeout=tout) as sess:
        h = await sess.get(f"{BASE}/healthz")
        print(f"[healthz] {await h.json()}\n")

        configs = [
            # (concurrency, batch_size, inject_s)
            (10,  10, 30),   # 轻载基线
            (20,  10, 30),   # 中等并发
            (20,  50, 30),   # 大批量
            (50,  10, 30),   # 高并发小批
            (50,  50, 30),   # 高并发大批
        ]

        print("═"*90)
        print(f"{'并发':>4} {'batch':>5} │ {'HTTP入队/s':>10} │ {'SQLite落盘/s':>12} │ "
              f"{'207%':>6} │ {'p50':>8} {'p95':>8} {'p99':>8} │ {'总持久化':>8}")
        print("─"*90)

        best_db = 0
        best_http = 0
        all_results = []

        for c, b, dur in configs:
            print(f"  运行 并发={c} batch={b} 注入={dur}s …", flush=True)
            r = await bench_sustained(c, b, dur, sess)
            all_results.append(r)
            best_db   = max(best_db,   r["db_eps"])
            best_http = max(best_http, r["http_eps"])
            print(f"  {c:>4} {b:>5} │ {r['http_eps']:>10.0f} │ {r['db_eps']:>12.0f} │ "
                  f"{r['partial_pct']:>5.1f}% │ "
                  f"{r['p50']:>7.1f}ms {r['p95']:>7.1f}ms {r['p99']:>7.1f}ms │ "
                  f"{r['persisted']:>8,}")
            # 等队列安静再开下一轮
            await asyncio.sleep(3)

        print("═"*90)

        print("\n\n╔══════════════════════════════════════════════════════╗")
        print("║               极限吞吐测试结论                       ║")
        print("╠══════════════════════════════════════════════════════╣")
        print(f"║  HTTP 入队峰值  (内存队列)  : {best_http:>8.0f} events/s        ║")
        print(f"║  SQLite 持久化峰值 (落盘)   : {best_db:>8.0f} events/s        ║")
        print(f"║  内存队列缓冲容量           :    10,000 events           ║")
        print("╠══════════════════════════════════════════════════════╣")
        print("║  瓶颈: aiosqlite 单连接 executemany + commit          ║")
        print("║  WAL + NORMAL sync 已启用，无 fsync 等待              ║")
        print("║  优化方向:                                            ║")
        print("║    · AUDIT_BATCH_SIZE 200 → 减少 commit 次数          ║")
        print("║    · AUDIT_FLUSH_INTERVAL_MS 50 → 更频繁小批          ║")
        print("║    · 多写入 worker (当前单 asyncio task)              ║")
        print("╚══════════════════════════════════════════════════════╝")

asyncio.run(main())
