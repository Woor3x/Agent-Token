"""
单元测试：token_exchange/intersect.py
纯函数，无 I/O，无 fixture。
"""
import pytest
from token_exchange.intersect import cap_match, intersect


class TestCapMatch:
    def test_exact_match(self):
        cap = {"action": "feishu.bitable.read", "resource_pattern": "app_token:abc/table:xyz"}
        assert cap_match(cap, "feishu.bitable.read", "app_token:abc/table:xyz")

    def test_wildcard_table(self):
        cap = {"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"}
        assert cap_match(cap, "feishu.bitable.read", "app_token:bascnXXX/table:tblYYY")

    def test_wrong_action(self):
        cap = {"action": "feishu.doc.write", "resource_pattern": "*"}
        assert not cap_match(cap, "feishu.bitable.read", "app_token:x/table:y")

    def test_pattern_no_match(self):
        cap = {"action": "web.fetch", "resource_pattern": "https://*"}
        assert not cap_match(cap, "web.fetch", "http://insecure.com")  # http 不符合 https://*

    def test_web_search_wildcard(self):
        cap = {"action": "web.search", "resource_pattern": "*"}
        assert cap_match(cap, "web.search", "https://google.com/search?q=test")
        assert cap_match(cap, "web.search", "*")

    def test_a2a_invoke_specific_agent(self):
        cap = {"action": "a2a.invoke", "resource_pattern": "agent:data_agent"}
        assert cap_match(cap, "a2a.invoke", "agent:data_agent")
        assert not cap_match(cap, "a2a.invoke", "agent:web_agent")


class TestIntersect:
    def test_full_allow(self):
        callee_caps = [{"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"}]
        user_perms  = [{"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"}]
        requested   = [("feishu.bitable.read", "app_token:bascnXXX/table:tblYYY")]
        result = intersect(callee_caps, user_perms, requested)
        assert result == ["feishu.bitable.read:app_token:bascnXXX/table:tblYYY"]

    def test_callee_missing_action(self):
        """callee 没有该 action → 空"""
        callee_caps = [{"action": "feishu.doc.write", "resource_pattern": "*"}]
        user_perms  = [{"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"}]
        requested   = [("feishu.bitable.read", "app_token:x/table:y")]
        assert intersect(callee_caps, user_perms, requested) == []

    def test_user_missing_permission(self):
        """用户没有该资源权限 → 空"""
        callee_caps = [{"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"}]
        user_perms  = [{"action": "feishu.bitable.read", "resource_pattern": "app_token:alice_only/*"}]
        requested   = [("feishu.bitable.read", "app_token:bascnOTHER/table:tbl")]
        assert intersect(callee_caps, user_perms, requested) == []

    def test_multiple_scopes_partial(self):
        """3 个请求，只有 2 个同时满足"""
        callee_caps = [
            {"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"},
            {"action": "web.search",           "resource_pattern": "*"},
        ]
        user_perms = [
            {"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"},
            # 用户没有 web.search
            {"action": "feishu.doc.write",    "resource_pattern": "doc_token:*"},
        ]
        requested = [
            ("feishu.bitable.read", "app_token:x/table:y"),
            ("web.search", "*"),
            ("feishu.doc.write", "doc_token:abc"),
        ]
        result = intersect(callee_caps, user_perms, requested)
        # callee 没有 feishu.doc.write；user 没有 web.search → 只剩 bitable.read
        assert result == ["feishu.bitable.read:app_token:x/table:y"]

    def test_empty_requested(self):
        assert intersect([], [], []) == []
