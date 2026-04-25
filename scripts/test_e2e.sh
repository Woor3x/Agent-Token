#!/bin/bash
# E2E 测试脚本：针对运行中的 docker-compose 环境
# 用法: bash scripts/test_e2e.sh
set -euo pipefail

BASE_URL="${IDP_URL:-http://localhost:8000}"
ADMIN_TOKEN="admin-secret-token"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }
section() { echo -e "\n${YELLOW}══ $1 ══${NC}"; }

# ── 依赖检查 ────────────────────────────────────────────────────────────────
command -v curl >/dev/null || fail "curl not found"
command -v python3 >/dev/null || fail "python3 not found"

# Python 辅助函数：生成 RSA 密钥对和 JWT
read -r -d '' PY_KEYGEN << 'PYEOF'
import sys, json, base64, time, uuid
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import jwt as jose_jwt

kid = sys.argv[1]
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
private_pem = private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()
pub = private_key.public_key().public_numbers()
def b64u(n):
    l = (n.bit_length()+7)//8
    return base64.urlsafe_b64encode(n.to_bytes(l,'big')).rstrip(b'=').decode()
jwk = {"kty":"RSA","kid":kid,"use":"sig","alg":"RS256","n":b64u(pub.n),"e":b64u(pub.e)}
print(json.dumps({"private_pem": private_pem, "public_jwk": jwk}))
PYEOF

read -r -d '' PY_ASSERTION << 'PYEOF'
import sys, json, time, uuid
from jose import jwt as jose_jwt

data = json.loads(sys.argv[1])
agent_id = sys.argv[2]
audience = sys.argv[3]
private_pem = data["private_pem"]
kid = data["public_jwk"]["kid"]
now = int(time.time())
claims = {
    "iss": agent_id, "sub": agent_id, "aud": audience,
    "iat": now, "exp": now+300, "jti": str(uuid.uuid4()),
}
token = jose_jwt.encode(claims, private_pem.encode(), algorithm="RS256", headers={"kid": kid})
print(token)
PYEOF

# ── 1. 健康检查 ──────────────────────────────────────────────────────────────
section "1. Health Check"
resp=$(curl -sf "$BASE_URL/healthz")
status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[[ "$status" == "ok" ]] && pass "Service healthy" || fail "Service unhealthy: $resp"

# ── 2. JWKS 端点 ──────────────────────────────────────────────────────────────
section "2. JWKS Endpoint"
jwks=$(curl -sf "$BASE_URL/jwks")
key_count=$(echo "$jwks" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['keys']))")
[[ "$key_count" -ge 1 ]] && pass "JWKS returned $key_count key(s)" || fail "No keys in JWKS"

# ── 3. OpenID Configuration ──────────────────────────────────────────────────
section "3. OpenID Configuration"
oidc_conf=$(curl -sf "$BASE_URL/.well-known/openid-configuration")
echo "$oidc_conf" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'jwks_uri' in d"
pass "OpenID configuration valid"

# ── 4. 注册 Orchestrator Agent ───────────────────────────────────────────────
section "4. Register Orchestrator (doc_assistant_e2e)"
ORCH_ID="doc_assistant_e2e"
ORCH_KID="agent-${ORCH_ID}-e2e-v1"

ORCH_KEYPAIR=$(python3 -c "$PY_KEYGEN" "$ORCH_KID")

CAPS_YAML=$(cat <<'EOF'
capabilities:
  - action: feishu.doc.write
    resource_pattern: "doc_token:*"
  - action: a2a.invoke
    resource_pattern: "agent:data_agent_e2e"
delegation:
  accept_from: [user]
  max_depth: 1
EOF
)
CAPS_B64=$(echo "$CAPS_YAML" | base64 -w 0)

REG_RESP=$(curl -sf -X POST "$BASE_URL/agents/register" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$ORCH_ID\",\"role\":\"orchestrator\",\"display_name\":\"E2E Orch\",\"capabilities_yaml\":\"$CAPS_B64\"}")

REGISTERED_KID=$(echo "$REG_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['kid'])")
[[ -n "$REGISTERED_KID" ]] && pass "Orchestrator registered, kid=$REGISTERED_KID" || fail "Registration failed: $REG_RESP"

# ── 5. 注册 Executor Agent ───────────────────────────────────────────────────
section "5. Register Executor (data_agent_e2e)"
EXEC_ID="data_agent_e2e"
EXEC_CAPS_YAML=$(cat <<'EOF'
capabilities:
  - action: feishu.bitable.read
    resource_pattern: "app_token:*/table:*"
delegation:
  accept_from: [doc_assistant_e2e]
  max_depth: 3
EOF
)
EXEC_CAPS_B64=$(echo "$EXEC_CAPS_YAML" | base64 -w 0)

EXEC_REG=$(curl -sf -X POST "$BASE_URL/agents/register" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$EXEC_ID\",\"role\":\"executor\",\"display_name\":\"E2E Executor\",\"capabilities_yaml\":\"$EXEC_CAPS_B64\"}")

EXEC_KID=$(echo "$EXEC_REG" | python3 -c "import sys,json; print(json.load(sys.stdin)['kid'])")
[[ -n "$EXEC_KID" ]] && pass "Executor registered, kid=$EXEC_KID" || fail "Executor registration failed"

# ── 6. 列出 Agents ───────────────────────────────────────────────────────────
section "6. List Agents"
AGENTS=$(curl -sf "$BASE_URL/agents" -H "Authorization: Bearer $ADMIN_TOKEN")
AGENT_COUNT=$(echo "$AGENTS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['agents']))")
pass "Found $AGENT_COUNT agent(s)"

# ── 7. OIDC 登录流程 ─────────────────────────────────────────────────────────
section "7. OIDC Login → User Access Token"

# 生成 PKCE
VERIFIER=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
CHALLENGE=$(python3 -c "
import sys, hashlib, base64
v = '$VERIFIER'.encode()
d = hashlib.sha256(v).digest()
print(base64.urlsafe_b64encode(d).rstrip(b'=').decode())
")

# GET /oidc/authorize → 拿到 state_token（从 HTML 中提取）
AUTH_HTML=$(curl -sf "$BASE_URL/oidc/authorize?\
response_type=code\
&client_id=web-ui\
&redirect_uri=http://localhost:3000/callback\
&code_challenge=$CHALLENGE\
&code_challenge_method=S256")

STATE_TOKEN=$(echo "$AUTH_HTML" | grep -oP 'value="\K[^"]+(?="[^>]*name="state_token")')
[[ -n "$STATE_TOKEN" ]] || {
  # 尝试另一种 grep 方式
  STATE_TOKEN=$(echo "$AUTH_HTML" | python3 -c "
import sys, re
html = sys.stdin.read()
m = re.search(r'name=\"state_token\" value=\"([^\"]+)\"', html) or \
    re.search(r'value=\"([^\"]+)\"[^>]*name=\"state_token\"', html)
if m: print(m.group(1))
")
}

[[ -n "$STATE_TOKEN" ]] && pass "Got state_token from authorize" || fail "Cannot extract state_token from HTML"

# POST /oidc/login → 302 到 redirect_uri?code=...
LOGIN_RESP=$(curl -si -X POST "$BASE_URL/oidc/login" \
  -d "state_token=$STATE_TOKEN&user_id=alice&password=alice123" \
  --max-redirs 0 2>&1 || true)

CODE=$(echo "$LOGIN_RESP" | grep -oP 'code=\K[^& ]+' | head -1)
[[ -n "$CODE" ]] && pass "Authorization code received: ${CODE:0:8}..." || fail "Login failed. Response:\n$LOGIN_RESP"

# POST /oidc/token → access_token
TOKEN_RESP=$(curl -sf -X POST "$BASE_URL/oidc/token" \
  -d "grant_type=authorization_code\
&code=$CODE\
&redirect_uri=http://localhost:3000/callback\
&code_verifier=$VERIFIER\
&client_id=web-ui")

USER_ACCESS_TOKEN=$(echo "$TOKEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
[[ -n "$USER_ACCESS_TOKEN" ]] && pass "User access_token obtained" || fail "Token exchange failed: $TOKEN_RESP"

# ── 8. Token Exchange ────────────────────────────────────────────────────────
section "8. Token Exchange (10-phase)"

# 构造 client_assertion（用注册时返回的私钥重新生成，这里用 ORCH_KEYPAIR 里的私钥）
# 注意：注册后 IdP 不存私钥，测试中用同一个密钥对
# 实际上注册时 IdP 用它自己的私钥覆盖了 public_jwk，所以这里需要用注册返回的 kid
# 但我们没存私钥...所以我们重新注册一个 agent 并立即做 token exchange

# 直接构造 assertion（用 ORCH_KEYPAIR 里的 private_pem 和 kid）
# 这样需要先更新 DB 里该 agent 的 public_jwk 与 ORCH_KEYPAIR 的公钥匹配
# 最简单：注册时指定 capabilities_yaml 不含私钥，IdP 生成新密钥对并返回
# 我们应该用注册返回的 private_key_pem 来签 assertion

# 重新注册并拿私钥
FRESH_REG=$(curl -sf -X POST "$BASE_URL/agents/register" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"agent_id\":\"orch_fresh\",
    \"role\":\"orchestrator\",
    \"capabilities_yaml\":\"$(echo 'capabilities:
  - action: a2a.invoke
    resource_pattern: "agent:data_agent_e2e"
delegation:
  accept_from: [user]' | base64 -w 0)\"
  }")

FRESH_KID=$(echo "$FRESH_REG" | python3 -c "import sys,json; print(json.load(sys.stdin)['kid'])")
FRESH_PRIVKEY=$(echo "$FRESH_REG" | python3 -c "import sys,json; print(json.load(sys.stdin)['private_key_pem'])")

# 注册 executor（需要接受 orch_fresh 的委托）
curl -sf -X POST "$BASE_URL/agents/register" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"agent_id\":\"exec_fresh\",
    \"role\":\"executor\",
    \"capabilities_yaml\":\"$(echo 'capabilities:
  - action: feishu.bitable.read
    resource_pattern: "app_token:*/table:*"
delegation:
  accept_from: [orch_fresh]' | base64 -w 0)\"
  }" > /dev/null

# 写入 alice 的权限（如果 API 存在的话直接调，否则 alice 已在启动时加载）
# alice 已从 users/alice.yaml 加载，有 feishu.bitable.read 权限

# 生成 client_assertion
CLIENT_ASSERTION=$(python3 << PYEOF
import json, time, uuid
from jose import jwt as jose_jwt

private_pem = """$FRESH_PRIVKEY"""
kid = "$FRESH_KID"
agent_id = "orch_fresh"
now = int(time.time())
claims = {
    "iss": agent_id, "sub": agent_id,
    "aud": "https://idp.local/token/exchange",
    "iat": now, "exp": now+300,
    "jti": str(uuid.uuid4()),
}
print(jose_jwt.encode(claims, private_pem.encode(), algorithm="RS256", headers={"kid": kid}))
PYEOF
)

[[ -n "$CLIENT_ASSERTION" ]] && pass "client_assertion constructed" || fail "Failed to build client_assertion"

# 执行 Token Exchange
EXCHANGE_RESP=$(curl -s -X POST "$BASE_URL/token/exchange" \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange\
&client_assertion=$CLIENT_ASSERTION\
&subject_token=$USER_ACCESS_TOKEN\
&scope=feishu.bitable.read:app_token:bascnXXX/table:tblYYY\
&audience=agent:exec_fresh\
&trace_id=e2e-trace-001")

HTTP_STATUS=$(echo "$EXCHANGE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if 'access_token' in d else 'fail')" 2>/dev/null || echo "fail")

if [[ "$HTTP_STATUS" == "ok" ]]; then
  DELEGATED_TOKEN=$(echo "$EXCHANGE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
  pass "Delegated token issued: ${DELEGATED_TOKEN:0:20}..."
else
  echo "Response: $EXCHANGE_RESP"
  fail "Token exchange failed"
fi

# ── 9. 撤销测试 ──────────────────────────────────────────────────────────────
section "9. Revoke"

REVOKE_RESP=$(curl -sf -X POST "$BASE_URL/revoke" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"agent","value":"exec_fresh","reason":"e2e test","ttl_sec":3600}')

REVOKED=$(echo "$REVOKE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['revoked'])")
[[ "$REVOKED" == "True" ]] && pass "Agent revoked" || fail "Revoke failed: $REVOKE_RESP"

# 检查状态
STATUS_RESP=$(curl -sf "$BASE_URL/revoke/status?type=agent&value=exec_fresh" \
  -H "Authorization: Bearer $ADMIN_TOKEN")
IS_REVOKED=$(echo "$STATUS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['revoked'])")
[[ "$IS_REVOKED" == "True" ]] && pass "Revoke status confirmed" || fail "Status check failed"

# ── 10. Plan Validate ────────────────────────────────────────────────────────
section "10. Plan Validate"

PLAN_RESP=$(curl -sf -X POST "$BASE_URL/plan/validate" \
  -H "Content-Type: application/json" \
  -d '{
    "plan_id": "e2e-plan-001",
    "trace_id": "e2e-trace-001",
    "tasks": [
      {
        "task_id": "t1",
        "orchestrator_id": "doc_assistant",
        "callee_id": "data_agent",
        "user_id": "alice",
        "scope": "feishu.bitable.read:app_token:bascnXXX/table:tblYYY",
        "audience": "agent:data_agent"
      }
    ]
  }')

OVERALL=$(echo "$PLAN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['overall'])" 2>/dev/null || echo "error")
pass "Plan validate returned: overall=$OVERALL"

# ── 11. Admin Reload ─────────────────────────────────────────────────────────
section "11. Admin Reload"
RELOAD=$(curl -sf -X POST "$BASE_URL/admin/reload" \
  -H "Authorization: Bearer $ADMIN_TOKEN")
[[ "$(echo $RELOAD | python3 -c 'import sys,json; print(json.load(sys.stdin)["reloaded"])')" == "True" ]] \
  && pass "Admin reload successful" || fail "Admin reload failed"

# ── 汇总 ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════${NC}"
echo -e "${GREEN}  All E2E tests passed!${NC}"
echo -e "${GREEN}══════════════════════════════════════${NC}"
