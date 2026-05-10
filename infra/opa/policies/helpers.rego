package agent.helpers

import future.keywords.if

# scope 覆盖检查：granted 是 token 里的 scope 条目，requested 是 "action:resource"
# separators: ":" 用于 action:resource，"/" 用于路径，"*" 用于通配
scope_covers(granted, requested) if {
    glob.match(granted, [":", "/", "*"], requested)
}

# 资源前缀匹配（pattern 末尾 * 代表任意子路径）
resource_prefix_match(pattern, resource) if {
    prefix := trim_suffix(pattern, "*")
    startswith(resource, prefix)
}
