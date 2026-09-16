# mypy: disable-error-code="arg-type,no-untyped-def,no-untyped-call,assignment,var-annotated,import-untyped,misc"
# ruff: noqa: E501

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.measurement.application import MeasurablePublication, MeasurementService
from creative_marketer.measurement.domain import (
    AttributionReference,
    AttributionReferenceInvalid,
    ConversionConflict,
    Freshness,
    MeasurementNotFound,
    MeasurementPermissionDenied,
    MeasurementProviderFailed,
    MetricSemantics,
    PerformanceObservation,
    ProviderMetric,
    derive_metrics,
)
from creative_marketer.measurement.provider import FakeSocialMetricsProvider


def context(role=MembershipRole.OWNER, tenant_id=None):
    user_id = uuid4()
    return ExecutionContext(
        tenant_id or uuid4(),
        Actor(ActorKind.USER, user_id),
        user_id,
        role,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "mfa"),
    )


class Sink:
    def __init__(self):
        self.values = []

    async def append(self, value):
        self.values.append(value)


class Repository:
    def __init__(self, publication):
        self.publication_value = publication
        self.observation_values = []
        self.run_values = []
        self.snapshot_values = []
        self.reference_values = []
        self.conversion_values = []
        self.result_values = []

    async def publication(self, publication_id):
        return (
            self.publication_value
            if publication_id == self.publication_value.publication_id
            else None
        )

    async def observations(self, publication_id):
        return tuple(
            item for item in self.observation_values if item.publication_id == publication_id
        )

    async def add_observation(self, value):
        if any(item.source_digest == value.source_digest for item in self.observation_values):
            return False
        self.observation_values.append(value)
        return True

    async def cursor(self, publication_id):
        successful = [
            item
            for item in self.run_values
            if item.publication_id == publication_id and item.status.value == "SUCCEEDED"
        ]
        return successful[-1].cursor if successful else None

    async def add_run(self, value):
        self.run_values.append(value)

    async def latest_snapshot(self, publication_id):
        values = [item for item in self.snapshot_values if item.publication_id == publication_id]
        return values[-1] if values else None

    async def add_snapshot(self, value):
        if any(item.semantic_digest == value.semantic_digest for item in self.snapshot_values):
            return False
        self.snapshot_values.append(value)
        return True

    async def add_reference(self, value):
        self.reference_values.append(value)

    async def reference_by_hash(self, code_hash):
        return next(
            (item for item in self.reference_values if item.public_code_hash == code_hash), None
        )

    async def references(self, publication_id):
        return tuple(
            item for item in self.reference_values if item.publication_id == publication_id
        )

    async def conversion_by_external_id(self, source, external_id):
        return next(
            (
                item
                for item in self.conversion_values
                if item.source == source and item.external_id == external_id
            ),
            None,
        )

    async def add_conversion(self, value):
        self.conversion_values.append(value)

    async def add_attribution(self, value):
        self.result_values.append(value)

    async def attributed_conversions(self, publication_id):
        return tuple(
            (
                next(
                    item
                    for item in self.conversion_values
                    if item.id == result.conversion_observation_id
                ),
                result,
            )
            for result in self.result_values
            if result.publication_id == publication_id
        )

    async def attributed_conversions_for_conversion(self, conversion_id):
        return tuple(
            item for item in self.result_values if item.conversion_observation_id == conversion_id
        )

    async def snapshots_for_product(self, product_id):
        latest = {}
        for item in self.snapshot_values:
            if item.product_id == product_id:
                latest[item.publication_id] = item
        return tuple(latest.values())


class Uow:
    def __init__(self, repository, audit):
        self.measurement = repository
        self.audit = audit
        self.outbox = Sink()
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def commit(self):
        self.commits += 1


def service(*, sequences=None, role=MembershipRole.OWNER):
    ctx = context(role)
    publication = MeasurablePublication(
        ctx.tenant_id, uuid4(), uuid4(), "fake-post", "https://shop.invalid/p"
    )
    repository = Repository(publication)
    audit = Sink()
    uow = Uow(repository, audit)
    return (
        ctx,
        publication,
        repository,
        audit,
        MeasurementService(lambda _tenant: uow, FakeSocialMetricsProvider(sequences=sequences)),
    )


def test_metric_formulas_are_versioned_and_never_invent_denominators():
    valid = {
        item.key: item
        for item in derive_metrics(
            {
                "impressions": Decimal(100),
                "clicks": Decimal(5),
                "likes": Decimal(2),
                "comments": Decimal(1),
                "shares": Decimal(1),
                "saves": Decimal(1),
            },
            attributed_conversions=1,
        )
    }
    assert valid["ctr"].value == Decimal("0.05")
    assert valid["engagement_rate"].value == Decimal("0.05")
    assert valid["conversion_rate"].value == Decimal("0.2")
    zero = {
        item.key: item
        for item in derive_metrics(
            {"impressions": Decimal(0), "clicks": Decimal(0)}, attributed_conversions=0
        )
    }
    assert zero["ctr"].value is None and zero["ctr"].unavailable_reason == "ZERO_DENOMINATOR"
    assert zero["engagement_rate"].unavailable_reason == "MISSING_INPUT"
    missing = {item.key: item for item in derive_metrics({}, attributed_conversions=0)}
    assert all(item.value is None for item in missing.values())
    assert "roas" not in valid


@pytest.mark.asyncio
async def test_fake_provider_is_zero_network_deterministic_and_cursor_resumable():
    provider = FakeSocialMetricsProvider()
    first = await provider.collect("post", None)
    second = await provider.collect("post", first.next_cursor)
    replay = await provider.collect("post", None)
    assert first == replay
    assert second.metrics[0].observed_at > first.metrics[0].observed_at
    assert all(item.key != "unavailable" for item in first.metrics)


@pytest.mark.asyncio
async def test_collection_deduplicates_identical_facts_and_advances_cumulative_values():
    ctx, publication, repository, audit, subject = service()
    first = await subject.collect(ctx, publication.publication_id)
    count = len(repository.observation_values)
    second = await subject.collect(ctx, publication.publication_id)
    assert len(repository.observation_values) > count
    assert first.latest_metrics["impressions"] == Decimal(100)
    assert second.latest_metrics["impressions"] == Decimal(250)
    assert set(second.observation_ids) == {item.id for item in repository.observation_values}
    assert second.freshness in {Freshness.CURRENT, Freshness.STALE}
    assert audit.values[-1].action == "measurement.collection.completed"


@pytest.mark.asyncio
async def test_repeated_terminal_cursor_deduplicates_and_unavailable_is_not_zero():
    sequences = {"fake-post": ({"impressions": 5, "clicks": None},)}
    ctx, publication, repository, _audit, subject = service(sequences=sequences)
    await subject.collect(ctx, publication.publication_id)
    await subject.collect(ctx, publication.publication_id)
    assert len(repository.observation_values) == 1
    snapshot = repository.snapshot_values[-1]
    assert "clicks" not in snapshot.latest_metrics
    assert next(item for item in snapshot.derived_metrics if item.key == "ctr").value is None


@pytest.mark.asyncio
async def test_exact_reference_conversion_attribution_is_idempotent_and_currency_safe():
    ctx, publication, repository, _audit, subject = service()
    await subject.collect(ctx, publication.publication_id)
    issued = await subject.issue_reference(
        ctx, publication.publication_id, "https://shop.invalid/p?x=1"
    )
    assert "cm_ref=" in issued.tracked_destination_url
    assert str(publication.publication_id) not in issued.public_code
    conversion, result = await subject.ingest_fake_conversion(
        ctx,
        external_id="order-1",
        amount=Decimal("19.95"),
        currency="USD",
        observed_at=datetime.now(UTC),
        public_code=issued.public_code,
    )
    assert result is not None and result.publication_id == publication.publication_id
    same, same_result = await subject.ingest_fake_conversion(
        ctx,
        external_id="order-1",
        amount=Decimal("19.95"),
        currency="USD",
        observed_at=conversion.observed_at,
        public_code=issued.public_code,
    )
    assert same.id == conversion.id and same_result == result
    await subject.ingest_fake_conversion(
        ctx,
        external_id="order-2",
        amount=Decimal("25"),
        currency="EUR",
        observed_at=datetime.now(UTC),
        public_code=issued.public_code,
    )
    revenue = repository.snapshot_values[-1].attributed_revenue
    assert revenue == {"USD": Decimal("19.95"), "EUR": Decimal(25)}
    with pytest.raises(ConversionConflict):
        await subject.ingest_fake_conversion(
            ctx,
            external_id="order-1",
            amount=Decimal("20"),
            currency="USD",
            observed_at=conversion.observed_at,
            public_code=issued.public_code,
        )


@pytest.mark.asyncio
async def test_missing_reference_is_unattributed_and_invalid_reference_rejected():
    ctx, _publication, repository, _audit, subject = service()
    conversion, result = await subject.ingest_fake_conversion(
        ctx,
        external_id="unattributed",
        amount=Decimal(3),
        currency="USD",
        observed_at=datetime.now(UTC),
        public_code=None,
    )
    assert conversion.attribution_code_hash is None and result is None
    assert not repository.result_values
    with pytest.raises(AttributionReferenceInvalid):
        await subject.ingest_fake_conversion(
            ctx,
            external_id="invalid",
            amount=Decimal(3),
            currency="USD",
            observed_at=datetime.now(UTC),
            public_code="cmr_" + "x" * 40,
        )


def test_observations_and_attribution_results_are_immutable_and_validate_semantics():
    metric = ProviderMetric("impressions", Decimal(1), datetime.now(UTC))
    observation = PerformanceObservation.from_provider(
        tenant_id=uuid4(),
        product_id=uuid4(),
        publication_id=uuid4(),
        metric=metric,
        provider="fake",
        provider_version="v1",
    )
    assert observation.semantics is MetricSemantics.CUMULATIVE
    with pytest.raises(FrozenInstanceError):
        observation.value = Decimal(2)
    with pytest.raises(ValueError):
        ProviderMetric("unknown", Decimal(1), datetime.now(UTC))


@pytest.mark.asyncio
async def test_member_cannot_collect_or_issue_reference():
    ctx, publication, _repository, _audit, subject = service(role=MembershipRole.MEMBER)
    with pytest.raises(MeasurementPermissionDenied):
        await subject.collect(ctx, publication.publication_id)
    with pytest.raises(MeasurementPermissionDenied):
        await subject.issue_reference(ctx, publication.publication_id, "https://shop.invalid")


@pytest.mark.asyncio
async def test_collection_failure_is_terminal_audited_and_unknown_publications_fail_closed():
    ctx, publication, repository, audit, subject = service()
    subject.provider = FakeSocialMetricsProvider(fail_posts=frozenset({"fake-post"}))
    with pytest.raises(MeasurementProviderFailed):
        await subject.collect(ctx, publication.publication_id, checkpoint="schedule-v1:1")
    assert repository.run_values[-1].status.value == "FAILED"
    assert repository.run_values[-1].failure_code == "MEASUREMENT_PROVIDER_FAILED"
    assert audit.values[-1].action == "measurement.collection.failed"
    unknown = uuid4()
    with pytest.raises(MeasurementNotFound):
        await subject.collect(ctx, unknown)
    with pytest.raises(MeasurementNotFound):
        await subject.get_publication(ctx, unknown)
    with pytest.raises(MeasurementNotFound):
        await subject.history(ctx, unknown)


@pytest.mark.asyncio
async def test_empty_reads_and_reference_destination_validation_preserve_no_data_truth():
    ctx, publication, _repository, _audit, subject = service()
    empty = await subject.get_publication(ctx, publication.publication_id)
    assert empty.freshness is Freshness.NO_DATA and not empty.observation_ids
    assert await subject.product(ctx, publication.product_id) == ()
    assert await subject.history(ctx, publication.publication_id) == ()
    for invalid in ("relative/path", "ftp://shop.invalid/p", "https://user@shop.invalid/p"):
        with pytest.raises(AttributionReferenceInvalid):
            await subject.issue_reference(ctx, publication.publication_id, invalid)
    with pytest.raises(MeasurementNotFound):
        await subject.issue_reference(ctx, uuid4(), "https://shop.invalid/p")


@pytest.mark.asyncio
async def test_fake_provider_rejects_invalid_cursor_and_explicit_failure() -> None:
    provider = FakeSocialMetricsProvider(fail_posts=frozenset({"failed"}))
    with pytest.raises(MeasurementProviderFailed):
        await provider.collect("failed", None)
    with pytest.raises(MeasurementProviderFailed):
        await provider.collect("post", "fake:not-a-number")


def test_reference_code_is_opaque_high_entropy_and_destination_is_not_a_redirect():
    code = AttributionReference.issue_code()
    assert len(code) >= 40
    assert UUID(int=0).hex not in code
    assert len(AttributionReference.hash_code(code)) == 64
    with pytest.raises(AttributionReferenceInvalid):
        AttributionReference.hash_code("bad")
