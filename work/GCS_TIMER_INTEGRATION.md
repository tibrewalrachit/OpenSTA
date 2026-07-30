# GCS-Timer integration: CCS-model GPU timing next to OpenSTA

**What it is.** [GCS-Timer](https://github.com/cuhk-eda/GCS-Timer)
(Lin/Guo/Huang/Sheng/Young/Wong, DAC 2024, BSD-3) is a GPU-accelerated
timer built on the **Composite Current Source (CCS)** model — the
advanced-node model class where NLDM's single-capacitor receiver breaks
down. It does simulation-based CCS analysis on GPU rather than model-order
reduction, and the paper reports **3.2× faster than a 16-thread industrial
signoff timer with better stage-delay accuracy vs HSPICE** on the four
largest EPFL arithmetic circuits (up to 200K gates), synthesized on ASAP7.
It is, to the authors' knowledge and ours, the only open-source CCS STA
engine — OpenSTA, OpenTimer, and BlackTimer Phase 0–2 are all NLDM-based.

**Can we use it for STA?** Yes, with a scoped role: it is not a general
STA frontend (fixed to its four shipped benchmarks + the ASAP7 RVT_TT CCS
libraries; no SDC/clocking — combinational GBA arrivals), but it is exactly
the right engine for quantifying and closing the NLDM↔CCS accuracy gap,
and the natural reference implementation for BlackTimer's CCS phase
(DESIGN.md §6.2 accuracy-tiering hook, §11 waveform engine). It is now
wired into the `blacktimer` Modal app:

```sh
modal run blacktimer/modal_app.py::seed_gcs          # once: sources + ASAP7 CCS libs + nvcc build
modal run blacktimer/modal_app.py::gcs --design mul               # GBA on B200
modal run blacktimer/modal_app.py::gcs --design mul --evaluate    # paper's accuracy repro
modal run blacktimer/modal_app.py::gcs --design mul --cpu         # CPU reference path
```

`seed_gcs` fetches everything (GCS-Timer, the 4 CCS libs out of
asap7sc7p5t_28, ~340 MB extracted) into the designs volume and compiles
two variants with sm_100 (B200) + compute_90 PTX fallback. The `--evaluate`
run reproduces the paper's stage-delay accuracy table against the
**PrimeTime and HSPICE reference results that ship in the repo** — no
commercial license needed. Like all Modal runs, execute locally or via the
GitHub Actions modal-gate (gRPC does not traverse this session's proxy).

## Measured locally: the NLDM↔CCS gap on the same benchmarks

We ran this OpenSTA (NLDM, ASAP7 RVT_TT nldm libs, same netlists/SPEF,
same 20 ps primary-input slew GCS-Timer uses) and compared output-port
arrivals against the PrimeTime **CCS** GBA references (`test.pt`) shipped
with each benchmark:

| Design | Gates (paper) | Outputs | OpenSTA parse (ms) | OpenSTA timing (ms) | mean err vs PT-CCS | p95 | max |
|---|---|---|---|---|---|---|---|
| mul | 27K | 128 | 1,714 | 3,004 | **+7.4%** | 9.3% | 13.0% |
| log2 | 32K | 32 | 2,236 | 3,482 | **+7.6%** | 7.8% | 7.9% |
| div | 57K | 128 | 6,035 | 9,043 | **+7.9%** | 8.5% | 8.5% |
| hyp | 200K | 128 | 13,417 | 20,032 | **+4.8%** | 5.6% | 5.8% |

The signed error is uniformly positive: **NLDM (OpenSTA) is consistently
4.8–7.9% pessimistic against CCS (PrimeTime) at ASAP7**, with a tight
spread — a systematic model gap, not noise. This is the gap CCS analysis
exists to close, GCS-Timer's raison d'être, and the measured motivation
for BlackTimer's CCS upgrade tier. (GCS-Timer's own GBA arrivals can be
compared to the same `test.pt` on a B200 via the `::gcs` entrypoint; the
paper's Table 1 puts its stage-delay error below the baseline timer's
against HSPICE.)

Tooling: `work/gcs/gcs_sta.tcl` (OpenSTA leg), `work/gcs/gcs_correlate.py`
(arrival comparison), reproducible against a GCS-Timer checkout.

## About "use 3nm PDK"

**There is no public 3 nm PDK.** TSMC N3/N3E and Samsung SF3 design kits
(and any CCS libraries characterized on them) are NDA-only; no open or
predictive 3 nm PDK with timing libraries exists as of mid-2026. The
options, honestly stated:

1. **ASAP7 (used here and by the GCS-Timer paper)** — the most advanced
   *open* PDK with production-grade CCS characterization (7 nm predictive,
   FinFET). This is what every published open CCS work evaluates on.
2. **Your foundry libraries** — the entire flow above is node-agnostic:
   OpenSTA consumes any Liberty/SPEF, and GCS-Timer consumes CCS `.lib`
   files (its Liberty parser is tuned to the ASAP7 dialect, so expect
   parser work for foundry libs — same class of issue as documented for
   OpenTimer's frontend in `work/CORRELATION_REPORT.md` §4.3). If you can
   place 3 nm CCS libs + a netlist + SPEF into the `blacktimer-designs`
   volume (they must never enter this git repo), the same three
   entrypoints run unchanged.
3. Predictive academic 3 nm models (e.g. ASU/TSMC-N3-like scaling of
   ASAP7) have **no CCS tables** and would silently degrade the comparison
   to NLDM-only — not offered here for that reason.

At advanced nodes the CCS-vs-NLDM divergence measured above only grows
(receiver Miller effect, resistive-shielding nonlinearity), which is the
technical argument for running CCS on GPU at 3 nm rather than NLDM
anywhere.

## Relation to BlackTimer

GPUTimer (TCAD'23) is BlackTimer's architecture baseline for **NLDM
graph-based STA at scale**; GCS-Timer is the reference point for the
**accuracy ceiling** (per-stage CCS simulation on GPU). BlackTimer's K2
accuracy-tiering design (Elmore → D2M → "CCS hook") now has a concrete
target for the third tier, an open implementation to diff against, and a
measured 5–8% arrival gap that the tier upgrade must close on near-critical
paths. The golden-diff protocol stays OpenSTA-referenced for NLDM phases;
CCS-phase correctness will need a GCS-Timer/HSPICE-deck reference instead,
exactly as this repo's EVALUATION.md E4 anticipates for "documented
modeling differences."
