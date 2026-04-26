"""agents/common unit tests: capability + auth + scope match."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.common.auth import verify_delegated_token
from agents.common.capability import load_capability
from agents.common.server import _scope_matches, sign_mock_token


def _data_cap_path() -> Path:
    return Path(__file__).resolve().parents[2] / "agents" / "data_agent" / "capability.yaml"


def test_capability_find_hit_and_miss() -> None:
    cap = load_capability(_data_cap_path())
    assert cap.agent_id == "data_agent"
    hit = cap.find("feishu.bitable.read", "app_token:bascn_x/table:tbl_1")
    assert hit is not None
    assert hit.constraints["max_rows_per_call"] == 1000
    assert cap.find("feishu.bitable.read", "completely_wrong") is None
    assert cap.find("web.fetch", "https://x") is None


def test_scope_glob_match() -> None:
    assert _scope_matches("feishu.bitable.read:*", "feishu.bitable.read:app_token:x/table:y")
    assert _scope_matches(
        "feishu.bitable.read:app_token:*/table:*",
        "feishu.bitable.read:app_token:x/table:y",
    )
    assert not _scope_matches("feishu.contact.read:*", "feishu.bitable.read:x")


@pytest.mark.asyncio
async def test_verify_delegated_token_mock_mode() -> None:
    tok = sign_mock_token(
        sub="user:alice",
        actor_sub="doc_assistant",
        aud="agent:data_agent",
        scope=["feishu.bitable.read:app_token:bascn_alice/table:tbl_q1"],
    )
    claims = await verify_delegated_token(
        f"DPoP {tok}",
        expected_issuer="https://idp.local",
        expected_audience="agent:data_agent",
        jwks_url="",
    )
    assert claims.sub == "user:alice"
    assert claims.act == {"sub": "doc_assistant", "act": None}
    assert claims.one_time is True


@pytest.mark.asyncio
async def test_verify_delegated_token_rejects_bad_aud() -> None:
    tok = sign_mock_token(
        sub="user:alice",
        actor_sub="doc_assistant",
        aud="agent:web_agent",  # mismatched
        scope=["web.search:*"],
    )
    from agents.common.auth import AuthnError

    with pytest.raises(AuthnError):
        await verify_delegated_token(
            f"DPoP {tok}",
            expected_issuer="https://idp.local",
            expected_audience="agent:data_agent",
            jwks_url="",
        )
