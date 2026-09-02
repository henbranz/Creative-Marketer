# ADR-006 — Least-Privilege Agent Identity

## Status
Accepted

## Decision
Each agent has explicit identity, version, data scopes, tool scopes, budgets, and approval policy. No super-agent receives universal business access.

## Rationale
Agentic systems increase blast radius if capability boundaries are prompt-only.

## Consequences
Permission design becomes foundational and must be testable independently of LLM behavior.
