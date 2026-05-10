"""
单元测试：token_exchange/intent.py  scope 解析与校验
"""
import pytest
from token_exchange.intent import parse_scope, extract_target_agent
from errors import InvalidRequest


class TestParseScope:
    def test_bitable_read(self):
        action, resource = parse_scope("feishu.bitable.read:app_token:bascnXXX/table:tblYYY")
        assert action == "feishu.bitable.read"
        assert resource == "app_token:bascnXXX/table:tblYYY"

    def test_doc_write(self):
        action, resource = parse_scope("feishu.doc.write:doc_token:doxcnABC")
        assert action == "feishu.doc.write"
        assert resource == "doc_token:doxcnABC"

    def test_web_search_wildcard(self):
        action, resource = parse_scope("web.search:*")
        assert action == "web.search"
        assert resource == "*"

    def test_a2a_invoke(self):
        action, resource = parse_scope("a2a.invoke:agent:data_agent")
        assert action == "a2a.invoke"
        assert resource == "agent:data_agent"

    def test_unknown_action_raises(self):
        with pytest.raises(InvalidRequest, match="Unknown action"):
            parse_scope("feishu.secret.hack:*")

    def test_no_colon_raises(self):
        with pytest.raises(InvalidRequest, match="format"):
            parse_scope("feishu.bitable.read")

    def test_invalid_resource_pattern(self):
        """bitable.read 资源格式必须是 app_token:.../table:..."""
        with pytest.raises(InvalidRequest, match="does not match pattern"):
            parse_scope("feishu.bitable.read:random_garbage")

    def test_contact_read(self):
        action, resource = parse_scope("feishu.contact.read:department:engineering")
        assert action == "feishu.contact.read"
        assert resource == "department:engineering"


class TestExtractTargetAgent:
    def test_agent_prefix(self):
        assert extract_target_agent("agent:data_agent") == "data_agent"

    def test_no_prefix(self):
        assert extract_target_agent("data_agent") == "data_agent"
