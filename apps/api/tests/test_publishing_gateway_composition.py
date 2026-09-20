# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,attr-defined"

from types import SimpleNamespace

import pytest

import creative_marketer.publishing.gateway_composition as module
from creative_marketer.publishing.gateway_composition import PublishingGatewayFactory


@pytest.mark.asyncio
async def test_gateway_factory_resolves_exact_social_tools_and_caches_gateway(monkeypatch) -> None:
    resolved = []

    class Resolver:
        def __init__(self, factory):
            self.factory = factory

        async def __call__(self, key):
            resolved.append(key)
            return SimpleNamespace(definition_id=key, version_id=key + ":v1")

    class Gateway:
        def __init__(self, *args):
            self.args = args

    monkeypatch.setattr(module, "SqlAlchemyToolRegistryUnitOfWorkFactory", lambda sessions: "tools")
    monkeypatch.setattr(module, "ResolveActiveTool", Resolver)
    monkeypatch.setattr(
        module, "SqlAlchemyAgentRegistryUnitOfWorkFactory", lambda sessions: "agents"
    )
    monkeypatch.setattr(module, "ResolveActiveAgentVersion", lambda factory: "agent-resolver")
    monkeypatch.setattr(
        module, "SqlAlchemyPermissionUnitOfWorkFactory", lambda sessions: "permissions"
    )
    monkeypatch.setattr(module, "EvaluateToolPermission", lambda *args: "evaluator")
    monkeypatch.setattr(module, "social_tool_bindings", lambda authority, tools: tuple(tools))
    monkeypatch.setattr(module, "ToolExecutionBindingRegistry", lambda values: values)
    monkeypatch.setattr(
        module, "SqlAlchemyGatewayUnitOfWorkFactory", lambda sessions: "gateway-uow"
    )
    monkeypatch.setattr(module, "ToolGateway", Gateway)

    factory = PublishingGatewayFactory(SimpleNamespace(), SimpleNamespace())
    first = await factory()
    second = await factory()
    assert first is second
    assert resolved == [
        "social.publish.submit",
        "social.publish.status",
        "social.publish.cancel",
    ]
    assert first.args[-1] == "gateway-uow"
