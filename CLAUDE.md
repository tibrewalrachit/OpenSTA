# Workspace guide

This checkout is upstream OpenSTA plus two integration layers developed on
branch `claude/blacktimer-gpu-sta-06lc5m`:

- `blacktimer/` — GPU-native STA engine project (Modal app named
  `blacktimer`, `TimerSession` class). Read `blacktimer/DESIGN.md` first;
  evaluation protocol in `blacktimer/EVALUATION.md`.
- `work/` — sta-claude integration material: smoke design
  (`work/design.tcl`), report fixtures (`work/fixtures/`), and the
  OpenSTA command-drift findings (`work/OPENSTA_BACKEND_VALIDATION.md`).
- `sta-claude/` — LLM timing-debug MCP layer; expected at the workspace
  root but NOT yet vendored. `.mcp.json` already points at
  `sta-claude/server/sta_mcp.py` (opensta backend) and passes
  `STA_BINARY`/`STA_DESIGN`; revisit those names against its README when
  the checkout lands.

## Build and test

- OpenSTA: `cmake -B build -G Ninja -DCUDD_DIR=<cudd-3.0.0 dir> && ninja -C build sta`
  (CUDD built per Dockerfile.ubuntu22.04). Do not modify OpenSTA sources
  for integration work — drive `sta` over a pipe.
- `make blacktimer-test` — 53 pytest tests (harness, evaluation data,
  session RPC over a live sta pipe).
- `make sta-smoke` — MCP stdio smoke (design_summary + worst_paths);
  requires the sta-claude checkout.
- Modal: `modal run blacktimer/modal_app.py::seed_designs` then `::ci`.
  gRPC does not traverse the Claude remote-session egress proxy, so run
  these locally or via the GitHub Actions modal-gate job
  (`.github/workflows/blacktimer.yml`, needs MODAL_TOKEN_ID/SECRET
  secrets).

## Conventions

- Timing values in RPC/tool payloads: ns floats plus `*_ps` integer
  siblings; unconstrained = None, never faked.
- This OpenSTA renamed corners to scenes (`sta::scenes`, `set_scene`);
  `report_exceptions` and `insert_buffer` do not exist — see
  `work/OPENSTA_BACKEND_VALIDATION.md` before adding backend Tcl.
- No timing exceptions in smoke designs or tests.
