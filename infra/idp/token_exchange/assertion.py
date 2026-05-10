import json
import time
from dataclasses import dataclass

from jose import jwt as jose_jwt, JWTError

from config import settings, ASSERTION_JTI_TTL_SEC
from errors import (
    AssertionReplay, AssertionTooLong, AgentRevoked,
    InvalidClient, InvalidRequest, SubIssMismatch,
)
from storage import sqlite as db
from storage.redis import setnx_with_ttl, sismember


@dataclass
class AgentIdentity:
    agent_id: str
    kid: str
    jti: str
    role: str


async def verify_client_assertion(client_assertion: str) -> AgentIdentity:
    try:
        header = jose_jwt.get_unverified_header(client_assertion)
    except JWTError as exc:
        raise InvalidClient(f"Cannot parse client_assertion header: {exc}")

    kid = header.get("kid")
    if not kid:
        raise InvalidClient("client_assertion missing kid in header")

    agent_row = await db.get_agent_by_kid(kid)
    if not agent_row:
        raise InvalidClient(f"No agent found with kid={kid}")

    if agent_row["status"] != "active":
        raise InvalidClient(f"Agent {agent_row['agent_id']} is not active (status={agent_row['status']})")

    public_jwk = json.loads(agent_row["public_jwk"])
    alg = agent_row.get("alg", "RS256")
    agent_id = agent_row["agent_id"]
    audience = f"{settings.idp_issuer}/token/exchange"

    try:
        claims = jose_jwt.decode(
            client_assertion,
            public_jwk,
            algorithms=[alg],
            audience=audience,
            issuer=agent_id,
            options={"leeway": 30},
        )
    except JWTError as exc:
        raise InvalidClient(f"client_assertion verification failed: {exc}")

    exp = claims.get("exp", 0)
    iat = claims.get("iat", 0)
    if (exp - iat) > 600:
        raise AssertionTooLong("client_assertion lifetime must not exceed 600 seconds")

    sub = claims.get("sub")
    iss = claims.get("iss")
    if sub != iss:
        raise SubIssMismatch("client_assertion sub must equal iss")

    jti = claims.get("jti")
    if not jti:
        raise InvalidRequest("client_assertion missing jti")

    redis_key = f"assertion:jti:{jti}"
    ok = await setnx_with_ttl(redis_key, "1", ASSERTION_JTI_TTL_SEC)
    if not ok:
        raise AssertionReplay(f"client_assertion jti already used: {jti}")

    is_revoked = await sismember("revoked:agents", agent_id)
    if is_revoked:
        raise AgentRevoked(f"Agent {agent_id} is revoked")

    return AgentIdentity(
        agent_id=agent_id,
        kid=kid,
        jti=jti,
        role=agent_row["role"],
    )
