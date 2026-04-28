package agent.authz_test

import future.keywords.if
import future.keywords.in

# ── shared test data ──────────────────────────────────────────────────────────

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

_base_data := {
    "executor_map": {
        "feishu.bitable.read": "data_agent",
        "feishu.doc.write":    "doc_assistant",
        "web.search":          "web_agent",
    },
    "agents": {
        "data_agent": {
            "role": "executor",
            "capabilities": [
                {
                    "action":           "feishu.bitable.read",
                    "resource_pattern": "app_token:*/table:*",
                    "constraints":      {"max_calls_per_minute": 60},
                },
            ],
            "delegation": {
                "accept_from": ["doc_assistant", "user"],
                "max_depth":   2,
            },
        },
        "doc_assistant": {
            "role": "orchestrator",
            "capabilities": [
                {
                    "action":           "feishu.doc.write",
                    "resource_pattern": "doc_token:*",
                    "constraints":      {"max_calls_per_minute": 30},
                },
            ],
            "delegation": {
                "accept_from": ["user"],
                "max_depth":   1,
            },
        },
        "web_agent": {
            "role": "executor",
            "capabilities": [
                {
                    "action":           "web.search",
                    "resource_pattern": "*",
                    "constraints":      {"max_calls_per_minute": 10},
                },
            ],
            "delegation": {
                "accept_from": ["doc_assistant", "user"],
                "max_depth":   2,
            },
        },
    },
    "revoked": {
        "jtis":   {},
        "subs":   {},
        "agents": {},
        "traces": {},
        "plans":  {},
        "chains": {},
    },
}

_base_input := {
    "token":        _base_token,
    "intent":       {"action": "feishu.bitable.read", "resource": "app_token:myapp/table:tbl1"},
    "target_agent": "data_agent",
    "context":      {"time": _now, "source_ip": "10.0.0.1", "recent_calls": 5, "delegation_depth": 1},
}

# ── test 1: 正常 allow 路径 ────────────────────────────────────────────────────

test_allow_doc_to_data if {
    agent.authz.allow with input as _base_input with data as _base_data
}

# ── test 2: executor 不匹配 ────────────────────────────────────────────────────
# web_agent 无法执行 feishu.bitable.read（executor_map 指向 data_agent）

test_deny_executor_mismatch if {
    bad_input := object.union(_base_input, {"target_agent": "web_agent"})
    not agent.authz.allow with input as bad_input with data as _base_data
    reasons := agent.authz.reasons with input as bad_input with data as _base_data
    "executor_mismatch" in reasons
}

# ── test 3: 委托来源不在白名单 ─────────────────────────────────────────────────
# 调用方是 "rogue_agent"，不在 data_agent.delegation.accept_from

test_deny_delegation_rejected if {
    bad_token := object.union(_base_token, {"act": {"sub": "rogue_agent", "act": null}})
    bad_input  := object.union(_base_input, {"token": bad_token})
    not agent.authz.allow with input as bad_input with data as _base_data
    reasons := agent.authz.reasons with input as bad_input with data as _base_data
    "delegation_rejected" in reasons
}

# ── test 4: 委托链深度超限 ─────────────────────────────────────────────────────
# data_agent.max_depth = 2，链 [doc_assistant, orchestrator, user] 长度 3 → 超限

test_deny_depth_exceeded if {
    deep_act  := {"sub": "doc_assistant", "act": {"sub": "orchestrator", "act": {"sub": "user", "act": null}}}
    bad_token := object.union(_base_token, {"act": deep_act})
    bad_input  := object.union(_base_input, {"token": bad_token})
    not agent.authz.allow with input as bad_input with data as _base_data
    reasons := agent.authz.reasons with input as bad_input with data as _base_data
    "depth_exceeded" in reasons
}

# ── test 5: jti 已撤销 ────────────────────────────────────────────────────────

test_deny_revoked_jti if {
    bad_data := object.union(_base_data, {"revoked": {
        "jtis":   {"jti-abc-001": true},
        "subs":   {},
        "agents": {},
        "traces": {},
        "plans":  {},
        "chains": {},
    }})
    not agent.authz.allow with input as _base_input with data as bad_data
    reasons := agent.authz.reasons with input as _base_input with data as bad_data
    "revoked" in reasons
}

# ── test 6: DPoP 未绑定 ───────────────────────────────────────────────────────

test_deny_dpop_unbound if {
    bad_token := object.union(_base_token, {"cnf": {"jkt": ""}})
    bad_input  := object.union(_base_input, {"token": bad_token})
    not agent.authz.allow with input as bad_input with data as _base_data
    reasons := agent.authz.reasons with input as bad_input with data as _base_data
    "dpop_unbound" in reasons
}

# ── test 7: plan_allow 批量全通过 ─────────────────────────────────────────────

_plan_data := object.union(_base_data, {"users": {
    "alice": {
        "permissions": [
            {"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"},
            {"action": "feishu.doc.write",    "resource_pattern": "doc_token:*"},
        ],
    },
}})

test_plan_allow_batch if {
    plan_input := {
        "orchestrator": {
            "agent_id": "doc_assistant",
            "caps": [
                {"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"},
            ],
        },
        "user": {"sub": "alice"},
        "plan": [
            {"id": "t1", "agent": "data_agent",  "action": "feishu.bitable.read", "resource": "app_token:myapp/table:tbl1"},
        ],
        "context": {"time": _now, "delegation_depth": 1},
    }
    result := agent.authz.plan_allow with input as plan_input with data as _plan_data
    result.overall == "allow"
    count(result.per_task) == 1
    result.per_task[0].allow == true
}

# ── test 8: plan_allow 部分 task 失败 → overall=deny ─────────────────────────

test_plan_deny_partial if {
    plan_input := {
        "orchestrator": {
            "agent_id": "doc_assistant",
            "caps": [
                {"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"},
            ],
        },
        "user": {"sub": "alice"},
        "plan": [
            {"id": "t1", "agent": "data_agent", "action": "feishu.bitable.read", "resource": "app_token:myapp/table:tbl1"},
            # t2: web_agent can't execute feishu.bitable.read → executor_mismatch
            {"id": "t2", "agent": "web_agent",  "action": "feishu.bitable.read", "resource": "app_token:myapp/table:tbl2"},
        ],
        "context": {"time": _now, "delegation_depth": 1},
    }
    result := agent.authz.plan_allow with input as plan_input with data as _plan_data
    result.overall == "deny"
    t2 := [t | some t in result.per_task; t.id == "t2"][0]
    t2.allow == false
    "executor_mismatch" in t2.reasons
}
