from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from typing import Protocol

SafeScalar = str | bool | int | float


class OperationalSpan(Protocol):
    def set_attribute(self, key: str, value: SafeScalar) -> None: ...
    def record_error(self, code: str) -> None: ...


class OperationalTelemetry(Protocol):
    def span(
        self,
        name: str,
        attributes: Mapping[str, SafeScalar] | None = None,
        *,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> AbstractContextManager[OperationalSpan]: ...

    def count(
        self, name: str, value: int = 1, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None: ...

    def duration(
        self, name: str, seconds: float, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None: ...

    def gauge(
        self, name: str, value: int | float, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None: ...


class NullSpan:
    def set_attribute(self, key: str, value: SafeScalar) -> None:
        pass

    def record_error(self, code: str) -> None:
        pass


class NullTelemetry:
    _span = NullSpan()

    def span(
        self,
        name: str,
        attributes: Mapping[str, SafeScalar] | None = None,
        *,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> AbstractContextManager[OperationalSpan]:
        return nullcontext(self._span)

    def count(
        self, name: str, value: int = 1, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None:
        pass

    def duration(
        self, name: str, seconds: float, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None:
        pass

    def gauge(
        self, name: str, value: int | float, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None:
        pass
