"""AutoGen adapter (方案-SDK §8.3).

``autogen`` (pyautogen) is an optional dependency. When absent we expose a
plain ``A2AAgent`` dataclass-style class with ``a2a_invoke`` — still enough for
tests to drive it. When present, we mix into ``ConversableAgent``.
"""
from __future__ import annotations

from typing import Any, Callable

from ..client import AgentClient

try:  # pragma: no cover — only when pyautogen is installed
    from autogen import ConversableAgent as _Base
except Exception:  # pragma: no cover
    class _Base:  # type: ignore[no-redef]
        def __init__(self, name: str, **_: Any) -> None:
            self.name = name


class A2AAgent(_Base):
    def __init__(
        self,
        agent_id: str,
        target: str,
        client: AgentClient,
        ctx_provider: Callable[[], dict[str, Any]],
        **kw: Any,
    ) -> None:
        super().__init__(name=agent_id, **kw)
        self._client = client
        self._target = target
        self._ctx = ctx_provider

    async def a2a_invoke(self, intent: dict[str, Any], purpose: str = "") -> dict[str, Any]:
        ctx = self._ctx()
        return await self._client.invoke(
            target=self._target,
            intent=intent,
            purpose=purpose,
            **ctx,
        )
