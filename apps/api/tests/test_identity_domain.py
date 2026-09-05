from uuid import uuid4

import pytest

from creative_marketer.identity.domain import Membership, Tenant, User
from creative_marketer.identity.domain.entities import normalize_email


def test_email_normalization_is_trimmed_and_case_insensitive() -> None:
    user = User.create(" Person@Example.COM ")
    assert user.email == "Person@Example.COM"
    assert user.normalized_email == "person@example.com"


def test_invalid_email_is_rejected() -> None:
    with pytest.raises(ValueError, match="local part and domain"):
        normalize_email("not-an-email")


def test_entity_defaults_are_unique_and_timezone_aware() -> None:
    first = Tenant(name="One", slug="one")
    second = Tenant(name="Two", slug="two")
    membership = Membership(tenant_id=first.id, user_id=uuid4())
    assert first.id != second.id
    assert first.created_at.tzinfo is not None
    assert membership.created_at.tzinfo is not None
