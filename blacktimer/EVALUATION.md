# BlackTimer Evaluation Methodology

Adopted from GPUTimer (Guo, Huang, Lin, *"Accelerating Static Timing Analysis
Using CPU–GPU Heterogeneous Parallelism"*, IEEE TCAD 2023,
https://guozz.cn/publication/gputimertcad-23/gputimertcad-23.pdf), the
baseline reference of DESIGN.md. We reproduce its experimental protocol so
BlackTimer's numbers are directly comparable with the published ones, then
extend it where DESIGN.md sets stronger targets (OpenTimer-40T baseline,
OpenSTA golden diff, cost accounting on Modal).

The machine-readable version of everything below — the benchmark manifest,
the published reference tables, the result-row schemas, and the crossover
extraction — lives in `harness/evaluation.py`; `bench()` in `modal_app.py`
emits rows in those schemas.

## 1. Benchmarks

The TAU 2015 contest netlists, re-synthesized under an industrial 14nm
technology with multi-corner cell libraries (voltage 0.66–0.99 V,
temperature −40 °C to 125 °C, ff/ss/tt process corners) — the same 15
designs with >10K gates GPUTimer evaluated, from `aes_core` (23K gates) to
`leon2` (1.6M gates, 22.5M STA-graph nodes, 28.1M edges). Full statistics
(PIs, POs, gates, nets, pins, graph nodes, graph edges) per design are in
`harness/evaluation.py::TAU15_14NM`. Where the 14nm re-synthesis is not
reproducible, the original TAU15 releases are used and flagged as such in
reports — cross-paper comparisons then hold only qualitatively.

## 2. Platform and measurement rules

GPUTimer's rules, kept verbatim:

- **End-to-end runtime**: every measurement includes GPU kernel time AND
  memory preparation/transfer, never kernel time alone.
- **One iteration of full timing** is the unit of measurement for full-graph
  experiments (parse time excluded, reported separately).
- Baselines run at **maximum hardware concurrency**; scalability sweeps vary
  CPU count over {1, 2, 4, 8, 16, 32, 40}.

GPUTimer's platform was 40 CPU cores @ 2.10 GHz, 512 GB RAM, 1× NVIDIA A40,
128 threads/block. BlackTimer's is a Modal B200 container (§3.1 of
DESIGN.md); every report records the platform tuple so A40-era numbers are
never silently compared against B200 runs.

## 3. Experiments

### E1 — Single-corner full timing (GPUTimer Table I)

One full-timing iteration per design. Row schema: benchmark statistics,
baseline runtime, our runtime, speed-up. Baselines, in order of authority:

1. OpenTimer with 40 threads (DESIGN.md §2.2 target: ≥10×),
2. OpenTimer with 16 threads (GPUTimer's Table I configuration, for direct
   comparison with its published 0.98×–4.07×),
3. published GPUTimer runtimes themselves (are we beating the prior GPU
   engine, not just the CPU?).

Accompanied by GPUTimer's Fig. 10 breakdown: per-stage runtime (levelization,
RC update, graph/propagation timing) for every item >1000 ms on the largest
designs, and its Fig. 11 CPU-scalability sweep (our runtime vs baseline
runtime at 1–40 CPUs on leon2 and netcard).

### E2 — Single-corner incremental timing (GPUTimer Figs. 12–14)

Runtime vs problem size, where problem size is measured three ways, each
producing a GPU-vs-CPU crossover threshold:

| Sweep axis | GPUTimer's published crossover (A40/PCIe) |
|---|---|
| propagation candidates (union of fan-in/fan-out cones of frontier pins) | ~67K |
| nets (RC computation) | ~40K |
| gates ≈ LUT interpolations | ~45K gates ≈ 360K LUTs |

Protocol: replay edit ripples of increasing size on leon2 and netcard,
measure GPU path and CPU path at each size, extract the smallest size beyond
which the GPU path always wins (`harness/evaluation.py::crossover`). These
measured thresholds — not the published A40 numbers — populate the
`CalibrationTable` of `engine/include/blacktimer/hac.hpp` (DESIGN.md §3.1:
the 67K figure is a PCIe-era artifact and must be remeasured per platform).
Below-threshold regressions must stay within GPUTimer's observed penalty
envelope (<80 ms difference under 10K candidates). DESIGN.md §2.2 adds the
absolute gate: <10 ms per update at the 95th percentile for ripples under
50K pins, evaluated by replaying an OpenROAD gate-sizing trace (Phase 4).

### E3 — Multi-corner analysis (GPUTimer Table II, Fig. 15)

A **128-corner** timing analysis per design, computed as 128/BC batches with
corner batch size BC swept over {1, 2, 4, 8, 12, 16} (BC=1 = the
single-corner engine run 128 times). Reported per design: runtime and
speed-up relative to BC=1. GPUTimer saturates at BC=16 with 5.66×–6.34× on
the three largest designs (published full table in
`harness/evaluation.py::GPUTIMER_TABLE_II`); combined with Table I this is
22.14×–25.67× vs repeated OpenTimer, against which DESIGN.md's ≥50× on 16
corners is measured. BlackTimer extends the sweep with the Modal fan-out
axis: N containers × BC corners each, reporting wall clock **and dollar
cost** per sweep (DESIGN.md §9 cost model), which has no GPUTimer analogue.

### E4 — Correctness (BlackTimer addition)

GPUTimer validated against OpenTimer's results; DESIGN.md §2.2/§8 sets a
stricter bar that gates every experiment above: endpoint-by-endpoint slack
agreement with OpenSTA within 1 ps (or 0.1% relative) via
`harness/golden_diff.py`, plus 5× repeated runs requiring bit-identical
s32-ps results. A performance number from a run whose golden diff is not
clean is invalid and must not be reported.

## 4. Reporting

Tables I/II analogues are emitted as markdown by
`harness/evaluation.py::format_table_i / format_table_ii`, one row per
design, with the platform tuple and git SHA in the caption. Sweep data
(E1 scalability, E2 crossovers, E3 BC curves) is stored as JSON rows on the
`blacktimer-designs` volume next to the NCU profiles (DESIGN.md §4.1) so
nightly runs accumulate a comparable history.
