"""Password policy shared by every service.

Deliberately simple and explicit: the rules are stated in one place so the API, the
console hint text, and any future SSO bridge cannot drift apart.
"""
import re

MIN_LENGTH = 12

# Rejected outright — these show up constantly in seeded/demo environments.
_COMMON = {
    "password", "password1", "passw0rd", "changeme", "changeme123",
    "admin", "admin123", "letmein", "welcome", "qwerty", "123456",
    "iloveyou", "monkey", "dragon", "football", "abc123",
}


def validate_password(password: str, *, email: str | None = None) -> list[str]:
    """Return a list of human-readable failures; empty list means the password is acceptable."""
    problems: list[str] = []

    if len(password) < MIN_LENGTH:
        problems.append(f"must be at least {MIN_LENGTH} characters")
    if not re.search(r"[a-z]", password):
        problems.append("must include a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("must include an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("must include a digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("must include a symbol")

    lowered = password.lower()
    if lowered in _COMMON:
        problems.append("is a commonly used password")
    # Catch 'ChangeMe123!' style variants built around a banned root.
    elif any(c in lowered for c in ("password", "changeme", "qwerty", "letmein")):
        problems.append("contains a commonly used word")

    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 3 and local in lowered:
            problems.append("must not contain your email address")

    return problems


def describe_policy() -> str:
    return (
        f"At least {MIN_LENGTH} characters, with lowercase, uppercase, a digit and a symbol. "
        "Must not be a common password or contain your email address."
    )
