from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.measurement.application import MeasurementService


@dataclass(slots=True)
class MeasurementConversionSink:
    """Adapter into the measurement application boundary; never touches its tables."""

    measurement: MeasurementService

    async def record_paid_order(
        self,
        context: ExecutionContext,
        *,
        commerce_order_id: UUID,
        external_order_id: str,
        attribution_code: str,
        amount: Decimal,
        currency: str,
        occurred_at: datetime,
    ) -> None:
        del commerce_order_id
        await self.measurement.ingest_commerce_conversion(
            context,
            external_id=external_order_id,
            amount=amount,
            currency=currency,
            observed_at=occurred_at,
            public_code=attribution_code,
        )
