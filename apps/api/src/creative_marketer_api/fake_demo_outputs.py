"""Deterministic structured outputs used by installed local-demo workers."""


def creative_output(invocation: object) -> dict[str, object]:
    sections = invocation.capability_context  # type: ignore[attr-defined]
    research = sections["research_findings"][0]
    claim = sections["product_claim_refs"][0]
    request = sections["strategy_request"]
    concepts = []
    for index in range(int(request["concept_count"])):
        concepts.append(
            {
                "concept_key": f"local_demo_{index + 1}",
                "title": f"LOCAL DEMO concept {index + 1}",
                "format": "SHORT_FORM_VIDEO",
                "channel_intent": request["channel_intent"],
                "creative_angle": f"Durability in daily motion {index + 1}",
                "strategic_rationale": "Grounded in the captured commuter evidence.",
                "target_audience": "Busy commuters",
                "hook": {
                    "spoken_or_voiceover": f"Built for commute routine {index + 1}.",
                    "on_screen_text": "One bottle. Every day.",
                    "visual_open": (f"Bottle reveal variation {index + 1} beside a train pass."),
                },
                "estimated_duration_seconds": 15,
                "scenes": [
                    {
                        "ordinal": ordinal,
                        "estimated_duration_seconds": 5,
                        "purpose": purpose,
                        "visual_direction": direction,
                        "on_screen_text": None,
                        "voiceover": voice,
                        "message_point_refs": ["durable_claim"],
                        "asset_requirements": [],
                    }
                    for ordinal, purpose, direction, voice in (
                        (
                            1,
                            "Hook",
                            f"Fast tabletop reveal variation {index + 1}",
                            "Built for real routines.",
                        ),
                        (2, "Proof", "Close product detail", "Durable and reusable."),
                        (3, "Action", "Product exits frame", "Take it everywhere."),
                    )
                ],
                "cta": {"text": "See the bottle", "intent": "DISCOVER"},
                "hypothesis": "A tangible routine-first hook improves hold rate.",
                "primary_success_metric": "HOOK_HOLD_RATE",
                "supporting_research_refs": [
                    {
                        "research_snapshot_id": sections["research_snapshot_id"],
                        "finding_key": research["key"],
                    }
                ],
                "message_points": [
                    {
                        "key": "durable_claim",
                        "kind": "PRODUCT_FACT",
                        "text": claim["text"],
                        "product_claim_ref": claim["key"],
                    }
                ],
                "required_assets": [
                    {
                        "kind": "EXISTING_ASSET",
                        "asset_id": sections["available_assets"][0]["asset_id"],
                        "intended_role": "product hero",
                    }
                ],
                "required_disclaimers": ["LOCAL DEMO imagery"],
                "production_notes": "LOCAL DEMO — use the governed fake providers.",
            }
        )
    return {"concepts": concepts, "strategy_limitations": ["LOCAL DEMO fixture"]}


def production_output(invocation: object) -> dict[str, object]:
    sections = invocation.capability_context  # type: ignore[attr-defined]
    asset_id = sections["selected_assets"][0]["asset_id"]
    base = {
        "visual_objective": "Show the product",
        "subject": "Bottle",
        "environment": "Commuter desk",
        "composition": "Centered",
        "framing": "Close",
        "lens_intent": "Natural",
        "camera_position": "Eye level",
        "camera_motion": "Slow push",
        "lighting": "Soft daylight",
        "action": "Rotate",
        "continuity_requirements": ["Same bottle"],
        "product_preservation_constraints": ["Keep label readable"],
        "audio_intent": None,
    }
    image_spec = {
        "subject": "Bottle",
        "environment": "Commuter desk",
        "composition": "Centered",
        "camera_framing": "Close",
        "lighting": "Soft daylight",
        "style": "Editorial",
        "product_preservation_constraints": ["Keep label readable"],
        "reference_asset_ids": [asset_id],
        "aspect_ratio": "9:16",
        "quality_intent": "HIGH",
        "negative_constraints": ["No distortion"],
    }
    scenes = []
    for ordinal, key in enumerate(sections["concept_scene_keys"], 1):
        strategy = (
            "GENERATE_IMAGE"
            if ordinal == 1
            else "GENERATE_VIDEO"
            if ordinal == 2
            else "MANUAL_CAPTURE"
        )
        scenes.append(
            {
                "scene_key": key,
                "ordinal": ordinal,
                "purpose": ("Hook", "Proof", "Action")[ordinal - 1],
                "duration_seconds": 5,
                "message": "LOCAL DEMO product story",
                "voiceover": None,
                "on_screen_text": None,
                "shots": [
                    {
                        **base,
                        "shot_key": f"local_demo_shot_{ordinal}",
                        "scene_key": key,
                        "ordinal": 1,
                        "source_strategy": strategy,
                        "existing_asset_id": None,
                        "image_generation_spec": image_spec if ordinal == 1 else None,
                    }
                ],
            }
        )
    spec = {
        "subject": "Bottle",
        "environment": "Commuter desk",
        "composition": "Centered",
        "camera_motion": "Slow push",
        "lighting": "Soft daylight",
        "action": "Rotate",
        "product_preservation_constraints": ["Keep label readable"],
        "negative_constraints": ["No distortion"],
        "generate_audio": False,
        "resolution": "720p",
        "aspect_ratio": "9:16",
    }
    return {
        "strategy": "LOCAL DEMO governed image and video production.",
        "format": {"kind": "SHORT_FORM_VERTICAL_VIDEO", "aspect_ratio": "9:16"},
        "scenes": scenes,
        "generation_segments": [
            {
                "segment_key": "local_demo_image",
                "shot_keys": ["local_demo_shot_1"],
                "media_kind": "IMAGE",
                "duration_seconds": None,
                "continuity": [],
                "reference_asset_ids": [asset_id],
                "generation_spec": spec,
            },
            {
                "segment_key": "local_demo_video",
                "shot_keys": ["local_demo_shot_2"],
                "media_kind": "VIDEO",
                "duration_seconds": 5,
                "continuity": ["Same bottle"],
                "reference_asset_ids": [asset_id],
                "generation_spec": spec,
            },
        ],
        "required_assets": ["LOCAL DEMO voiceover"],
    }


def intelligence_output(invocation: object) -> dict[str, object]:
    sections = invocation.capability_context  # type: ignore[attr-defined]
    comparisons = sections.get("performance_comparisons", [])
    if not comparisons:
        return {
            "summary": "A single synthetic publication is available for workflow validation.",
            "observations": [
                {
                    "statement": (
                        "Synthetic performance facts were observed without a reliable "
                        "matched baseline."
                    ),
                    "source_ref": "local-demo-performance",
                    "window": "UNMATCHED",
                }
            ],
            "comparative_findings": [],
            "insight_candidates": [],
            "limitations": [
                "Synthetic demo data; not real market evidence.",
                "Insufficient comparable matched-window sample; no trend conclusion is available.",
            ],
            "next_experiments": [],
        }
    comparison = comparisons[0]
    return {
        "summary": (
            "A possible synthetic pattern is worth testing in a controlled creative experiment."
        ),
        "observations": [
            {
                "statement": "A deterministic matched-window comparison is available.",
                "source_ref": comparison["id"],
                "window": comparison["comparison_window"],
            }
        ],
        "comparative_findings": [
            {
                "comparison_id": comparison["id"],
                "interpretation": (
                    "This may indicate a creative feature worth isolating in a test."
                ),
            }
        ],
        "insight_candidates": [
            {
                "statement": (
                    "The opening treatment may be worth testing while other elements remain fixed."
                ),
                "comparison_ids": [comparison["id"]],
                "metric": comparison["metric_key"],
                "confidence": "LOW",
                "scope": {"platform": "local-demo"},
                "limitations": ["Synthetic and observational evidence only."],
            }
        ],
        "limitations": [
            "Synthetic demo data; not real market evidence.",
            "Observational evidence does not establish causality.",
            "No advertising spend facts are available.",
        ],
        "next_experiments": [
            {
                "candidate_indexes": [0],
                "hypothesis": "An alternate opening treatment may be worth further testing.",
                "primary_variable": "opening treatment",
                "controlled_elements": ["CTA", "caption", "duration"],
                "target_metric": comparison["metric_key"],
                "platform": "local-demo",
                "recommended_measurement_window": comparison["comparison_window"],
                "creative_direction": (
                    "Create variants that differ only in the opening treatment."
                ),
                "rationale": "A single-variable experiment supports clearer learning.",
                "expected_learning": (
                    "Whether the opening treatment merits a larger observed-data test."
                ),
            }
        ],
    }
