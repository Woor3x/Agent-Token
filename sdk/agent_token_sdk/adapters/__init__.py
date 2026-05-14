"""Framework adapters: LangGraph / LangChain / AutoGen. Each module imports its
optional dependency lazily so that pulling only one does not require the others.

Top-level re-exports keep callers from having to remember the per-framework
module path::

    from agent_token_sdk.adapters import make_a2a_node, make_a2a_tool, A2AAgent
"""
from .autogen import A2AAgent
from .langchain import make_a2a_tool
from .langgraph import make_a2a_node

__all__ = ["make_a2a_node", "make_a2a_tool", "A2AAgent"]
