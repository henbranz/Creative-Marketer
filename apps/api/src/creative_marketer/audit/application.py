from typing import Protocol

from creative_marketer.audit.domain import AuditRecord


class AuditWriter(Protocol):
    """Append only; mutation and read operations are intentionally absent."""

    async def append(self, record: AuditRecord) -> None: ...


class StandaloneAuditWriter(Protocol):
    """Appends and commits one independent short security transaction."""

    async def append(self, record: AuditRecord) -> None: ...
