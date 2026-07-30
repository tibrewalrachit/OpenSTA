# Workspace-level convenience targets (the OpenSTA build itself is CMake;
# see README.md). Added for the sta-claude timing-debug integration.

STA ?= build/sta
SMOKE_DESIGN := work/design.tcl

.PHONY: sta-smoke blacktimer-test

# Start the sta-claude opensta MCP server on the smoke design and drive
# design_summary + worst_paths through a minimal stdio client.
# Requires the sta-claude checkout at ./sta-claude (not yet vendored --
# see work/OPENSTA_BACKEND_VALIDATION.md).
sta-smoke: $(STA)
	@test -f sta-claude/server/sta_mcp.py || \
	  { echo "sta-claude/ checkout missing: clone it into ./sta-claude first"; exit 1; }
	STA_BINARY=$(abspath $(STA)) STA_DESIGN=$(abspath $(SMOKE_DESIGN)) \
	  python3 work/mcp_smoke_client.py \
	  python3 sta-claude/server/sta_mcp.py --backend opensta

$(STA):
	@echo "no sta binary; build with: cmake -B build -G Ninja -DCUDD_DIR=<cudd> && ninja -C build sta"
	@exit 1

blacktimer-test:
	python3 -m pytest blacktimer/tests -q
