import re

TOOL_KEY_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
TOOL_KEY = re.compile(TOOL_KEY_PATTERN)
MAX_TOOL_KEY_LENGTH = 128


def canonical_tool_key(value: str, *, field_name: str = "tool_key") -> str:
    if len(value) > MAX_TOOL_KEY_LENGTH or not TOOL_KEY.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase dot-separated canonical tool key")
    return value


def canonical_tool_keys(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} contains duplicates")
    return tuple(sorted(canonical_tool_key(value, field_name=field_name) for value in values))
