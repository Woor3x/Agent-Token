# Gateway-测试报告

## 总览

| 服务     | 测试文件        | 测试数 | 通过   | 跳过 |
| -------- | --------------- | ------ | ------ | ---- |
| gateway  | test_unit.py    | 21     | ✅ 21   | 0    |
| gateway  | test_gateway.py | 34     | ✅ 34   | 0    |
| **合计** | **2 个文件**    | **55** | **55** | 0    |

## gateway — `infra/gateway/tests/`

### test_unit.py — 21/21 ✅

| 测试类                   | 用例数 | 覆盖功能                                                     |
| ------------------------ | ------ | ------------------------------------------------------------ |
| `TestDelegationVerifier` | 2      | 无 act 链返回空；单跳委托返回长度 1 chain                    |
| `TestCircuitBreaker`     | 5      | 初始 CLOSED；达到 failure_threshold → OPEN；OPEN 抛 CircuitOpenError；open_duration 后进入 HALF_OPEN；HALF_OPEN 成功 → CLOSED |
| `TestIntentSchema`       | 8      | 合法 intent 通过；未知 action / 缺 action / 缺 resource → IntentError；额外字段 → IntentError；资源超 512 字符 → IntentError；资源含空格 → IntentError；全部合法 action 枚举通过；params 可选 |
| `TestIntentParser`       | 3      | 合法 body 解析；缺 intent 键 → IntentError；intent 非 dict → IntentError |
| `TestOneShot`            | 3      | 第一次 consume 成功；第二次抛异常；并发相同 jti 只有一个获胜 |

---

### test_gateway.py — 34/34 ✅

全链路集成测试，httpx.AsyncClient + ASGITransport，内存 Redis 模拟，OPA 用 `unittest.mock.patch` 按需 allow/deny，上游 Agent 用真实 HTTPServer 独立线程。

#### TestInfra — 4/4 ✅

| 测试用例                          | 验证行为                             |
| --------------------------------- | ------------------------------------ |
| `test_healthz`                    | /healthz 返回 200 + Redis/OPA 状态   |
| `test_metrics`                    | /metrics 返回 prometheus text        |
| `test_admin_reload_valid_token`   | admin token → /admin/reload 返回 200 |
| `test_admin_reload_invalid_token` | 错误 token → 401                     |

#### TestAuthnJWT — 7/7 ✅

| 测试用例                      | 验证行为                      |
| ----------------------------- | ----------------------------- |
| `test_no_auth_header`         | 无 Authorization → 401        |
| `test_bearer_scheme_rejected` | Bearer scheme（非 DPoP）→ 401 |
| `test_expired_token`          | exp 过期 → 401                |
| `test_wrong_issuer`           | iss 不匹配 → 401              |
| `test_missing_one_time_claim` | 缺 one_time 字段 → 401        |
| `test_missing_cnf_jkt`        | 缺 cnf.jkt → 401              |
| `test_unknown_kid`            | kid 不在 JWKS → 401           |

#### TestAuthnRevocation — 4/4 ✅

| 测试用例             | 验证行为                  |
| -------------------- | ------------------------- |
| `test_revoked_jti`   | jti 在撤销列表 → 401      |
| `test_revoked_sub`   | sub 在撤销列表 → 401      |
| `test_revoked_trace` | trace_id 在撤销列表 → 401 |
| `test_revoked_plan`  | plan_id 在撤销列表 → 401  |

#### TestAuthnDPoP — 5/5 ✅

| 测试用例                    | 验证行为                                  |
| --------------------------- | ----------------------------------------- |
| `test_missing_dpop_header`  | 无 DPoP 头 → 401                          |
| `test_dpop_method_mismatch` | DPoP htm ≠ 请求 method → 401              |
| `test_dpop_htu_mismatch`    | DPoP htu ≠ 请求 URL → 401                 |
| `test_dpop_jkt_mismatch`    | DPoP JWK thumbprint ≠ token cnf.jkt → 401 |
| `test_dpop_jti_replay`      | 相同 DPoP jti 第二次请求 → 401            |

#### TestAuthzOPA — 2/2 ✅

| 测试用例                           | 验证行为                        |
| ---------------------------------- | ------------------------------- |
| `test_opa_deny`                    | OPA 返回 deny → 403             |
| `test_opa_unavailable_fail_closed` | OPA 不可达 → 503（fail-closed） |

#### TestAuthzDelegation — 2/2 ✅

| 测试用例                         | 验证行为                   |
| -------------------------------- | -------------------------- |
| `test_delegation_depth_exceeded` | act 链超过 max_depth → 403 |
| `test_delegation_cycle`          | act 链成环 → 403           |

#### TestExecution — 9/9 ✅

| 测试用例                           | 验证行为                           |
| ---------------------------------- | ---------------------------------- |
| `test_one_shot_already_consumed`   | one_time token 二次使用 → 401      |
| `test_unknown_target_agent`        | 目标 agent 不在 registry → 404     |
| `test_upstream_5xx`                | 上游返回 5xx → 透传 502            |
| `test_upstream_timeout`            | 上游超时 → 504                     |
| `test_circuit_breaker_opens`       | 连续失败后熔断器打开 → 503         |
| `test_happy_path`                  | 正常请求全链路通过 → 200           |
| `test_happy_path_response_headers` | 响应头 X-Trace-ID 等正确传递       |
| `test_sensitive_headers_stripped`  | Authorization 等敏感头不转发到上游 |
| `test_invalid_json_body`           | body 非 JSON → 400                 |

#### TestRateLimit — 1/1 ✅

| 测试用例                    | 验证行为         |
| --------------------------- | ---------------- |
| `test_rate_limit_exhausted` | 令牌桶耗尽 → 429 |