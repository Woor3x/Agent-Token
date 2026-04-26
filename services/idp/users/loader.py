from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from passlib.hash import bcrypt
from pydantic import BaseModel

from storage import sqlite as db


class UserPermissionEntry(BaseModel):
    action: str
    resource_pattern: str


class UserRecord(BaseModel):
    user_id: str
    password: Optional[str] = None
    permissions: list[UserPermissionEntry] = []


async def load_users(users_dir: str) -> None:
    path = Path(users_dir)
    if not path.exists():
        return

    for yaml_file in path.glob("*.yaml"):
        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        user = UserRecord.model_validate(data)
        password_hash: Optional[str] = None
        if user.password:
            password_hash = bcrypt.hash(user.password)

        permissions = [p.model_dump() for p in user.permissions]
        now = datetime.now(timezone.utc).isoformat()
        await db.upsert_user({
            "user_id": user.user_id,
            "password_hash": password_hash,
            "permissions": permissions,
            "updated_at": now,
        })
