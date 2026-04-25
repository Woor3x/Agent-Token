#!/bin/bash
# 快速运行各层测试的入口脚本
# 用法: bash scripts/run_tests.sh [unit|integration|api|all|e2e]
set -euo pipefail

MODE="${1:-all}"
IDP_DIR="$(cd "$(dirname "$0")/../idp" && pwd)"

cd "$IDP_DIR"

install_deps() {
  echo ">>> Installing test dependencies..."
  pip install -q -r requirements-test.txt
}

run_unit() {
  echo ">>> Unit tests (pure functions, no I/O)"
  pytest tests/unit/ -v --tb=short
}

run_integration() {
  echo ">>> Integration tests (fakeredis + in-memory SQLite)"
  pytest tests/integration/ -v --tb=short
}

run_api() {
  echo ">>> API tests (FastAPI TestClient)"
  pytest tests/api/ -v --tb=short
}

run_e2e() {
  echo ">>> E2E tests (requires docker-compose up)"
  cd "$(dirname "$0")/.."
  bash scripts/test_e2e.sh
}

run_coverage() {
  echo ">>> Full test suite with coverage"
  pytest tests/unit/ tests/integration/ \
    --cov=. --cov-report=term-missing \
    --cov-omit="tests/*,*/__init__.py"
}

case "$MODE" in
  unit)        install_deps && run_unit ;;
  integration) install_deps && run_integration ;;
  api)         install_deps && run_api ;;
  e2e)         run_e2e ;;
  coverage)    install_deps && run_coverage ;;
  all)
    install_deps
    run_unit
    run_integration
    echo ""
    echo "✓ All tests passed. Run 'docker-compose up -d' then 'bash scripts/run_tests.sh e2e' for E2E."
    ;;
  *)
    echo "Usage: $0 [unit|integration|api|all|e2e|coverage]"
    exit 1
    ;;
esac
