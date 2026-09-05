# ADR-006 — Least-Privilege Agent Identity

## Status
Accepted

## Decision
Each agent has explicit identity, version, data scopes, tool scopes, budgets, and approval policy. No super-agent receives universal business access.

## Rationale
Agentic systems increase blast radius if capability boundaries are prompt-only.

## Consequences
Permission design becomes foundational and must be testable independently of LLM behavior.

## TASK-005 implementation clarification

Agent Registry stores requested tool keys and data/memory scopes as immutable, validated
declarations on an `AgentVersion`. They are not permissions and do not authorize execution.
Unknown tool keys remain inert and the future Tool Registry and Permission Engine deny by default.

Current tenant registry mutations require a trusted `ExecutionContext` whose actor is the
authenticated human User. Tenant and actor identifiers are derived from that context rather than
accepted separately. Agent actors cannot create versions, change activation, or alter their own
identity, budgets, scopes, or tool declarations. Platform templates are controlled only by the
migration/control-plane boundary until platform administration authorization exists.
