import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import DomainEvent, EventIdConflict, EventScopeKind
from creative_marketer.identity.application.context import TenantContext


class OutboxWriter(Protocol):
    async def append(self, event: DomainEvent) -> None: ...


class EventTransport(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...


class PublicationState(StrEnum):
    PENDING = "PENDING"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED_TERMINAL = "FAILED_TERMINAL"


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    event: DomainEvent
    lease_owner: UUID
    attempt_count: int


class PublisherStore(Protocol):
    async def claim_ready(
        self, worker_id: UUID, *, batch_size: int, now: datetime, lease_duration: timedelta
    ) -> tuple[ClaimedEvent, ...]: ...

    async def mark_published(self, claimed: ClaimedEvent, *, now: datetime) -> bool: ...
    async def mark_retryable(
        self,
        claimed: ClaimedEvent,
        *,
        next_attempt_at: datetime,
        error_code: str,
        error_digest: str,
        now: datetime,
    ) -> bool: ...
    async def mark_terminal(
        self,
        claimed: ClaimedEvent,
        *,
        error_code: str,
        error_digest: str,
        now: datetime,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class PublicationBatchResult:
    claimed_count: int = 0
    published_count: int = 0
    retry_count: int = 0
    terminal_failure_count: int = 0


class RetryableTransportError(Exception):
    def __init__(self, code: str, digest: str) -> None:
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code) is None:
            raise ValueError("transport error code must be canonical and bounded")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise ValueError("transport error digest must be SHA-256")
        super().__init__(code)
        self.code, self.digest = code, digest


class TerminalTransportError(RetryableTransportError):
    pass


@dataclass(slots=True)
class PublishOutboxBatch:
    store: PublisherStore
    transport: EventTransport
    worker_id: UUID
    max_attempts: int = 8
    lease_duration: timedelta = timedelta(seconds=30)
    base_backoff: timedelta = timedelta(seconds=1)
    max_backoff: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("publisher max_attempts must be positive")
        if self.lease_duration <= timedelta(0):
            raise ValueError("publisher lease duration must be positive")
        if self.base_backoff <= timedelta(0) or self.max_backoff < self.base_backoff:
            raise ValueError("publisher backoff bounds are invalid")

    async def __call__(
        self, *, batch_size: int = 100, now: datetime | None = None
    ) -> PublicationBatchResult:
        at = now or datetime.now(UTC)
        claimed = await self.store.claim_ready(
            self.worker_id, batch_size=batch_size, now=at, lease_duration=self.lease_duration
        )
        published = retries = terminal = 0
        for item in claimed:
            try:
                await self.transport.publish(item.event)
            except TerminalTransportError as error:
                terminal += int(
                    await self.store.mark_terminal(
                        item,
                        error_code=error.code,
                        error_digest=error.digest,
                        now=at,
                    )
                )
            except RetryableTransportError as error:
                if item.attempt_count >= self.max_attempts:
                    terminal += int(
                        await self.store.mark_terminal(
                            item,
                            error_code=error.code,
                            error_digest=error.digest,
                            now=at,
                        )
                    )
                else:
                    multiplier = 2 ** max(0, item.attempt_count - 1)
                    delay = min(self.base_backoff * multiplier, self.max_backoff)
                    retries += int(
                        await self.store.mark_retryable(
                            item,
                            next_attempt_at=at + delay,
                            error_code=error.code,
                            error_digest=error.digest,
                            now=at,
                        )
                    )
            else:
                published += int(await self.store.mark_published(item, now=at))
        return PublicationBatchResult(len(claimed), published, retries, terminal)


class InboxReservation(StrEnum):
    RESERVED = "RESERVED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"


class InboxRepository(Protocol):
    async def reserve(
        self, consumer_name: str, handler_version: str, event: DomainEvent, processed_at: datetime
    ) -> InboxReservation: ...


class ConsumerUnitOfWork(Protocol):
    inbox: InboxRepository

    async def __aenter__(self) -> "ConsumerUnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class ConsumerUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> ConsumerUnitOfWork: ...


EventHandler = Callable[[DomainEvent, ConsumerUnitOfWork], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ConsumerRegistration:
    consumer_name: str
    event_types: frozenset[str]
    handler_version: str
    handler: EventHandler


class ConsumerRegistry:
    def __init__(self, registrations: tuple[ConsumerRegistration, ...]) -> None:
        self._registrations: dict[str, ConsumerRegistration] = {}
        for registration in registrations:
            if not registration.consumer_name or not registration.event_types:
                raise ValueError("consumer registration requires a stable name and event types")
            if registration.consumer_name in self._registrations:
                raise ValueError(f"duplicate consumer: {registration.consumer_name}")
            self._registrations[registration.consumer_name] = registration

    def resolve(self, consumer_name: str, event_type: str) -> ConsumerRegistration:
        registration = self._registrations.get(consumer_name)
        if registration is None or event_type not in registration.event_types:
            raise ValueError(f"consumer {consumer_name} does not support {event_type}")
        return registration


@dataclass(frozen=True, slots=True)
class ConsumerResult:
    processed_count: int = 0
    duplicate_count: int = 0
    consumer_failure_count: int = 0


@dataclass(slots=True)
class ProcessEvent:
    contracts: EventContractRegistry
    consumers: ConsumerRegistry
    uow_factory: ConsumerUnitOfWorkFactory

    async def __call__(self, consumer_name: str, event: DomainEvent) -> ConsumerResult:
        self.contracts.validate_event(event)
        if event.scope_kind is not EventScopeKind.TENANT or event.tenant_id is None:
            raise ValueError("Phase-0 consumer path accepts tenant events only")
        registration = self.consumers.resolve(consumer_name, event.event_type)
        async with self.uow_factory(TenantContext(event.tenant_id)) as uow:
            reservation = await uow.inbox.reserve(
                registration.consumer_name,
                registration.handler_version,
                event,
                datetime.now(UTC),
            )
            if reservation is InboxReservation.ALREADY_PROCESSED:
                return ConsumerResult(duplicate_count=1)
            await registration.handler(event, uow)
            await uow.commit()
            return ConsumerResult(processed_count=1)


def assert_same_event(existing_digest: str, event: DomainEvent) -> None:
    if existing_digest != event.event_digest:
        raise EventIdConflict("same event_id was received with different semantic content")
