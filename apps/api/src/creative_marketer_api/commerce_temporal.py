from __future__ import annotations

import asyncio

from creative_marketer.infrastructure.temporal.client import TemporalCommerceWorkflowStarter
from creative_marketer.infrastructure.temporal.worker import connect_client
from creative_marketer.workflow_orchestration.contracts import (
    CommerceActionWorkflowInput,
    CommerceSyncWorkflowInput,
)


class LazyCommerceWorkflowStarter:
    """API adapter that connects only when a user explicitly starts Commerce work."""

    def __init__(self, address: str, namespace: str) -> None:
        self._address = address
        self._namespace = namespace
        self._starter: TemporalCommerceWorkflowStarter | None = None
        self._lock = asyncio.Lock()

    async def _value(self) -> TemporalCommerceWorkflowStarter:
        if self._starter is None:
            async with self._lock:
                if self._starter is None:
                    client = await connect_client(self._address, namespace=self._namespace)
                    self._starter = TemporalCommerceWorkflowStarter(client)
        return self._starter

    async def start_action(self, request: CommerceActionWorkflowInput) -> None:
        await (await self._value()).start_action(request)

    async def signal_action(self, request: CommerceActionWorkflowInput) -> None:
        await (await self._value()).signal_action(request)

    async def start_sync(self, request: CommerceSyncWorkflowInput) -> None:
        await (await self._value()).start_sync(request)
