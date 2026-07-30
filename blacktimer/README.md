# BlackTimer

A GPU-native static timing analysis engine targeting Blackwell-class GPUs,
developed and CI-gated on Modal Labs, with vendored OpenSTA (the parent
directory of this one) as its frontend and golden reference.

Read [DESIGN.md](DESIGN.md) first — everything here implements its Phase 0
(section 9): the Modal app topology, the golden-diff harness running as a
null test (CPU engine on both sides), and the two hardware-abstraction
contracts the engine will be built against.

## Layout

```
blacktimer/
├── DESIGN.md                  # the design document (Draft v0.1)
├── EVALUATION.md              # evaluation methodology (GPUTimer TCAD'23)
├── modal_app.py               # Modal app: image, volumes, fn families (s4.1)
├── harness/
│   ├── golden_diff.py         # 1 ps endpoint-slack gate + waivers (s8)
│   └── evaluation.py          # GPUTimer protocol: manifests, published
│                              #   reference tables, Table I/II schemas
├── scripts/
│   └── dump_endpoints.tcl     # OpenSTA script: endpoint slacks -> CSV
├── engine/
│   ├── CMakeLists.txt         # Phase 0: interface headers only
│   └── include/blacktimer/
│       ├── hac.hpp            # Hardware Abstraction Contract (s3.1)
│       └── graph_image.hpp    # position-independent binary format (s5.4)
└── tests/
    ├── test_golden_diff.py    # harness unit tests (pure Python)
    └── test_evaluation.py     # protocol + published-data transcription
```

## Running Phase 0

Local (no GPU, no Modal account — the harness tests):

```sh
pip install pytest
python -m pytest blacktimer/tests -q
```

On Modal (`pip install modal && modal setup`):

```sh
# once: upload the in-repo smoke design (gcd_sky130hd) to the designs volume
modal run blacktimer/modal_app.py::seed_designs

# the per-merge gate: build + unit tests + golden null-diff
modal run blacktimer/modal_app.py::ci

# a single design diff
modal run blacktimer/modal_app.py::diff --design gcd_sky130hd
```

The image builds vendored OpenSTA from this repository, so `golden_diff`
runs the real reference flow end to end; the device-under-test engine is
selected by `DUT_ENGINE` in `modal_app.py` and is `"cpu"` until Phase 1
registers the GPU engine — flipping that constant turns the null test into
the real diff.

### Benchmarks and evaluation

Performance evaluation follows the GPUTimer TCAD'23 experimental protocol —
see [EVALUATION.md](EVALUATION.md) for the three experiments (single-corner
full timing, incremental sweeps with crossover extraction, 128-corner BC
sweep) and `harness/evaluation.py` for the benchmark manifest, the published
reference tables, and the report schemas.

The TAU 2015/2019 contest suites and the leon2/leon3mp/netcard/vga_lcd
designs (DESIGN.md s2.2) are not vendored; fetch them from the contest
distributions and upload each design directory to the `blacktimer-designs`
volume with a `design.json` descriptor:

```json
{
  "liberty": ["corner_typ.lib"],
  "verilog": "design.v",
  "top": "design",
  "spef": "design.spef",
  "sdc": "design.sdc"
}
```

Optional `waivers.json` alongside it lists documented modeling divergences
(`[{"endpoint_pattern": ..., "reason": ...}]`); a waiver without a reason is
rejected by the harness.

## Phase status

| Phase | Scope (DESIGN.md s9) | Status |
|---|---|---|
| 0 | Modal scaffolding, golden harness null test | this directory |
| 1 | Graph image + K1/K2(S,M)/K3 single-corner forward path | not started |
| 2 | Full GBA: K4, K2(L,XL), clocks, CPPR, reporting | not started |
| 3 | MMMC corner batching + Modal fan-out | not started |
| 4 | Incremental engine + TimerSession ECO serving | not started |
| 5 | PBA, OpenROAD integration, GB200 port | not started |
