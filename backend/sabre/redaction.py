"""Redaction of Sabre session tokens from anything that leaves process memory."""

import re

REDACTED = "[REDACTED]"

_SECURITY_TOKEN_RE = re.compile(
    r"(<(?:[\w.-]+:)?BinarySecurityToken\b[^>]*>)(.*?)(</(?:[\w.-]+:)?BinarySecurityToken\s*>)",
    re.IGNORECASE | re.DOTALL,
)


def redact_security_tokens(value):
    """Strip BinarySecurityToken values from XML strings and parsed XML dicts.

    Session tokens are live Sabre credentials and must not be archived, logged,
    or returned to API callers.
    """
    if isinstance(value, str):
        return _SECURITY_TOKEN_RE.sub(rf"\g<1>{REDACTED}\g<3>", value)
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower().endswith("binarysecuritytoken")
                  else redact_security_tokens(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_security_tokens(item) for item in value]
    return value
