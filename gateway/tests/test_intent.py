"""Unit tests for intent parsing — structured and schema validation."""
import pytest
from intent.schema import validate_intent
from errors import IntentError


class TestStructuredIntent:
    def test_valid_intent_passes(self):
        validate_intent({"action": "feishu.bitable.read", "resource": "app:abc/table:xyz"})

    def test_unknown_action_raises(self):
        with pytest.raises(IntentError) as exc_info:
            validate_intent({"action": "evil.action", "resource": "*"})
        assert "INTENT_INVALID" in str(exc_info.value.code)

    def test_missing_action_raises(self):
        with pytest.raises(IntentError):
            validate_intent({"resource": "*"})

    def test_missing_resource_raises(self):
        with pytest.raises(IntentError):
            validate_intent({"action": "web.search"})

    def test_extra_field_raises(self):
        with pytest.raises(IntentError):
            validate_intent({"action": "web.search", "resource": "*", "evil": "injection"})

    def test_resource_too_long_raises(self):
        with pytest.raises(IntentError):
            validate_intent({"action": "web.search", "resource": "a" * 257})

    def test_resource_pattern_invalid(self):
        with pytest.raises(IntentError):
            validate_intent({"action": "web.search", "resource": "ha ha spaces"})

    def test_all_valid_actions(self):
        actions = [
            "feishu.bitable.read", "feishu.contact.read", "feishu.calendar.read",
            "feishu.doc.write", "web.search", "web.fetch", "a2a.invoke", "orchestrate",
        ]
        for action in actions:
            validate_intent({"action": action, "resource": "*"})

    def test_params_optional(self):
        validate_intent({"action": "web.search", "resource": "*", "params": {"q": "hello"}})


class TestStructuredParser:
    def test_parse_valid_body(self):
        from intent.parser_structured import parse_structured
        intent = parse_structured({"intent": {"action": "web.search", "resource": "*"}})
        assert intent["action"] == "web.search"

    def test_missing_intent_key_raises(self):
        from intent.parser_structured import parse_structured
        with pytest.raises(IntentError):
            parse_structured({"not_intent": {}})

    def test_non_dict_intent_raises(self):
        from intent.parser_structured import parse_structured
        with pytest.raises(IntentError):
            parse_structured({"intent": "string_not_dict"})
