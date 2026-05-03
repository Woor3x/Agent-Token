"""Shared test fixtures: MOCK_AUTH on by default, Feishu mock base, paths."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SDK = _ROOT / "sdk"
if str(_SDK) not in sys.path:
    sys.path.insert(0, str(_SDK))

os.environ.setdefault("MOCK_AUTH", "true")
os.environ.setdefault("MOCK_AUTH_SECRET", "mock-secret")
os.environ.setdefault("IDP_ISSUER", "https://idp.local")
os.environ.setdefault("IDP_JWKS_URL", "http://idp.local/jwks")
os.environ.setdefault("GATEWAY_URL", "http://gateway.local")
os.environ.setdefault("FEISHU_BASE", "http://feishu-mock.local:9000")
os.environ.setdefault("FEISHU_MOCK", "true")
