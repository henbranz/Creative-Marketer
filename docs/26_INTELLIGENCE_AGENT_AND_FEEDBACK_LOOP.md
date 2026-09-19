# 26 — Intelligence Agent and Governed Creative Feedback Loop

## Purpose and boundary

The `intelligence` bounded context closes the development learning loop without turning model output into marketing truth. Deterministic application services select tenant-local evidence, extract known creative metadata, match measurement windows, and calculate baselines and deltas. The Intelligence Agent receives those bounded facts and may only return structured interpretations, limitations, candidate hypotheses, and experiments. It has no tools, connectors, memory writes, publishing authority, campaign authority, or spend authority.

The flow is:

```text
PerformanceSnapshot
  -> IntelligenceContextManifest
  -> PerformanceComparison
  -> AgentRun (performance_intelligence)
  -> IntelligenceReport + CANDIDATE InsightCandidate + ExperimentProposal
  -> append-only human ExperimentDecision
  -> application-layer Generate next concepts
  -> existing Creative Strategist AgentRun
  -> CreativeConceptSet linked to the exact proposal
```

There is no agent-to-agent RPC. The application layer revalidates the approved proposal and constructs a new immutable Creative Strategy context.

## Deterministic analytics

`creative-features-v1` extracts only metadata already represented by canonical concept, production, assembly, final-creative, and publication artifacts. Missing features stay missing. It does not inspect media bytes or infer people, emotion, lighting, camera motion, or style.

`intelligence-comparability-v1` requires the same tenant, Product, platform, compatible publication type, shared metric, and exact measurement-age checkpoint. Supported checkpoints are `+1h`, `+6h`, `+24h`, `+72h`, and `+7d`. An unmatched/manual snapshot may be summarized but is never compared.

`intelligence-calculation-v1` uses the median of at least three previous comparable publications. The immutable comparison records the subject value, median, absolute delta, safe optional relative delta, baseline population, publication IDs, snapshot IDs, policy versions, trust level, and digest. A zero baseline produces no relative delta. Fewer than three observations produces no comparison.

## Manifest and provenance

Before inference, `IntelligenceContextManifest` freezes exact Product and Research snapshots, creative artifacts, publications, performance snapshots, attribution facts, extracted features, comparisons, versions, trust level, and a canonical semantic digest. The AgentRun binds the manifest ID and digest. Runtime resolution reconstructs this exact manifest; it never resolves “latest” after the run begins.

Reports bind the AgentRun, AgentVersion, output contract, manifest, model route recorded by the common AgentRuntime, and semantic digest. Reruns create new immutable reports.

## Trust, confidence, and causality

Trust is explicit:

- `SYNTHETIC`: any input measurement came from the fake provider.
- `OBSERVED`: all selected measurement facts came from a governed real observation source.

Synthetic trust propagates to the report, candidates, proposals, and Creative Strategist handoff. Synthetic candidates are permanently capped at `LOW`; database and application guards make `VALIDATED` and `ACTIVE` unavailable. Current lifecycle authority is intentionally narrow: the agent creates `CANDIDATE`; a human may append `PROPOSE_FOR_TESTING` or `REJECT`. Human approval does not manufacture validation.

Confidence is categorical. Synthetic data and insufficient samples cap it at `LOW`; future calibration is required before stronger evidence rules are activated. Every report and candidate requires limitations. Validation rejects causal certainty, winner/guarantee claims, unsupported numbers, budget recommendations, and ROAS/CPA/CPC/CPM conclusions. Generation cost is never advertising spend.

## Experiment governance

An immutable `ExperimentProposal` identifies one primary variable, controlled elements, target metric, platform, measurement window, rationale, and expected learning. An append-only `ExperimentDecision` may be `APPROVED_FOR_CREATIVE` or `REJECTED` and binds the exact proposal digest. Approval does not publish, schedule, generate automatically, or mutate Product/Research history.

For “Generate next concepts,” the application revalidates tenant, Product, approval, exact digest, source report, and canonical references. The existing Creative Strategist receives the proposal as bounded data. The new `CreativeConceptSet` preserves proposal/report provenance without changing old artifacts.

## Product and human-knowledge surfaces

The Insights tab distinguishes deterministic Observed/Derived facts from AI hypotheses and shows comparison window, baseline, delta, sample size, confidence, evidence scope, and limitations. Synthetic reports always display a persistent warning. Proposal actions require an authorized human.

The Knowledge Graph and Obsidian bridge project `IntelligenceReport`, `InsightCandidate`, and `ExperimentProposal`, plus their performance and resulting concept-set links. Notes contain safe structured facts only—never prompts, provider payloads, hidden reasoning, credentials, tracking codes, signed URLs, or storage keys.

## Production activation gate

This implementation is development-ready, not activated for automatic real-customer learning. Production use remains blocked until governed real Meta/Instagram and TikTok metrics adapters exist, commerce/conversion facts are available where needed, operational acceptance and data-quality review pass, representative samples exist, and insight confidence is calibrated. Missing integrations do not block fake-provider workflow validation, but they do block claims of real-world learning.
