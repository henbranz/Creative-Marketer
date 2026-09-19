# ADR-038 — Intelligence Produces Hypotheses, Not Truth

- Status: Accepted
- Date: 2026-09-19

## Context

Creative Marketer can now observe publication performance and exact-reference attribution, but observational data does not normally establish causality. Allowing a model to select baselines, recalculate metrics, promote its prose into Product truth, or directly invoke another agent would create untraceable and commercially consequential behavior. Current measurements can also be synthetic.

## Decision

Add an `intelligence` bounded context inside the modular monolith and reuse the common AgentRuntime. Deterministic services own feature extraction, comparability, matched windows, median baselines, deltas, evidence quality, and trust classification. An immutable context manifest freezes exact inputs before one zero-tool structured model call.

The agent may create an immutable report, `CANDIDATE` insights, and testable experiment proposals only. It cannot establish causal truth, publish, mutate campaigns or spend, browse, use connectors, or write memory. Human decisions are append-only; current authority permits proposing a candidate for testing, rejecting it, approving an experiment for creative work, or rejecting it. `VALIDATED` and `ACTIVE` remain unavailable, and synthetic evidence is permanently ineligible for those states.

An approved proposal enters the existing Creative Strategist only through an application boundary that revalidates tenant, Product, exact digest, approval, and source report. The resulting concept set retains full proposal provenance. No agent-to-agent RPC is introduced.

## Consequences

- Numeric facts remain deterministic and auditable while prose remains explicitly interpretive.
- Matched-window and minimum-sample rules prevent misleading comparisons.
- Fake data can exercise the full loop without masquerading as market evidence.
- Reports and proposals are immutable, rerunnable, and tenant isolated.
- Product truth and validated memory do not change automatically.
- Real-provider activation requires representative observed data and future calibration.

## Rejected alternatives

- Let the LLM choose baselines or recalculate metrics: numbers would cease to be authoritative.
- Promote accepted prose directly into Product knowledge: approval is not experimental validation.
- Let Intelligence call Creative Strategist directly: it bypasses human authority and provenance checks.
- Compare across tenants: it violates the current privacy and tenancy boundary.
- Recommend spend or compute ROAS without ad-spend facts: it fabricates a decision-critical metric.
