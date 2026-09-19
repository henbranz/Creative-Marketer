from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.tool_governance.domain import RiskLevel

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
ISO_CURRENCY = re.compile(r"[A-Z]{3}")
INVENTORY_RULE_VERSION = "commerce-inventory-rules-v1"
ORDER_RULE_VERSION = "commerce-order-rules-v1"
DEFAULT_LOW_STOCK_THRESHOLD = 5
PII_FIELD_NAMES = frozenset(
    {
        "customer_name",
        "email",
        "phone",
        "address",
        "ip_address",
        "payment_card",
        "card_number",
        "payment_token",
        "tracking_number",
    }
)


class CommerceError(Exception):
    code = "COMMERCE_ERROR"


class CommerceNotFound(CommerceError):
    code = "COMMERCE_NOT_FOUND"


class CommercePermissionDenied(CommerceError):
    code = "COMMERCE_PERMISSION_DENIED"


class CommerceConflict(CommerceError):
    code = "COMMERCE_CONFLICT"


class InvalidCommerceProposal(CommerceError):
    code = "INVALID_COMMERCE_PROPOSAL"


class ConnectionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISCONNECTED = "DISCONNECTED"
    REQUIRES_REAUTH = "REQUIRES_REAUTH"
    ARCHIVED = "ARCHIVED"


class MappingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class PaymentState(StrEnum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    PAID = "PAID"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FulfillmentState(StrEnum):
    UNFULFILLED = "UNFULFILLED"
    PARTIAL = "PARTIAL"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"
    RETURNED = "RETURNED"


class SyncType(StrEnum):
    CATALOG = "CATALOG"
    INVENTORY = "INVENTORY"
    ORDERS = "ORDERS"
    ALL = "ALL"


class SyncStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"


class DurableSyncStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ActionType(StrEnum):
    INVENTORY_ADJUSTMENT = "INVENTORY_ADJUSTMENT"
    REFUND = "REFUND"


class DecisionKind(StrEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"


class ActionJobStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    CANCELLED = "CANCELLED"


class OperationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class InventoryIndicator(StrEnum):
    OUT_OF_STOCK = "OUT_OF_STOCK"
    LOW_STOCK = "LOW_STOCK"
    IN_STOCK = "IN_STOCK"
    UNAVAILABLE = "UNAVAILABLE"


class OrderExceptionKind(StrEnum):
    PAID_BUT_UNFULFILLED = "PAID_BUT_UNFULFILLED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PARTIAL_REFUND = "PARTIAL_REFUND"


def _currency(value: str) -> str:
    if not ISO_CURRENCY.fullmatch(value):
        raise ValueError("currency must be an ISO 4217 uppercase code")
    return value


def _money(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 0:
        raise ValueError("money must be finite and non-negative")
    return value


def canonical_money_text(value: Decimal) -> str:
    """Stable money text across PostgreSQL numeric scale normalization."""

    normalized = _money(value).normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("commerce timestamps must be timezone-aware")
    return value


def _digest(value: Mapping[str, object]) -> str:
    return canonical_digest(value)


@dataclass(frozen=True, slots=True)
class CommerceConnection:
    tenant_id: UUID
    provider: str
    display_name: str
    external_store_id: str
    safe_store_identifier: str
    capabilities: tuple[str, ...]
    id: UUID = field(default_factory=uuid4)
    status: ConnectionStatus = ConnectionStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not all(
            (
                self.provider.strip(),
                self.display_name.strip(),
                self.external_store_id.strip(),
                self.safe_store_identifier.strip(),
            )
        ):
            raise ValueError("only an explicitly identified fake commerce provider is enabled")
        if any(name in self.__dataclass_fields__ for name in ("access_token", "client_secret")):
            raise ValueError("credentials cannot be domain connection fields")


@dataclass(frozen=True, slots=True)
class CommerceProductObservation:
    tenant_id: UUID
    connection_id: UUID
    external_product_id: str
    title: str
    status: str
    source_digest: str
    id: UUID = field(default_factory=uuid4)
    provider: str = "fake"
    provider_version: str = "fake-commerce-v1"
    schema_version: int = 1
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.provider.strip() or not DIGEST.fullmatch(self.source_digest):
            raise ValueError("catalog observation source is invalid")
        _aware(self.captured_at)


@dataclass(frozen=True, slots=True)
class CommerceVariantObservation:
    tenant_id: UUID
    connection_id: UUID
    external_product_id: str
    external_variant_id: str
    sku: str | None
    price: Decimal
    currency: str
    inventory_tracked: bool | None
    source_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        _money(self.price)
        _currency(self.currency)
        _aware(self.captured_at)
        if not DIGEST.fullmatch(self.source_digest):
            raise ValueError("variant source digest is invalid")


@dataclass(frozen=True, slots=True)
class ProductCommerceMapping:
    tenant_id: UUID
    product_id: UUID
    connection_id: UUID
    external_product_id: str
    created_by: UUID
    external_variant_id: str | None = None
    id: UUID = field(default_factory=uuid4)
    status: MappingStatus = MappingStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.external_product_id.strip():
            raise ValueError("an explicit external product identifier is required")


@dataclass(frozen=True, slots=True)
class InventoryObservation:
    tenant_id: UUID
    connection_id: UUID
    external_product_id: str
    external_variant_id: str
    available_quantity: int | None
    captured_at: datetime
    source_digest: str
    sku: str | None = None
    location_id: str | None = None
    committed_quantity: int | None = None
    on_hand_quantity: int | None = None
    id: UUID = field(default_factory=uuid4)
    provider: str = "fake"
    provider_version: str = "fake-commerce-v1"
    schema_version: int = 1

    def __post_init__(self) -> None:
        _aware(self.captured_at)
        if not self.provider.strip() or not DIGEST.fullmatch(self.source_digest):
            raise ValueError("inventory observation source is invalid")

    def indicator(self, threshold: int = DEFAULT_LOW_STOCK_THRESHOLD) -> InventoryIndicator:
        if threshold < 1:
            raise ValueError("low stock threshold must be positive")
        if self.available_quantity is None:
            return InventoryIndicator.UNAVAILABLE
        if self.available_quantity <= 0:
            return InventoryIndicator.OUT_OF_STOCK
        return (
            InventoryIndicator.LOW_STOCK
            if self.available_quantity <= threshold
            else InventoryIndicator.IN_STOCK
        )


@dataclass(frozen=True, slots=True)
class OrderLineObservation:
    external_line_id: str
    external_product_id: str
    external_variant_id: str | None
    sku: str | None
    quantity: int
    unit_price: Decimal
    currency: str
    mapped_product_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order line quantity must be positive")
        _money(self.unit_price)
        _currency(self.currency)


@dataclass(frozen=True, slots=True)
class OrderObservation:
    tenant_id: UUID
    connection_id: UUID
    external_order_id: str
    order_reference: str
    currency: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    shipping_total: Decimal
    total: Decimal
    financial_status: PaymentState
    fulfillment_status: FulfillmentState
    created_at_external: datetime
    updated_at_external: datetime
    captured_at: datetime
    lines: tuple[OrderLineObservation, ...]
    source_digest: str
    attribution_code: str | None = None
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1

    def __post_init__(self) -> None:
        _currency(self.currency)
        for amount in (
            self.subtotal,
            self.discount_total,
            self.tax_total,
            self.shipping_total,
            self.total,
        ):
            _money(amount)
        for value in (self.created_at_external, self.updated_at_external, self.captured_at):
            _aware(value)
        if any(line.currency != self.currency for line in self.lines):
            raise ValueError("order line currency mismatch")
        if not DIGEST.fullmatch(self.source_digest):
            raise ValueError("order source digest is invalid")

    @property
    def refundable_amount(self) -> Decimal:
        return self.total if self.financial_status is PaymentState.PAID else Decimal(0)


@dataclass(frozen=True, slots=True)
class PaymentObservation:
    tenant_id: UUID
    connection_id: UUID
    external_order_id: str
    state: PaymentState
    amount: Decimal
    currency: str
    captured_at: datetime
    source_digest: str
    provider_payment_reference: str | None = None
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1

    def __post_init__(self) -> None:
        _money(self.amount)
        _currency(self.currency)
        _aware(self.captured_at)
        if not DIGEST.fullmatch(self.source_digest):
            raise ValueError("payment source digest is invalid")


@dataclass(frozen=True, slots=True)
class FulfillmentObservation:
    tenant_id: UUID
    connection_id: UUID
    external_order_id: str
    state: FulfillmentState
    captured_at: datetime
    source_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1

    def __post_init__(self) -> None:
        _aware(self.captured_at)
        if not DIGEST.fullmatch(self.source_digest):
            raise ValueError("fulfillment source digest is invalid")


@dataclass(frozen=True, slots=True)
class CommerceSyncRun:
    tenant_id: UUID
    connection_id: UUID
    sync_type: SyncType
    status: SyncStatus
    cursor: str | None = None
    safe_failure_code: str | None = None
    id: UUID = field(default_factory=uuid4)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CommerceSyncRequest:
    tenant_id: UUID
    connection_id: UUID
    requested_by_user_id: UUID
    correlation_id: UUID
    sync_types: tuple[SyncType, ...]
    status: DurableSyncStatus = DurableSyncStatus.QUEUED
    safe_failure_code: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.sync_types or len(set(self.sync_types)) != len(self.sync_types):
            raise ValueError("durable sync types must be a non-empty unique set")


@dataclass(frozen=True, slots=True)
class CommerceOperationsContextManifest:
    tenant_id: UUID
    product_id: UUID
    product_snapshot_ref: Mapping[str, str]
    mapping_ref: Mapping[str, str]
    observation_refs: tuple[Mapping[str, str], ...]
    deterministic_exceptions: tuple[Mapping[str, object], ...]
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "product_snapshot_ref", MappingProxyType(dict(self.product_snapshot_ref))
        )
        object.__setattr__(self, "mapping_ref", MappingProxyType(dict(self.mapping_ref)))
        object.__setattr__(
            self,
            "observation_refs",
            tuple(MappingProxyType(dict(v)) for v in self.observation_refs),
        )
        object.__setattr__(
            self,
            "deterministic_exceptions",
            tuple(MappingProxyType(dict(v)) for v in self.deterministic_exceptions),
        )
        material = {
            "product_snapshot_ref": dict(self.product_snapshot_ref),
            "mapping_ref": dict(self.mapping_ref),
            "observation_refs": [dict(v) for v in self.observation_refs],
            "deterministic_exceptions": [dict(v) for v in self.deterministic_exceptions],
            "schema_version": self.schema_version,
        }
        if self.semantic_digest != _digest(material):
            raise ValueError("commerce context manifest is not canonical")


@dataclass(frozen=True, slots=True)
class CommerceOperationsReport:
    tenant_id: UUID
    product_id: UUID
    agent_run_id: UUID
    agent_version_id: UUID
    context_manifest_id: UUID
    context_manifest_digest: str
    summary: str
    inventory_exceptions: tuple[Mapping[str, object], ...]
    order_exceptions: tuple[Mapping[str, object], ...]
    limitations: tuple[str, ...]
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CommerceAgentResult:
    report: CommerceOperationsReport
    proposals: tuple[CommerceActionProposal, ...]


@dataclass(frozen=True, slots=True)
class CommerceActionProposal:
    tenant_id: UUID
    connection_id: UUID
    product_id: UUID
    action_type: ActionType
    external_product_id: str | None
    external_variant_id: str | None
    external_order_id: str | None
    exact_quantity: int | None
    exact_amount: Decimal | None
    currency: str | None
    reason: str
    evidence_refs: tuple[Mapping[str, str], ...]
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_refs", tuple(MappingProxyType(dict(v)) for v in self.evidence_refs)
        )
        if self.action_type is ActionType.INVENTORY_ADJUSTMENT:
            if (
                self.external_variant_id is None
                or self.exact_quantity is None
                or self.exact_amount is not None
            ):
                raise ValueError("SET_AVAILABLE_TO requires exact variant and quantity")
        else:
            if (
                self.external_order_id is None
                or self.exact_amount is None
                or self.currency is None
                or self.exact_quantity is not None
            ):
                raise ValueError("refund requires exact order, amount, and currency")
            _money(self.exact_amount)
            _currency(self.currency)
        if not DIGEST.fullmatch(self.semantic_digest):
            raise ValueError("proposal digest is invalid")

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.R5 if self.action_type is ActionType.INVENTORY_ADJUSTMENT else RiskLevel.R6


@dataclass(frozen=True, slots=True)
class CommerceDecision:
    tenant_id: UUID
    proposal_id: UUID
    proposal_digest: str
    decision: DecisionKind
    decided_by: UUID
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CommerceActionJob:
    tenant_id: UUID
    proposal_id: UUID
    proposal_digest: str
    idempotency_key: str
    status: ActionJobStatus = ActionJobStatus.PENDING_APPROVAL
    external_operation_id: str | None = None
    safe_failure_code: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CommerceActionResult:
    tenant_id: UUID
    proposal_id: UUID
    proposal_digest: str
    action_type: ActionType
    provider: str
    status: OperationStatus
    observed_fact_refs: tuple[Mapping[str, str], ...]
    semantic_digest: str
    external_operation_id: str | None = None
    id: UUID = field(default_factory=uuid4)
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def inventory_exceptions(
    values: tuple[InventoryObservation, ...], threshold: int = DEFAULT_LOW_STOCK_THRESHOLD
) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for value in values:
        state = value.indicator(threshold)
        if state in {InventoryIndicator.OUT_OF_STOCK, InventoryIndicator.LOW_STOCK}:
            result.append(
                {
                    "kind": state.value,
                    "observation_id": str(value.id),
                    "external_variant_id": value.external_variant_id,
                    "available_quantity": value.available_quantity,
                    "rule_version": INVENTORY_RULE_VERSION,
                }
            )
    return tuple(result)


def order_exceptions(orders: tuple[OrderObservation, ...]) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for order in orders:
        kind = None
        if (
            order.financial_status is PaymentState.PAID
            and order.fulfillment_status is FulfillmentState.UNFULFILLED
        ):
            kind = OrderExceptionKind.PAID_BUT_UNFULFILLED
        elif order.financial_status is PaymentState.FAILED:
            kind = OrderExceptionKind.PAYMENT_FAILED
        elif order.financial_status is PaymentState.PARTIALLY_REFUNDED:
            kind = OrderExceptionKind.PARTIAL_REFUND
        if kind:
            result.append(
                {
                    "kind": kind.value,
                    "observation_id": str(order.id),
                    "external_order_id": order.external_order_id,
                    "rule_version": ORDER_RULE_VERSION,
                }
            )
    return tuple(result)


def proposal_digest(material: Mapping[str, object]) -> str:
    return _digest(material)
