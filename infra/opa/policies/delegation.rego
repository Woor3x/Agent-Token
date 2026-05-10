package agent.delegation

import future.keywords.if
import future.keywords.in

# 展开 act 链，返回所有 sub 的数组。
# act 结构：{"sub": "agent_id", "act": <嵌套 act 或 null>}
# 改用 walk() 内置函数遍历，避免 OPA 禁止用户规则自递归的限制。
actor_chain(act) := [] if {
    act == null
}

actor_chain(act) := chain if {
    act != null
    chain := [v.sub | walk(act, [_, v]); is_object(v); v.sub]
}

# 环检测：链中出现重复 sub 则有环
has_cycle(act) if {
    ids := actor_chain(act)
    count(ids) != count({x | some x in ids})
}
