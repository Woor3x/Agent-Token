import ipaddress
from datetime import datetime, timezone

from config import WRITE_ACTIONS, MAX_USER_CALLS_PER_MIN, settings
from errors import ContextDenied, RateLimited
from storage.redis import incr_with_window


def _ip_in_allowed_nets(client_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for net_str in settings.source_nets_list:
        try:
            if addr in ipaddress.ip_network(net_str, strict=False):
                return True
        except ValueError:
            continue
    return False


def _has_write_action(scope: list[str]) -> bool:
    return any(s.split(":")[0] in WRITE_ACTIONS for s in scope)


async def apply_context(scope: list[str], ctx: dict) -> list[str]:
    if not scope:
        return scope

    now = datetime.now(timezone.utc)

    if _has_write_action(scope) and now.hour < 6:
        raise ContextDenied("Write actions not permitted outside business hours (UTC 06:00+)")

    user_id = ctx.get("user", "")
    if user_id:
        user_key = f"rate:user:{user_id}:calls"
        count, allowed = await incr_with_window(user_key, 60, MAX_USER_CALLS_PER_MIN)
        if not allowed:
            raise RateLimited(f"User {user_id} rate limit exceeded ({count}/{MAX_USER_CALLS_PER_MIN} per min)")

    client_ip = ctx.get("client_ip", "127.0.0.1")
    if not _ip_in_allowed_nets(client_ip):
        raise ContextDenied(f"Client IP {client_ip} not in allowed source nets")

    return scope
