"""Pure, bounded provider rejection values; SDK bodies never cross this boundary."""

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SENSITIVE = re.compile(
    r"sk-|bearer|^www\.|^(?:https?|ftp|file|data|mailto):|"
    r"[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    re.IGNORECASE,
)


def diagnostic_token(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _TOKEN.fullmatch(value) or _SENSITIVE.search(value):
        return "REDACTED"
    return value


@dataclass(frozen=True, slots=True)
class ProviderRejection:
    http_status: int
    error_type: str | None = None
    error_code: str | None = None
    param: str | None = None
    request_id: str | None = None
    exception_class: str | None = None

    def __post_init__(self) -> None:
        if type(self.http_status) is not int or not 400 <= self.http_status <= 599:
            raise ValueError("invalid provider rejection status")
        for field in ("error_type", "error_code", "param", "exception_class"):
            object.__setattr__(self, field, diagnostic_token(getattr(self, field)))
        value = self.request_id
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"req_[A-Za-z0-9_-]{8,80}", value)
            or _SENSITIVE.search(value)
        ):
            object.__setattr__(self, "request_id", None)

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "http_status": self.http_status,
            "error_type": self.error_type,
            "error_code": self.error_code,
            "param": self.param,
            "request_id": self.request_id,
            "exception_class": self.exception_class,
        }
