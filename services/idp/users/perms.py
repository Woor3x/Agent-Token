from typing import Optional

from storage import sqlite as db


async def load_permissions(user_id: str) -> list[dict]:
    user = await db.get_user(user_id)
    if not user:
        return []
    return user.get("permissions", [])


async def verify_password(user_id: str, password: str) -> bool:
    user = await db.get_user(user_id)
    if not user or not user.get("password_hash"):
        return False
    from passlib.hash import bcrypt
    try:
        return bcrypt.verify(password, user["password_hash"])
    except Exception:
        return False
