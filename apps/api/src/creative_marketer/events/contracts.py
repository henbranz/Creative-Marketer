import json
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType

from jsonschema import Draft202012Validator

from creative_marketer.events.domain import DomainEvent, EventContractError, event_sha256_v1


class EventContractRegistry:
    """Immutable registry backed by canonical, self-contained JSON Schema files."""

    def __init__(self, contract_directory: Path | None = None) -> None:
        root = contract_directory or Path(str(files("creative_marketer.events") / "schemas"))
        schemas: dict[str, dict[str, object]] = {}
        digests: dict[str, str] = {}
        for path in sorted(root.glob("*.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            event_type = schema.get("x-event-type")
            if not isinstance(event_type, str):
                raise EventContractError(f"missing x-event-type in {path.name}")
            self._reject_refs(schema)
            Draft202012Validator.check_schema(schema)
            if event_type in schemas:
                raise EventContractError(f"duplicate event contract: {event_type}")
            schemas[event_type] = schema
            digests[event_type] = event_sha256_v1(schema)
        self._schemas = MappingProxyType(schemas)
        self._digests = MappingProxyType(digests)

    @staticmethod
    def _reject_refs(value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                raise EventContractError("event schemas must be self-contained and cannot use $ref")
            for child in value.values():
                EventContractRegistry._reject_refs(child)
        elif isinstance(value, list):
            for child in value:
                EventContractRegistry._reject_refs(child)

    def schema_digest(self, event_type: str) -> str:
        try:
            return self._digests[event_type]
        except KeyError as error:
            raise EventContractError(f"unknown event type: {event_type}") from error

    def validate_event(self, event: DomainEvent) -> None:
        expected = self.schema_digest(event.event_type)
        if event.payload_schema_digest != expected:
            raise EventContractError("event payload schema digest does not match local contract")
        errors = sorted(
            Draft202012Validator(
                self._schemas[event.event_type], format_checker=Draft202012Validator.FORMAT_CHECKER
            ).iter_errors(dict(event.payload)),
            key=lambda item: list(item.path),
        )
        if errors:
            raise EventContractError(f"invalid {event.event_type} payload: {errors[0].message}")

    @property
    def event_types(self) -> tuple[str, ...]:
        return tuple(self._schemas)
