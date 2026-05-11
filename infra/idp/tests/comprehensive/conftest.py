"""
综合测试 conftest.py
====================
职责：
  1. Rich 富文本 pytest 报告插件（无 rich 时优雅降级为普通输出）
  2. 完整 FastAPI app_client（含 alice 用户真实加载 — 区别于 tests/api/conftest.py）
  3. registered_agents fixture（通过 API 注册 doc_assistant / data_agent）

目录继承：tests/conftest.py 中的 fake_redis / mem_db / tmp_kms 不在此处重复定义，
本目录的测试通过 pytest 根 conftest 自动继承这些 fixtures。
"""
import asyncio
import sys
from pathlib import Path

import aiosqlite
import fakeredis.aioredis
import pytest
from httpx import AsyncClient, ASGITransport

# 确保 services/idp/ 在 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Rich 富文本插件（可选）──────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    _con = Console(highlight=False, markup=True, legacy_windows=False)
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False

_SEEN_SECTIONS: set[str] = set()
# nodeid → first line of the test's docstring (populated in pytest_collection_finish)
_ITEM_DOCS: dict[str, str] = {}

ADMIN_HDR = {"Authorization": "Bearer test-admin-token"}


def pytest_collection_finish(session: pytest.Session) -> None:
    """Collect the first line of every test's docstring for later display."""
    for item in session.items:
        doc = getattr(item.function, "__doc__", None) or ""
        first_line = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        if first_line:
            _ITEM_DOCS[item.nodeid] = first_line


def pytest_sessionstart(session: pytest.Session) -> None:
    if not _HAS_RICH:
        return
    try:
        _con.print()
        _con.print(
            Panel(
                "[bold white]IDP Comprehensive Test Suite[/bold white]\n"
                "[dim]OIDC · DPoP · Token Exchange · Error Schema · Agent Lifecycle[/dim]",
                title="[bold blue]>> Starting[/bold blue]",
                border_style="blue",
                expand=False,
            )
        )
    except Exception:  # pragma: no cover — encoding failures on non-UTF-8 terminals
        pass


def pytest_runtest_logreport(report: pytest.TestReport) -> None:  # type: ignore[name-defined]
    """每个 call 阶段: 打印分区标题 + 彩色 PASS/FAIL 行 + docstring + 失败详情。"""
    if not _HAS_RICH or report.when != "call":
        return

    parts = report.nodeid.split("::")
    cls = parts[-2] if len(parts) >= 3 else ""
    name = parts[-1]
    doc = _ITEM_DOCS.get(report.nodeid, "")

    # Duration — available as report.duration (float seconds)
    dur = getattr(report, "duration", None)
    dur_str = f"[dim]{dur * 1000:.0f}ms[/dim]" if dur is not None else ""

    try:
        if cls and cls not in _SEEN_SECTIONS:
            _SEEN_SECTIONS.add(cls)
            _con.print()
            _con.print(
                Panel(Text(cls, style="bold cyan"), expand=False, border_style="cyan")
            )

        if report.passed:
            _con.print(f"  [green]PASS[/green] [dim]{name}[/dim]  {dur_str}")
            if doc:
                _con.print(f"       [italic dim]{doc}[/italic dim]")
        elif report.failed:
            _con.print(f"  [bold red]FAIL[/bold red] [red]{name}[/red]  {dur_str}")
            if doc:
                _con.print(f"       [italic dim]{doc}[/italic dim]")
            # Extract the short error message from longrepr
            err_text = ""
            if report.longrepr:
                raw = str(report.longrepr)
                # Last non-empty line usually contains "AssertionError: ..." or "E  ..."
                lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                # Find lines starting with "E " (pytest error lines)
                e_lines = [ln[2:].strip() for ln in lines if ln.startswith("E ")]
                if e_lines:
                    err_text = e_lines[-1][:200]
                elif lines:
                    err_text = lines[-1][:200]
            if err_text:
                _con.print(
                    Panel(
                        f"[red]{err_text}[/red]",
                        title="[bold red]Error[/bold red]",
                        border_style="red",
                        expand=False,
                        padding=(0, 1),
                    )
                )
        elif report.skipped:
            _con.print(f"  [yellow]SKIP[/yellow] [dim]{name}[/dim]  {dur_str}")
            if doc:
                _con.print(f"       [italic dim]{doc}[/italic dim]")
    except Exception:  # pragma: no cover
        pass


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _HAS_RICH:
        return
    ok = exitstatus == 0
    color = "green" if ok else "red"
    label = "ALL TESTS PASSED" if ok else "TESTS FAILED"
    try:
        _con.print()
        _con.print(
            Panel(
                f"[bold {color}]{label}[/bold {color}]",
                border_style=color,
                expand=False,
            )
        )
    except Exception:  # pragma: no cover
        pass


# ── YAML 测试数据 ─────────────────────────────────────────────────────────────
DOC_ASSISTANT_YAML = """
agent_id: doc_assistant
display_name: Doc Assistant
role: orchestrator
capabilities:
  - action: feishu.doc.write
    resource_pattern: "doc_token:*"
  - action: a2a.invoke
    resource_pattern: "agent:data_agent"
delegation:
  accept_from: [user]
  max_depth: 1
underlying_credentials: []
"""

DATA_AGENT_YAML = """
agent_id: data_agent
display_name: Data Agent
role: executor
capabilities:
  - action: feishu.bitable.read
    resource_pattern: "app_token:*/table:*"
  - action: feishu.contact.read
    resource_pattern: "department:*"
delegation:
  accept_from: [doc_assistant]
  max_depth: 3
underlying_credentials: []
"""

ALICE_YAML = """
user_id: alice
password: "alice123"
permissions:
  - action: feishu.bitable.read
    resource_pattern: "app_token:*/table:*"
  - action: feishu.doc.write
    resource_pattern: "doc_token:*"
"""


# ── Module-scope event loop ────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def event_loop():
    """所有 module-scope async fixtures 共享同一个 event loop，
    避免 asyncio 内部对象（Queue 等）'bound to a different event loop' 错误。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


# ── 完整 FastAPI app_client ───────────────────────────────────────────────────
@pytest.fixture(scope="module")
async def app_client(tmp_path_factory):
    """
    启动完整 FastAPI 应用：
    - fakeredis  替换真实 Redis
    - 内存 SQLite 替换文件 DB
    - 真实 KMS（临时目录）
    - alice 用户**真实写入 DB**（不同于 tests/api/conftest 的 no-op patch）
    - capability YAML 加载进内存

    yield (AsyncClient, FakeRedis)
    """
    import config as config_mod
    import storage.redis as redis_mod
    import storage.sqlite as sqlite_mod
    import kms.store as kms_mod
    import agents.loader as loader_mod
    import agents.sod_check as sod_mod
    import audit.writer as audit_mod
    import users.loader as users_loader_mod
    import main as main_mod

    tmp = tmp_path_factory.mktemp("comp_app")

    caps_dir = tmp / "capabilities"
    caps_dir.mkdir()
    (caps_dir / "doc_assistant.yaml").write_text(DOC_ASSISTANT_YAML)
    (caps_dir / "data_agent.yaml").write_text(DATA_AGENT_YAML)

    users_dir = tmp / "users"
    users_dir.mkdir()
    (users_dir / "alice.yaml").write_text(ALICE_YAML)

    kms_dir = tmp / "kms"

    # Override settings (pydantic v2 — mutable by default)
    config_mod.settings.sqlite_path = str(tmp / "test.db")
    config_mod.settings.capabilities_dir = str(caps_dir)
    config_mod.settings.users_dir = str(users_dir)
    config_mod.settings.kms_keys_dir = str(kms_dir)

    # Inject fakeredis
    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_mod._redis = fake_r

    # In-memory SQLite with schema
    db_conn = await aiosqlite.connect(":memory:")
    db_conn.row_factory = aiosqlite.Row
    await db_conn.execute("PRAGMA journal_mode=WAL")
    await db_conn.execute("PRAGMA foreign_keys=ON")
    schema = (Path(__file__).parent.parent.parent / "audit" / "schema.sql").read_text()
    await db_conn.executescript(schema)
    await db_conn.commit()
    sqlite_mod._db = db_conn

    # Init infrastructure
    kms_mod.init_kms("test-passphrase-1234", str(kms_dir))
    loader_mod.load_capabilities(str(caps_dir))
    sod_mod.run_global_sod_check()

    # ★ Actually load alice (verify_password must work for OIDC login tests)
    await users_loader_mod.load_users(str(users_dir))

    # Audit writer
    writer = audit_mod.init_audit_writer()
    writer.start()

    # Patch lifespan no-ops so ASGI transport doesn't re-run init functions
    async def _anoop(*a, **kw):
        pass

    def _snoop(*a, **kw):
        pass

    main_mod.init_db = _anoop
    main_mod.init_redis = _anoop
    main_mod.init_kms = _snoop
    main_mod.load_capabilities = _snoop
    main_mod.run_global_sod_check = _snoop
    main_mod.load_users = _anoop
    main_mod.init_audit_writer = lambda: writer

    from main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://idp.test",
    ) as client:
        yield client, fake_r

    # Cleanup
    await writer.stop()
    if sqlite_mod._db is not None:
        await sqlite_mod._db.close()
        sqlite_mod._db = None
    if redis_mod._redis is not None:
        await redis_mod._redis.aclose()
        redis_mod._redis = None
    kms_mod._kms = None


# ── registered_agents: doc_assistant + data_agent 注册到 DB ──────────────────
@pytest.fixture(scope="module")
async def registered_agents(app_client):
    """
    通过 POST /agents/register 将 doc_assistant 和 data_agent 写入 SQLite。
    能力定义 (capabilities) 已由 app_client 的 load_capabilities 加载进内存。
    token exchange 同时需要内存能力表 + DB 公钥，两者缺一不可。

    返回:
        {
            "doc_assistant": {"kid": ..., "private_key_pem": ..., "public_jwk": ...},
            "data_agent":    {"kid": ..., "private_key_pem": ..., "public_jwk": ...},
        }
    """
    client, _ = app_client
    result = {}

    for agent_id, role in [("doc_assistant", "orchestrator"), ("data_agent", "executor")]:
        resp = await client.post(
            "/agents/register",
            json={"agent_id": agent_id, "role": role, "display_name": agent_id},
            headers=ADMIN_HDR,
        )
        assert resp.status_code == 200, f"Register {agent_id} failed: {resp.text}"
        data = resp.json()
        result[agent_id] = {
            "kid": data["kid"],
            "private_key_pem": data["private_key_pem"].encode(),
            "public_jwk": data["public_jwk"],
        }

    return result
