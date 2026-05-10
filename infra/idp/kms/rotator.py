import json
import time
from datetime import datetime, timezone

from storage import sqlite as db
from kms.store import get_kms
from storage.redis import publish


async def rotate_idp_key() -> dict:
    kms = get_kms()
    new_kid, old_kid = kms.rotate()
    now = datetime.now(timezone.utc).isoformat()

    all_keys = kms.get_all_public_keys()
    key_map = {k["kid"]: k for k in all_keys}

    if new_kid in key_map:
        await db.upsert_jwks_rotation(new_kid, "active", json.dumps(key_map[new_kid]), now)

    if old_kid and old_kid in key_map:
        await db.upsert_jwks_rotation(old_kid, "previous", json.dumps(key_map[old_kid]), now)

    await publish("policy_reload", json.dumps({"event": "key_rotated", "new_kid": new_kid, "old_kid": old_kid}))

    return {"new_kid": new_kid, "old_kid": old_kid, "rotated_at": now}
