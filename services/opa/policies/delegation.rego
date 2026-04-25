package agent.delegation

import future.keywords.if

# 递归展开 act 链，返回所有 sub 的数组
# act 结构：{"sub": "agent_id", "act": <嵌套 act 或 null>}
actor_chain(act) := [] if {
    act == null
}

actor_chain(act) := chain if {
    act != null
    sub_chain := actor_chain(act.act)
    chain := array.concat([act.sub], sub_chain)
}

# 环检测：链中出现重复 sub 则有环
has_cycle(act) if {
    ids := actor_chain(act)
    count(ids) != count({x | x := ids[_]})
}
