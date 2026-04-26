"""Intent JSON Schema definition and validation."""
import jsonschema

INTENT_SCHEMA: dict = {
    "type": "object",
    "required": ["action", "resource"],
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "feishu.bitable.read",
                "feishu.contact.read",
                "feishu.calendar.read",
                "feishu.doc.write",
                "web.search",
                "web.fetch",
                "a2a.invoke",
                "orchestrate",
            ],
        },
        "resource": {
            "type": "string",
            "maxLength": 256,
            "pattern": r"^[a-zA-Z0-9._:/*@\-]+$",
        },
        "params": {"type": "object"},
    },
    "additionalProperties": False,
}

_VALIDATOR = jsonschema.Draft7Validator(INTENT_SCHEMA)


def validate_intent(intent: dict) -> None:
    """Raise IntentError on schema violation."""
    from errors import IntentError
    errors = list(_VALIDATOR.iter_errors(intent))
    if errors:
        msg = "; ".join(e.message for e in errors[:3])
        raise IntentError("INTENT_INVALID", msg)
