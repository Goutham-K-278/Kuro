# Kuro Vision Integration Plan

## Goal

Add Florence-2 based screen intelligence to Kuro with safe phased rollout, low latency, and no regression in current voice/tool workflow.

## Current Baseline (observed)

- Async command pipeline already exists in `kuro/pipeline.py`.
- Model lifecycle and VRAM gate already exist in `core/model_manager.py`.
- LLM intent entry-point exists in `kuro/utils/llm.py`.
- No `kuro/vision` runtime module exists yet.

## Phased Implementation

### Phase 1: Foundation (safe, low-risk)

1. Create `kuro/vision` package with:

    - `screen_capture.py`
    - `florence_engine.py`
    - `scene_analyzer.py`
    - `action_verifier.py`
    - `element_finder.py`
    - `state_tracker.py`

2. Add vision config flags in `core/config.py`.
3. Register and optional prewarm Florence in `main.py` via model manager.
4. Add setup + diagnostics scripts:

    - `scripts/setup_florence.py`
    - `scripts/test_vision.py`

Exit criteria:

- `python scripts/test_vision.py` runs without crashes.
- Health endpoint still works.

### Phase 2: Reasoning context injection

1. Extend parse signature in LLM reasoners to accept optional `vision_context`.
2. Add a non-blocking `KuroPipeline._get_vision_context()` helper.
3. Inject context before `parse_intent()`.

Exit criteria:

- Existing commands still execute.
- LLM receives vision context only when enabled.

### Phase 3: Post-action verification loop

1. Capture pre-state before action execution.
2. Run post-action verification after result.
3. Attach verification metadata to action result events.

Exit criteria:

- Result payload includes `vision_verified` and `vision_description` when enabled.

### Phase 4: Hardening and tuning

1. Tune OCR/grounding timeouts.
2. Add app-specific state rules.
3. Tune VRAM behavior and prewarm policy.

Exit criteria:

- Stable latency and no OOM in normal workflows.

## Dependency Policy

- Add: `transformers`, `torch`, `torchvision`, `bitsandbytes`, `accelerate`.
- Keep `easyocr` and `ultralytics` for now to avoid breaking existing OmniParser/screen watcher paths.
- Remove legacy deps only after migration verification.

## Execution Order (this run)

1. Phase 1 implementation.
2. Phase 2 implementation.
3. Phase 3 implementation.
4. Validation and report.
