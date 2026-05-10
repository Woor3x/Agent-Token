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
                "feishu.bitable.read_all",
                "feishu.contact.read",
                "feishu.calendar.read",
                "feishu.docx.read",
                "feishu.doc.write",
                "web.search",
                "web.fetch",
                "a2a.invoke",
                "orchestrate",
            ],
        },
        # Resource pattern needs to fit two shapes:
        #   - capability resources e.g. ``app_token:foo/table:bar``
        #   - URLs for ``web.fetch`` e.g. ``https://x.y/z?a=b&c=d``
        # Allow URL-safe RFC3986 chars (unreserved + sub-delims + %, ?, =, &, #).
        # Spaces and control chars still rejected.
        "resource": {
            "type": "string",
            "maxLength": 512,
            "pattern": r"^[A-Za-z0-9._:/*@\-?=&#%~+]+$",
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
