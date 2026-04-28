"""
API 测试专用 fixtures：启动完整 FastAPI app，注入 fakeredis + 内存 SQLite。

根本问题（两个）
--------------
1. httpx ASGITransport 不触发 FastAPI lifespan，所以所有 init_* 从未被调用。
   修复：在 fixture 里手动调用每个初始化函数，并把 lifespan 里的 init_* 换成
   no-op（防止万一 lifespan 被触发时覆盖我们注入的 mock）。

2. pytest-asyncio asyncio_mode=auto 下，scope="module" 的 async fixture
   与每个 async test function 使用不同 event loop，导致 asyncio 对象
   （fakeredis 内部 Queue、aiosqlite 连接等）抛 "bound to a different event loop"。
   修复：在本模块定义 scope="module" 的 event_loop fixture，让 fixture 和所有
   测试共享同一个 event loop（pytest-asyncio 0.23 已弃用但仍有效的方案）。
"""

import asyncio
import sys
from pathlib import Path

import aiosqlite
import fakeredis.aioredis
import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── 能力 YAML（测试用）────────────────────────────────────────────────────────
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


# ── Fix 2: 为本模块所有 test + fixture 共用一个 module 级 event loop ──────────
@pytest.fixture(scope="module")
def event_loop():
    """
    module 作用域 event loop（pytest-asyncio 0.23 deprecated 方案，仍可用）。
    确保 app_client fixture 和所有 async test 共享同一个 loop，
    避免 asyncio 内部对象（Queue 等）"bound to a different event loop" 错误。
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def app_client(tmp_path_factory):
    """
    启动完整 FastAPI 应用的 AsyncClient。
    - fakeredis 替换真实 Redis（直接注入模块全局）
    - 内存 SQLite 替换文件 DB（直接注入模块全局）
    - 真实 KMS（临时目录）
    - 手动调用所有 init_*（lifespan 不会自动触发）
    """
    import config as config_mod
    import storage.redis as redis_mod
    import storage.sqlite as sqlite_mod
    import kms.store as kms_mod
    import agents.loader as loader_mod
    import agents.sod_check as sod_mod
    import audit.writer as audit_mod
    import main as main_mod

    tmp = tmp_path_factory.mktemp("api_app")

    # ── 写临时 YAML 文件 ───────────────────────────────────────────────────────
    caps_dir = tmp / "capabilities"
    caps_dir.mkdir()
    (caps_dir / "doc_assistant.yaml").write_text(DOC_ASSISTANT_YAML)
    (caps_dir / "data_agent.yaml").write_text(DATA_AGENT_YAML)

    users_dir = tmp / "users"
    users_dir.mkdir()
    (users_dir / "alice.yaml").write_text(ALICE_YAML)

    kms_dir = tmp / "kms"

    # ── 覆盖 settings（pydantic v2 model 默认不 frozen，直接赋值即可）─────────
    config_mod.settings.sqlite_path      = str(tmp / "test.db")
    config_mod.settings.capabilities_dir = str(caps_dir)
    config_mod.settings.users_dir        = str(users_dir)
    config_mod.settings.kms_keys_dir     = str(kms_dir)

    # ── Fix 1a: 直接注入 fakeredis（绕过 init_redis）─────────────────────────
    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_mod._redis = fake_r

    # ── Fix 1b: 直接注入内存 SQLite（绕过 init_db）───────────────────────────
    db_conn = await aiosqlite.connect(":memory:")
    db_conn.row_factory = aiosqlite.Row
    await db_conn.execute("PRAGMA journal_mode=WAL")
    await db_conn.execute("PRAGMA foreign_keys=ON")
    schema = (Path(__file__).parent.parent.parent / "audit" / "schema.sql").read_text()
    await db_conn.executescript(schema)
    await db_conn.commit()
    sqlite_mod._db = db_conn

    # ── Fix 1c: 初始化 KMS、capabilities、SoD（同步，直接调用）──────────────
    kms_mod.init_kms("test-passphrase-1234", str(kms_dir))
    loader_mod.load_capabilities(str(caps_dir))
    sod_mod.run_global_sod_check()

    # ── Fix 1d: 手动初始化 AuditWriter（lifespan 不会触发）──────────────────
    writer = audit_mod.init_audit_writer()
    writer.start()

    # ── 把 lifespan 里的 init_* 换成 no-op（防御性 patch）───────────────────
    # httpx ASGITransport 一般不触发 lifespan，但做防御以免行为随版本变化。
    async def _anoop(*a, **kw): pass
    def   _snoop(*a, **kw): pass

    main_mod.init_db              = _anoop
    main_mod.init_redis           = _anoop
    main_mod.init_kms             = _snoop
    main_mod.load_capabilities    = _snoop
    main_mod.run_global_sod_check = _snoop
    main_mod.load_users           = _anoop
    main_mod.init_audit_writer    = lambda: writer   # 返回已有 writer，防止重复创建

    from main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://idp.test",
    ) as client:
        yield client, fake_r

    # ── 清理 ──────────────────────────────────────────────────────────────────
    await writer.stop()
    if sqlite_mod._db is not None:
        await sqlite_mod._db.close()
        sqlite_mod._db = None
    if redis_mod._redis is not None:
        await redis_mod._redis.aclose()
        redis_mod._redis = None
    kms_mod._kms = None
