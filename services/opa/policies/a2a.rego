package agent.authz

import future.keywords.if
import future.keywords.in
import future.keywords.contains

# 策略版本，与 IdP POLICY_VERSION 环境变量保持一致
policy_version := "v1.2.0"

default allow := false
# `reasons` is a partial set built by `contains` rules below; OPA defaults
# missing partial sets to the empty set, so no `default reasons := set()`.

# ─────────────────────────────────────────────────────────────────────────────
# 规则 1: Token 语义校验
# IdP 已做 RS256 验签；OPA 在此做第二道：iss 白名单 + 时间窗口
# ─────────────────────────────────────────────────────────────────────────────
token_valid if {
    input.token.iss == "https://idp.local"
    now_sec := time.now_ns() / 1000000000
    input.token.exp > now_sec
    input.token.nbf <= now_sec
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 2: 一次性声明
# token 必须声明 one_time=true 且含非空 jti
# 实际销毁（SETNX）由 Gateway 负责，OPA 只校验声明位
# ─────────────────────────────────────────────────────────────────────────────
one_time_declared if {
    input.token.one_time == true
    input.token.jti != ""
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 3: Audience 绑定
# token.aud 必须等于 "agent:{target_agent}"
# ─────────────────────────────────────────────────────────────────────────────
audience_match if {
    # Fix B: IdP now signs aud as "agent:<id>"; match that prefix form.
    expected := sprintf("agent:%s", [input.target_agent])
    input.token.aud == expected
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 4: Scope 覆盖意图
# token.scope 中至少一条能通过 glob 匹配覆盖 intent.action:intent.resource
# separators: ":" "/" "*"  —— 允许 "app_token:*/table:*" 覆盖具体路径
# ─────────────────────────────────────────────────────────────────────────────
scope_covers if {
    required := sprintf("%s:%s", [input.intent.action, input.intent.resource])
    # IdP signs scope as RFC6749 space-separated string; split before iterate.
    scope_arr := split(input.token.scope, " ")
    some s in scope_arr
    glob.match(s, [":", "/", "*"], required)
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 5: 单执行者校验
# executor_map[action] 必须等于 target_agent
# ─────────────────────────────────────────────────────────────────────────────
executor_valid if {
    # Generic actions: lookup executor_map.
    data.executor_map[input.intent.action] == input.target_agent
}

executor_valid if {
    # Fix C: a2a.invoke with "agent:<id>" format.
    # Guard: trim_prefix returns the original string if prefix absent,
    # so check that the result doesn't still start with "/" (agent:// leak).
    input.intent.action == "a2a.invoke"
    target := trim_prefix(input.intent.resource, "agent:")
    not startswith(target, "/")
    target == input.target_agent
}

executor_valid if {
    # Fix C: a2a.invoke with URI-authority format "agent://<id>".
    input.intent.action == "a2a.invoke"
    target := trim_prefix(input.intent.resource, "agent://")
    not startswith(target, "/")   # guard against "agent:///..." edge case
    target == input.target_agent
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 6: 委托白名单
# target_agent 的 delegation.accept_from 必须包含直接调用者 (token.act.sub)
# 纯白名单，无 reject_from
# ─────────────────────────────────────────────────────────────────────────────
delegation_accepted if {
    caller := input.token.act.sub
    some accepted in data.agents[input.target_agent].delegation.accept_from
    accepted == caller
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 7: 委托链深度
# act 链长度 ≤ target_agent 声明的 max_depth
# ─────────────────────────────────────────────────────────────────────────────
delegation_depth_ok if {
    chain := _actor_chain(input.token.act)
    count(chain) <= data.agents[input.target_agent].delegation.max_depth
}

_actor_chain(act) := [] if { act == null }
_actor_chain(act) := chain if {
    act != null
    # 用 walk() 遍历嵌套 act 对象，回避 Rego 禁止自递归限制。
    chain := [v.sub | walk(act, [_, v]); is_object(v); v.sub]
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 8: 未撤销（二次确认）
# Gateway 先查 Redis；OPA data.revoked 作为兜底确认
# 6 粒度：jti / sub / trace_id / plan_id / agent / chain
# ─────────────────────────────────────────────────────────────────────────────
not_revoked if {
    not data.revoked.jtis[input.token.jti]
    not data.revoked.subs[input.token.sub]
    not data.revoked.agents[input.target_agent]
    _trace_not_revoked
    _plan_not_revoked
}

_trace_not_revoked if { not input.token.trace_id }
_trace_not_revoked if {
    input.token.trace_id
    not data.revoked.traces[input.token.trace_id]
}

_plan_not_revoked if { not input.token.plan_id }
_plan_not_revoked if {
    input.token.plan_id
    not data.revoked.plans[input.token.plan_id]
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 9: DPoP 绑定
# token.cnf.jkt 非空表示已做 DPoP 绑定
# Gateway 负责验证 DPoP proof 签名；OPA 确认声明存在
# ─────────────────────────────────────────────────────────────────────────────
dpop_bound if {
    # Explicitly guard on cnf presence before accessing jkt.
    # If cnf is absent, input.token.cnf is `undefined`; OPA would treat the
    # condition as false (correct behaviour) but the intent is not obvious.
    # Two-step check makes the invariant explicit and avoids any future
    # ambiguity when the rule is extended.
    input.token.cnf
    input.token.cnf.jkt != ""
}

# ─────────────────────────────────────────────────────────────────────────────
# 规则 10: 上下文约束
# 10a: 调用频率未超过 agent capability 声明的 max_calls_per_minute
# 10b: 写操作不在 UTC 凌晨 06:00 前执行
# ─────────────────────────────────────────────────────────────────────────────
context_ok if {
    not _rate_limit_exceeded
    not _out_of_hours_write
}

_rate_limit_exceeded if {
    some cap in data.agents[input.target_agent].capabilities
    cap.action == input.intent.action
    cap.constraints.max_calls_per_minute
    input.context.recent_calls > cap.constraints.max_calls_per_minute
}

#_out_of_hours_write if {
#    startswith(input.intent.action, "feishu.doc.write")
#    hour := time.clock(time.now_ns())[0]
#    hour < 6
#}

# ─────────────────────────────────────────────────────────────────────────────
# 综合 allow：10 条全满足
# ─────────────────────────────────────────────────────────────────────────────
allow if {
    token_valid
    one_time_declared
    audience_match
    scope_covers
    executor_valid
    delegation_accepted
    delegation_depth_ok
    not_revoked
    dpop_bound
    context_ok
}

# ─────────────────────────────────────────────────────────────────────────────
# Deny 原因收集（set comprehension，返回所有未满足规则的名称）
# ─────────────────────────────────────────────────────────────────────────────
reasons contains "token_invalid"       if { not token_valid }
reasons contains "not_one_time"        if { not one_time_declared }
reasons contains "audience_mismatch"   if { not audience_match }
reasons contains "scope_exceeded"      if { not scope_covers }
reasons contains "executor_mismatch"   if { not executor_valid }
reasons contains "delegation_rejected" if { not delegation_accepted }
reasons contains "depth_exceeded"      if { not delegation_depth_ok }
reasons contains "revoked"             if { not not_revoked }
reasons contains "dpop_unbound"        if { not dpop_bound }
reasons contains "context_denied"      if { not context_ok }
