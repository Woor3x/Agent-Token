package agent.authz_test

import future.keywords.if
import future.keywords.in

# ── Shared test data ──────────────────────────────────────────────────────────

_now := time.now_ns() / 1000000000

_base_token := {
    "iss":      "https://idp.local",
    "sub":      "doc_assistant",
    "aud":      "agent:data_agent",
    "exp":      _now + 120,
    "nbf":      _now - 1,
    "jti":      "jti-abc-001",
    "one_time": true,
    # scope is RFC6749 space-separated string, matching what the IdP signs.
    "scope":    "feishu.bitable.read:app_token:myapp/table:tbl1",
    "act":      {"sub": "user", "act": null},
    "cnf":      {"jkt": "thumbprint-xyz"},
    "trace_id": "trace-001",
    "plan_id":  "plan-001",
}

_base_input := {
    "token":        _base_token,
    "intent":       {"action": "feishu.bitable.read", "resource": "app_token:myapp/table:tbl1"},
    "target_agent": "data_agent",
    "context":      {"time": _now, "source_ip": "10.0.0.1", "recent_calls": 5, "delegation_depth": 1},
}

# ── Data components ───────────────────────────────────────────────────────────
# Use partial "with data.X as Y" overrides (not "with data as _base_data") to
# avoid OPA's static recursion detector flagging test rules as mutually recursive.

_executor_map := {
    "feishu.bitable.read": "data_agent",
    "feishu.doc.write":    "doc_assistant",
    "web.search":          "web_agent",
}

_agents := {
    "data_agent": {
        "role": "executor",
        "capabilities": [{
            "action":           "feishu.bitable.read",
            "resource_pattern": "app_token:*/table:*",
            "constraints":      {"max_calls_per_minute": 60},
        }],
        "delegation": {"accept_from": ["doc_assistant", "user"], "max_depth": 2},
    },
    "doc_assistant": {
        "role": "orchestrator",
        "capabilities": [{
            "action":           "feishu.doc.write",
            "resource_pattern": "doc_token:*",
            "constraints":      {"max_calls_per_minute": 30},
        }],
        "delegation": {"accept_from": ["user"], "max_depth": 1},
    },
    "web_agent": {
        "role": "executor",
        "capabilities": [{
            "action":           "web.search",
            "resource_pattern": "*",
            "constraints":      {"max_calls_per_minute": 10},
        }],
        "delegation": {"accept_from": ["doc_assistant", "user"], "max_depth": 2},
    },
}

_clean_revoked := {
    "jtis": {}, "subs": {}, "agents": {}, "traces": {}, "plans": {}, "chains": {},
}

# ── test 1: 正常 allow 路径 ────────────────────────────────────────────────────

test_allow_doc_to_data if {
    data.agent.authz.allow
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
}

# ── test 2: executor 不匹配 ────────────────────────────────────────────────────
# web_agent 无法执行 feishu.bitable.read（executor_map 指向 data_agent）

test_deny_executor_mismatch if {
    bad_input := object.union(_base_input, {"target_agent": "web_agent"})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "executor_mismatch" in reasons
}

# ── test 3: 委托来源不在白名单 ─────────────────────────────────────────────────
# 调用方是 "rogue_agent"，不在 data_agent.delegation.accept_from

test_deny_delegation_rejected if {
    bad_token := object.union(_base_token, {"act": {"sub": "rogue_agent", "act": null}})
    bad_input := object.union(_base_input, {"token": bad_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "delegation_rejected" in reasons
}

# ── test 4: 委托链深度超限 ─────────────────────────────────────────────────────
# data_agent.max_depth=2，链 [doc_assistant, orchestrator, user] 长度 3 → 超限

test_deny_depth_exceeded if {
    deep_act  := {"sub": "doc_assistant", "act": {"sub": "orchestrator", "act": {"sub": "user", "act": null}}}
    bad_token := object.union(_base_token, {"act": deep_act})
    bad_input := object.union(_base_input, {"token": bad_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "depth_exceeded" in reasons
}

# ── test 5: jti 已撤销 ────────────────────────────────────────────────────────

test_deny_revoked_jti if {
    revoked := object.union(_clean_revoked, {"jtis": {"jti-abc-001": true}})
    not data.agent.authz.allow
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    reasons := data.agent.authz.reasons
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    "revoked" in reasons
}

# ── test 6: DPoP 未绑定 ───────────────────────────────────────────────────────

test_deny_dpop_unbound if {
    bad_token := object.union(_base_token, {"cnf": {"jkt": ""}})
    bad_input := object.union(_base_input, {"token": bad_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "dpop_unbound" in reasons
}

# ── test 7: token 已过期 ───────────────────────────────────────────────────────
# exp < now_sec → token_invalid

test_deny_expired_token if {
    expired_token := object.union(_base_token, {"exp": _now - 1})
    bad_input     := object.union(_base_input, {"token": expired_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "token_invalid" in reasons
}

# ── test 8: iss 不在白名单 ────────────────────────────────────────────────────

test_deny_wrong_issuer if {
    bad_token := object.union(_base_token, {"iss": "https://evil.example.com"})
    bad_input := object.union(_base_input, {"token": bad_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "token_invalid" in reasons
}

# ── test 9: one_time=false → not_one_time ────────────────────────────────────

test_deny_one_time_false if {
    bad_token := object.union(_base_token, {"one_time": false})
    bad_input := object.union(_base_input, {"token": bad_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "not_one_time" in reasons
}

# ── test 10: jti="" → not_one_time ───────────────────────────────────────────

test_deny_empty_jti if {
    bad_token := object.union(_base_token, {"jti": ""})
    bad_input := object.union(_base_input, {"token": bad_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "not_one_time" in reasons
}

# ── test 11: aud 缺少 "agent:" 前缀 → audience_mismatch ──────────────────────

test_deny_audience_no_prefix if {
    bad_token := object.union(_base_token, {"aud": "data_agent"})
    bad_input := object.union(_base_input, {"token": bad_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "audience_mismatch" in reasons
}

# ── test 12: scope 不覆盖 intent → scope_exceeded ────────────────────────────

test_deny_scope_not_covered if {
    bad_token := object.union(_base_token, {"scope": "feishu.doc.write:doc_token:abc"})
    bad_input := object.union(_base_input, {"token": bad_token})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "scope_exceeded" in reasons
}

# ── test 13: scope 通配符正常覆盖（正常路径）─────────────────────────────────
# "feishu.bitable.read:app_token:*/table:*" 覆盖 "app_token:myapp/table:tbl1"

test_allow_scope_wildcard if {
    wildcard_token := object.union(_base_token, {"scope": "feishu.bitable.read:app_token:*/table:*"})
    wildcard_input := object.union(_base_input, {"token": wildcard_token})
    data.agent.authz.allow
        with input            as wildcard_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
}

# ── test 14: executor_valid — a2a.invoke 特殊路径 ─────────────────────────────
# executor_map 无 a2a.invoke 条目，应通过 trim_prefix("agent:{id}") 路径匹配

test_executor_valid_a2a_invoke if {
    invoke_input := {
        "token": {
            "iss":      "https://idp.local",
            "sub":      "user",
            "aud":      "agent:doc_assistant",
            "exp":      _now + 120,
            "nbf":      _now - 1,
            "jti":      "jti-invoke-001",
            "one_time": true,
            "scope":    "a2a.invoke:agent:doc_assistant",
            "act":      {"sub": "user", "act": null},
            "cnf":      {"jkt": "thumbprint-xyz"},
            "trace_id": "trace-002",
            "plan_id":  "plan-002",
        },
        "intent":       {"action": "a2a.invoke", "resource": "agent:doc_assistant"},
        "target_agent": "doc_assistant",
        "context":      {"time": _now, "source_ip": "10.0.0.1", "recent_calls": 0, "delegation_depth": 0},
    }
    data.agent.authz.executor_valid
        with input            as invoke_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
}

# ── test 15: delegation_depth_ok — 恰好在边界（应通过）────────────────────────
# data_agent.max_depth=2，act 链 [doc_assistant, user] 长度 2 → 通过

test_allow_at_exact_max_depth if {
    act_at_max := {"sub": "doc_assistant", "act": {"sub": "user", "act": null}}
    ok_token   := object.union(_base_token, {"act": act_at_max})
    ok_input   := object.union(_base_input, {"token": ok_token})
    data.agent.authz.allow
        with input            as ok_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
}

# ── test 16: sub 撤销 → revoked ───────────────────────────────────────────────

test_deny_revoked_sub if {
    revoked := object.union(_clean_revoked, {"subs": {"doc_assistant": true}})
    not data.agent.authz.allow
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    reasons := data.agent.authz.reasons
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    "revoked" in reasons
}

# ── test 17: trace_id 撤销 → revoked ─────────────────────────────────────────

test_deny_revoked_trace if {
    revoked := object.union(_clean_revoked, {"traces": {"trace-001": true}})
    not data.agent.authz.allow
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    reasons := data.agent.authz.reasons
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    "revoked" in reasons
}

# ── test 18: plan_id 撤销 → revoked ──────────────────────────────────────────

test_deny_revoked_plan if {
    revoked := object.union(_clean_revoked, {"plans": {"plan-001": true}})
    not data.agent.authz.allow
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    reasons := data.agent.authz.reasons
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    "revoked" in reasons
}

# ── test 19: target_agent 撤销 → revoked ─────────────────────────────────────

test_deny_revoked_agent if {
    revoked := object.union(_clean_revoked, {"agents": {"data_agent": true}})
    not data.agent.authz.allow
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    reasons := data.agent.authz.reasons
        with input            as _base_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as revoked
    "revoked" in reasons
}

# ── test 20: Rate Limit 超限 → context_denied ────────────────────────────────
# data_agent.feishu.bitable.read.max_calls_per_minute=60，recent_calls=61 → 超限

test_deny_rate_limit_exceeded if {
    overloaded_ctx := {"time": _now, "source_ip": "10.0.0.1", "recent_calls": 61, "delegation_depth": 1}
    bad_input      := object.union(_base_input, {"context": overloaded_ctx})
    not data.agent.authz.allow
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "context_denied" in reasons
}

# ── test 21: Rate Limit 恰好在边界（应通过）──────────────────────────────────
# recent_calls=60 = max_calls_per_minute=60，条件是 >，不触发限流

test_allow_at_rate_limit_boundary if {
    boundary_ctx := {"time": _now, "source_ip": "10.0.0.1", "recent_calls": 60, "delegation_depth": 1}
    ok_input     := object.union(_base_input, {"context": boundary_ctx})
    data.agent.authz.allow
        with input            as ok_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
}

# ── test 22: 多条规则同时失败 — reasons 集合收录所有 ─────────────────────────
# wrong iss + empty jkt + executor mismatch → 至少 3 个 reasons

test_deny_multiple_reasons if {
    multi_bad_token := object.union(_base_token, {
        "iss": "https://evil.com",
        "cnf": {"jkt": ""},
    })
    multi_bad_input := object.union(_base_input, {
        "token":        multi_bad_token,
        "target_agent": "web_agent",
    })
    not data.agent.authz.allow
        with input            as multi_bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    reasons := data.agent.authz.reasons
        with input            as multi_bad_input
        with data.executor_map as _executor_map
        with data.agents       as _agents
        with data.revoked      as _clean_revoked
    "token_invalid"     in reasons
    "dpop_unbound"      in reasons
    "executor_mismatch" in reasons
}
