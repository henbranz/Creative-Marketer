from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from .domain import ProviderPublicationState, PublicationDraft


class FakeBehavior(StrEnum):
    SUCCESS = "success"
    DELAYED = "delayed"
    FAILURE = "failure"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True, slots=True)
class ProviderSubmission:
    state: ProviderPublicationState
    external_operation_id: str
    external_post_id: str | None = None
    permalink: str | None = None
    published_at: datetime | None = None
    failure_code: str | None = None


class SocialPublishingProvider(Protocol):
    provider_key: str
    connector_version: str

    async def submit_publication(self, draft: PublicationDraft) -> ProviderSubmission: ...
    async def get_publication_status(self, operation_id: str) -> ProviderSubmission: ...
    async def cancel_scheduled_publication(self, operation_id: str) -> ProviderSubmission: ...


class FakeSocialProvider:
    """Deterministic, in-memory, zero-network social provider used only in development/tests."""

    provider_key = "fake"
    connector_version = "fake-social-v1"

    def __init__(self, behavior: FakeBehavior = FakeBehavior.SUCCESS) -> None:
        self.behavior = behavior
        self._operations: dict[str, ProviderSubmission] = {}
        self.submit_count = 0

    @staticmethod
    def _ids(draft: PublicationDraft) -> tuple[str, str]:
        digest = hashlib.sha256(str(draft.id).encode()).hexdigest()[:24]
        return f"fake-op-{digest}", f"fake-post-{digest}"

    async def submit_publication(self, draft: PublicationDraft) -> ProviderSubmission:
        operation_id, post_id = self._ids(draft)
        if operation_id in self._operations:
            return self._operations[operation_id]
        self.submit_count += 1
        now = datetime.now(UTC)
        if self.behavior is FakeBehavior.FAILURE:
            result = ProviderSubmission(
                ProviderPublicationState.FAILED, operation_id, failure_code="FAKE_PROVIDER_FAILURE"
            )
        elif self.behavior is FakeBehavior.OUTCOME_UNKNOWN:
            result = ProviderSubmission(ProviderPublicationState.OUTCOME_UNKNOWN, operation_id)
        elif self.behavior is FakeBehavior.DELAYED:
            result = ProviderSubmission(ProviderPublicationState.SUBMITTED, operation_id, post_id)
        else:
            result = ProviderSubmission(
                ProviderPublicationState.PUBLISHED,
                operation_id,
                post_id,
                f"https://social.invalid/{draft.platform.value}/post/{post_id}",
                now,
            )
        self._operations[operation_id] = result
        return result

    async def get_publication_status(self, operation_id: str) -> ProviderSubmission:
        # The fake provider is deliberately restart-safe: the durable operation reference is
        # sufficient to reconstruct its deterministic external state after process memory is lost.
        result = self._operations.get(operation_id)
        if result is None:
            post_id = operation_id.replace("fake-op-", "fake-post-", 1)
            result = ProviderSubmission(
                ProviderPublicationState.PUBLISHED,
                operation_id,
                post_id,
                f"https://social.invalid/fake/post/{post_id}",
                datetime.now(UTC),
            )
            self._operations[operation_id] = result
        if result.state in {
            ProviderPublicationState.SUBMITTED,
            ProviderPublicationState.OUTCOME_UNKNOWN,
        }:
            post_id = result.external_post_id or operation_id.replace("fake-op-", "fake-post-")
            result = ProviderSubmission(
                ProviderPublicationState.PUBLISHED,
                operation_id,
                post_id,
                f"https://social.invalid/fake/post/{post_id}",
                datetime.now(UTC),
            )
            self._operations[operation_id] = result
        return result

    async def cancel_scheduled_publication(self, operation_id: str) -> ProviderSubmission:
        result = ProviderSubmission(ProviderPublicationState.CANCELLED, operation_id)
        self._operations[operation_id] = result
        return result
