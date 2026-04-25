"""
API 测试专用 fixtures：启动完整 FastAPI app，注入 fakeredis + 内存 SQLite。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import fakeredis.aioredis
import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── 能力 YAML 内容（测试用）──────────────────────────────────────────────────
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


@pytest.fixture(scope="module")
async def app_client(tmp_path_factory):
    """
    启动完整 FastAPI 应用的 TestClient。
    - fakeredis 替换真实 Redis
    - 内存 SQLite 替换文件 DB
    - 真实 KMS（临时目录）
    - 临时 capabilities / users 目录
    """
    import storage.redis as redis_mod
    import storage.sqlite as sqlite_mod
    import kms.store as kms_mod
    import agents.loader as loader_mod
    import audit.writer as audit_mod

    tmp = tmp_path_factory.mktemp("app")

    # ── 写临时 YAML 文件 ──
    caps_dir = tmp / "capabilities"
    caps_dir.mkdir()
    (caps_dir / "doc_assistant.yaml").write_text(DOC_ASSISTANT_YAML)
    (caps_dir / "data_agent.yaml").write_text(DATA_AGENT_YAML)

    users_dir = tmp / "users"
    users_dir.mkdir()
    (users_dir / "alice.yaml").write_text(ALICE_YAML)

    kms_dir = tmp / "kms"

    # ── 覆盖环境变量 ──
    os.environ["SQLITE_PATH"] = str(tmp / "test.db")
    os.environ["CAPABILITIES_DIR"] = str(caps_dir)
    os.environ["USERS_DIR"] = str(users_dir)
    os.environ["KMS_KEYS_DIR"] = str(kms_dir)
    os.environ["REDIS_URL"] = "redis://localhost:6379"  # 会被 monkeypatch 覆盖

    # ── Patch：用 fakeredis 替换 init_redis ──
    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)

    original_init_redis = redis_mod.init_redis
    async def fake_init_redis(url):
        redis_mod._redis = fake_r

    redis_mod.init_redis = fake_init_redis

    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://idp.test") as client:
        yield client, fake_r

    redis_mod.init_redis = original_init_redis
    await fake_r.aclose()
