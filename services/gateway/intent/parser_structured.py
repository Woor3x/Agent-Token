"""Structured intent parser — validates the JSON body against INTENT_SCHEMA."""
from intent.schema import validate_intent


def parse_structured(body: dict) -> dict:
    """Extract and validate intent from a structured request body.

    Returns the validated intent dict.
    """
    intent = body.get("intent")
    if not isinstance(intent, dict):
        from errors import IntentError
        raise IntentError("INTENT_INVALID", "missing or non-object 'intent' field")
    validate_intent(intent)
    return intent
