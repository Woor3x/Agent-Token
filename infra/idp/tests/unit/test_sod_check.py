"""
单元测试：agents/sod_check.py
"""
import pytest
from agents.loader import AgentCapability, CapabilityEntry, DelegationConfig
from agents.sod_check import check_sod
from errors import InvalidRequest


def _make_agent(agent_id: str, role: str, actions: list[str]) -> AgentCapability:
    caps = [CapabilityEntry(action=a, resource_pattern="*") for a in actions]
    return AgentCapability(agent_id=agent_id, role=role, capabilities=caps)


class TestSoD:
    def test_no_overlap_passes(self):
        """orchestrator 和 executor actions 完全不重叠 → 通过"""
        orch = [_make_agent("doc_assistant", "orchestrator",
                            ["feishu.doc.write", "a2a.invoke"])]
        exec_ = [_make_agent("data_agent", "executor",
                             ["feishu.bitable.read", "feishu.contact.read"])]
        check_sod(orch, exec_)  # 不应该抛异常

    def test_overlap_raises(self):
        """orchestrator 和 executor 共享 feishu.doc.write → SoD 违规"""
        orch  = [_make_agent("bad_orch", "orchestrator", ["feishu.doc.write"])]
        exec_ = [_make_agent("bad_exec", "executor",    ["feishu.doc.write"])]
        with pytest.raises(InvalidRequest, match="SoD violation"):
            check_sod(orch, exec_)

    def test_empty_lists_pass(self):
        check_sod([], [])

    def test_multiple_orchestrators_no_overlap(self):
        orch = [
            _make_agent("o1", "orchestrator", ["a2a.invoke"]),
            _make_agent("o2", "orchestrator", ["feishu.doc.write"]),
        ]
        exec_ = [_make_agent("e1", "executor", ["feishu.bitable.read"])]
        check_sod(orch, exec_)

    def test_overlap_reports_which_actions(self):
        orch  = [_make_agent("o", "orchestrator", ["web.search", "web.fetch"])]
        exec_ = [_make_agent("e", "executor",    ["web.search"])]
        with pytest.raises(InvalidRequest) as exc_info:
            check_sod(orch, exec_)
        assert "web.search" in str(exc_info.value)
