# BlackTimer: A GPU-Native Static Timing Analysis Engine
## Design Document & Implementation Plan — Blackwell-class GPUs, Modal Labs runtime

**Status:** Draft v0.1 · **Target hardware:** NVIDIA B200 (Modal) → GB200 NVL72 (production) · **Baseline references:** OpenSTA, OpenTimer, GPUTimer (Guo/Huang/Lin, TCAD'23)

---

## 1. Overview

BlackTimer is a from-scratch GPU-native static timing analysis engine intended to serve as a drop-in replacement timer for OpenSTA and OpenTimer within OpenROAD-style flows. It generalizes the GPUTimer (TCAD'23) architecture — GPU levelization, flattened RC-tree Elmore delay, batched multi-corner propagation, CPU-GPU task scheduling — to Blackwell-class hardware, and packages the entire build, test, benchmark, and multi-corner scale-out pipeline as serverless functions on Modal Labs.

The central bet: full-graph STA is memory-bandwidth-bound, and Blackwell's 8 TB/s HBM3e (roughly 11x the A40 GPUTimer was evaluated on) plus 180+ GB of device memory means a 100M-gate flat design fits and sweeps on a single GPU, while corners, modes, and what-if scenarios scale horizontally across ephemeral Modal containers rather than across a statically provisioned cluster.

## 2. Requirements

### 2.1 Functional

The engine must perform graph-based STA (GBA) with arrival/required/slack computation across the four timing conditions (early/late x rise/fall), NLDM lookup-table delay models with Elmore/D2M interconnect delay from SPEF parasitics, multi-corner multi-mode (MMMC) analysis, incremental timing after netlist edits (ECO loop), clock propagation with generated clocks, and common path pessimism removal (CPPR). Path-based analysis (PBA) for top-K critical path reporting follows in a later phase. Input formats: structural Verilog, Liberty (.lib), SPEF, SDC. The Tcl command surface of OpenSTA (`report_checks`, `report_wns`, `set_input_delay`, ...) must be preserved for flow compatibility, and the OpenTimer C++ incremental API (`insert_gate`, `repower_gate`, `update_timing`) must be preserved for programmatic callers such as placers and sizers.

### 2.2 Non-functional

Performance targets, stated against OpenTimer with 40 CPU threads on the TAU/leon-class benchmarks GPUTimer used: at least 10x on single-corner full timing, at least 50x on 16-corner MMMC, and incremental update latency under 10 ms for ECO ripples touching fewer than ~50K pins. Correctness target: slack agreement with OpenSTA within 1 ps (or 0.1% relative) on all endpoints across the TAU 2015/2019 contest suites; any divergence must be triaged to a documented modeling difference, never an unexplained one. Capacity target: 100M-gate flat designs within a single B200's 180 GB usable HBM. Cost target: a full regression sweep (parse + full timing + 16 corners + report, all TAU benchmarks) under $10 of Modal compute per run so it can gate every merge.

### 2.3 Constraints

Team-scale project (assume 1–3 engineers), so we buy rather than build wherever a mature component exists: OpenSTA's Liberty/SDC/Verilog readers are reused via liberty parsing extraction rather than rewriting parsers; Taskflow is reused for host-side task graphs; CUDA C++ (not a portability layer like SYCL) since the deployment target is exclusively NVIDIA. Modal imposes its own constraints, covered in §4.

## 3. Hardware model and the Modal portability layer

### 3.1 The two deployment targets

| Property | Modal B200 (dev/CI/scale-out) | GB200 node (production) |
|---|---|---|
| Host CPU | x86, container vCPUs | 72-core Grace (Neoverse V2) |
| CPU↔GPU link | PCIe Gen5 (~64 GB/s) | NVLink-C2C, ~900 GB/s, cache-coherent |
| GPU memory | 192 GB HBM3e (≈180 usable) | Same per GPU, 2 GPUs per superchip |
| GPU↔GPU | NVLink 5 within a node | NVL72 domain: 72 GPUs, 1.8 TB/s each |
| Provisioning | Serverless, per-second, scale-to-zero | Reserved instances |

The GB200 design assumptions from the earlier architecture sketch — unified coherent memory, near-free CPU/GPU boundary crossing, Grace-side parallel parsing feeding the GPU — do not hold on Modal. Rather than maintain two engines, we define a Hardware Abstraction Contract (HAC) that the core engine codes against:

1. **Residency rule.** The timing graph, RC arrays, and all per-pin state live in device memory, period. The host never dereferences them. On GB200 this is trivially satisfied by coherence; on Modal/PCIe it is satisfied by construction.
2. **Ingest channel.** Parsing produces a stream of packed, position-independent binary segments (the "graph image", §5.4) pushed via `cudaMemcpyAsync` on Modal and via coherent writes on GB200, behind one `IngestChannel` interface.
3. **Command channel.** Incremental edits and timing queries cross the boundary as small fixed-format command/result buffers (kilobytes), so PCIe latency affects only round-trip count, never bulk bandwidth. The dispatch heuristic (GPU vs CPU path for tiny ECOs) reads its thresholds from a calibration table measured per platform at startup, not from constants — GPUTimer's ~67K-candidate crossover was a PCIe-era artifact and will differ on each target.

Everything below the HAC is identical on both platforms. This is the single most important design decision in the document: it lets Modal's cheap per-second B200s serve as the entire development, CI, and benchmarking substrate while keeping the GB200 fast path honest.

### 3.2 What Blackwell specifically buys us

Three microarchitectural features are load-bearing. First, HBM3e bandwidth (8 TB/s) directly scales the bandwidth-bound kernels — propagation and RC scans — which is most of the runtime. Second, DPX instructions accelerate the min/max-plus arithmetic that arrival/required propagation reduces to (§6.3). Third, the large L2 (126 MB) plus distributed shared memory make persistent-kernel levelization viable: frontier queues stay cache-resident across virtual "supersteps" without kernel relaunch (§6.1). Thread-block clusters are used in the RC scan kernels for large nets whose flattened trees exceed one block's shared memory.

## 4. Modal runtime architecture

### 4.1 Application topology

The project is one Modal App with a small set of function families:

```
modal app: blacktimer
├── image: blacktimer-cuda          # CUDA 12.x toolkit, cmake, ninja, OpenSTA
│                                   #   deps, Python bindings (nanobind)
├── volume: designs                 # benchmark suites: TAU15/19, leon2/3,
│                                   #   netcard, vga_lcd + golden results
├── volume: build-cache             # ccache + compiled fatbins keyed by
│                                   #   git SHA to avoid nvcc cold starts
├── fn build()            [CPU]     # compile engine, produce wheel + fatbin
├── fn unit_tests()       [B200]    # kernel-level tests (GTest via pytest)
├── fn full_sta(design, corners)    [B200]     # one end-to-end run
├── fn mmmc_sweep(design, corner_list) [B200 fan-out]  # map over corners
├── fn golden_diff(design)          [CPU, high-mem]    # OpenSTA reference
├── fn bench(design, config)        [B200]    # perf harness, NCU capture
└── cls TimerSession                [B200, memory_snapshot] # long-lived
                                    #   incremental server: load once,
                                    #   accept ECO/query RPCs
```

Key Modal mechanics exploited:

- **Per-second B200 billing (~$6.25/hr)** means a 90-second full-timing regression costs about 16 cents; the CI gate in §9 budgets accordingly.
- **`@app.cls` with container lifecycle hooks** implements the incremental server: `@modal.enter()` loads the design and runs full timing once; subsequent `.remote()` method calls apply ECO deltas against the warm GPU state. Modal's memory snapshotting shortens cold starts for the CPU-side state; GPU state is rebuilt from the graph image on restore (device memory is not snapshotted), which the graph-image format makes a bulk-bandwidth-bound reload rather than a re-parse.
- **Fan-out via `.map()`/`.starmap()`** implements MMMC: since corners share topology, the coordinator function computes the graph image once, writes it to the volume, and maps corner batches over N ephemeral B200 containers. This substitutes Modal's horizontal elasticity for the NVL72 domain — coarser-grained (no NVLink between containers) but corners are embarrassingly parallel, so the only shared traffic is the one-time graph image read, served from the volume.
- **Multi-GPU containers** (`gpu="B200:8"`) are reserved for the cases that genuinely need NVLink: designs exceeding 180 GB of timing state, and the PBA path-enumeration phase where work stealing across GPUs pays.
- **NCU/Nsight profiles** are captured inside `bench()` and written to the volume; Modal containers permit the CUDA profiling counters needed with the right image configuration.

### 4.2 What Modal cannot do (and the mitigation)

No Grace, no NVLink-C2C, no NVL72 fabric, and containers are ephemeral. Mitigations, respectively: the HAC (§3.1) keeps the engine agnostic; corner scale-out replaces fabric scale-out; and the graph-image format makes state reconstruction cheap enough (<2 s for 100M gates at PCIe Gen5 rates) that ephemerality is a non-issue for batch work, while the `TimerSession` class keeps containers warm for interactive/ECO use with an idle timeout.

## 5. Data model

### 5.1 Timing graph

Structure-of-arrays throughout. Pins are the vertices; timing arcs (cell arcs and net arcs) are the edges, stored in forward CSR and reverse CSR (both are needed: forward for AT propagation, reverse for RAT propagation and for the in-degree-balanced levelization trick from GPUTimer — reversed edges bound fan-in at the cell's input count, avoiding the 260-fanout skew of clock nets).

Per-pin timing state is a 4-vector over conditions (early/late x rise/fall); per-arc delay/slew tables are per condition per corner. The innermost memory index is condition, then corner-within-batch, exactly the interleaving GPUTimer validated for coalescing: thread t and thread t+1 in a warp touch adjacent conditions/corners of the same arc, so a warp's accesses form one contiguous segment.

```
at[pin][corner_batch][cond]      f32   4 * BC per pin
slew[pin][corner_batch][cond]    f32
rat[pin][corner_batch][cond]     f32
arc_delay[arc][corner_batch][cond] f32
csr_fwd:  row_ptr[pins+1], col[arcs], arc_id[arcs]
csr_rev:  row_ptr[pins+1], col[arcs], arc_id[arcs]
level[pin] u16, in_deg[pin] u8 (atomic working copy per pass)
```

### 5.2 RC forests

Every net's RC tree is flattened, per GPUTimer, into BFS order with a parent-index array, so all four Elmore passes become linear scans (two forward, two backward) with only parent[] indirection. All nets are packed into one global arena with a per-net offset table, and nets are binned by node count (≤32, ≤256, ≤4K, >4K) at build time; each bin gets a differently-shaped kernel (§6.2).

### 5.3 Liberty tables

NLDM 2-D LUTs (delay and output slew vs. input slew and output load) are deduplicated across cells (libraries share template axes), stored as a template table (axis vectors) plus per-arc value grids in texture-friendly layout. Interpolation is bilinear; axis binary search is replaced by a precomputed monotone bucket index since axes are tiny (7x7 typical).

### 5.4 The graph image

All of the above serializes to a single position-independent binary blob — offsets, not pointers — versioned and checksummed. It is the unit of ingest (§3.1), of Modal fan-out (written once to the volume, mapped by corner workers), and of session restore. Building it is the CPU's main job; on Modal that is the container's vCPUs running the reused OpenSTA readers plus a parallel SPEF chunker, on GB200 it is Grace's 72 cores doing the same with the ingest channel degenerating to coherent stores.

### 5.5 Memory budget (100M-gate design, ~140M pins, ~180M arcs, BC=4 corner batch)

| Component | Bytes/unit | Total |
|---|---|---|
| AT + slew + RAT (3 arrays x 4 cond x BC=4 x f32) | 192 B/pin | 26.9 GB |
| Arc delays + arc slews (2 x 4 x 4 x f32) | 128 B/arc | 23.0 GB |
| CSR fwd + rev (ptr + col + arc_id) | ~26 B/arc + 8 B/pin | 5.8 GB |
| RC arena (~500M RC nodes, cap/res/parent/scratch) | ~48 B/node | 24.0 GB |
| Liberty value grids (dedup'd) | — | ~2 GB |
| Levelization, frontiers, scratch, CPPR tags | — | ~8 GB |
| **Total** | | **~90 GB** |

Comfortable within 180 GB with headroom for BC=8 or a second scenario resident simultaneously. A 10M-gate design (leon2-class x6) is under 10 GB, meaning CI-scale benchmarks never pressure memory at all.

## 6. Kernel specifications

### 6.1 K1 — Levelization (persistent cooperative kernel)

GPUTimer profiled levelization at ~42% of CPU runtime and moved it to a per-level launch loop; on Blackwell we go one step further and fuse the whole loop into a single persistent cooperative kernel to eliminate per-level launch latency, which dominates when levels are shallow-but-many (deep pipelines produce thousands of levels with small frontiers).

Algorithm: reverse-CSR frontier advance. The frontier holds pins whose in-degree (in the reversed graph, i.e., fan-in) has reached zero. Each superstep: threads cooperatively advance over the frontier's out-edges, `atomicSub` the successor's in-degree counter, and append successors that hit zero to the next-frontier queue (warp-aggregated atomics for the append). `grid.sync()` separates supersteps. Frontier queues are double-buffered in global memory but sized to stay L2-resident for typical frontiers. Load balancing: because we advance over the *reversed* graph, out-degree in the traversal equals cell fan-in (bounded ~8), so a simple thread-per-frontier-vertex mapping is balanced without Gunrock-style merge-path partitioning; the pathological direction (clock fanout 260+) never appears on the advance side. Output: `level[pin]` plus a level-sorted pin permutation used by K3.

Incremental variant: on an ECO, only the affected cone is re-levelized. Damage tracking (§7) yields a seed frontier; levels downstream are recomputed with the same kernel over the induced subgraph, and if no level value actually changes past a fence, propagation halts early.

### 6.2 K2 — RC delay (binned, warp-per-net Elmore/D2M)

Four scans per net per condition per corner: downstream cap (backward), delay (forward), downstream cap-x-delay (backward), impulse/beta (forward). Binning by net size selects the mapping:

- Bin S (≤32 nodes, ~85% of nets): one *thread* per (net, cond, corner) — the GPUTimer mapping — since the whole tree fits in registers/L1 and parallelism across nets is abundant.
- Bin M (≤256): one *warp* per (net, cond); the scan is a warp-cooperative segmented scan over BFS order using shuffle intrinsics; corners vectorized in the innermost loop.
- Bin L (≤4K): one thread block per net, tree staged in shared memory, block-scan.
- Bin XL (>4K, clock trees and top-level routes): thread-block cluster per net using distributed shared memory; these are few but individually deep, and they sit on the critical path of the timing update, so they are launched *first* in their own stream while bins S–M fill the machine behind them.

Accuracy tiering: Elmore is computed everywhere; a second pass upgrades near-critical nets (slack within a configurable guard band after a first-cut propagation) to D2M, and the hook is left for a CCS/current-source recompute in a later phase. The tiering decision list is produced on-GPU by a slack filter, so no host round trip.

### 6.3 K3 — Arrival/slew forward propagation (level-synchronous max-plus sweep)

Pins are processed in level order using the permutation from K1: one kernel launch per *level band* (contiguous levels whose total pin count exceeds an occupancy threshold are fused into one launch with an intra-kernel dependency check; tiny levels are batched). For each pin, threads reduce over its reverse-CSR fan-in: candidate AT = predecessor AT + arc delay, taking max for late / min for early — a (max,+)/(min,+) semiring reduction that compiles to DPX `__vimax3_s32` family ops on Blackwell when quantized to integer picoseconds (we carry f32 and s32-ps side by side; s32-ps is the comparison type, f32 the reporting type, which also makes results bit-deterministic across runs — floating-point max is order-stable, but the fused slew-merge arithmetic is not, and determinism is a hard requirement for golden diffing).

Slew propagation and worst-driver bookkeeping fuse into the same kernel (one pass, one read of the arc tables). Cell arc delays are computed inline: the LUT bilinear interpolation (§5.3) happens at the moment the arc is relaxed, since input slew is only known then — this is the GPUTimer "compute delay during propagation for cell arcs, precompute for net arcs" split, preserved.

### 6.4 K4 — Required-time backward propagation

Symmetric to K3 over forward CSR in reverse level order, min-plus for late RAT / max-plus for early. GPUTimer left this on the CPU because it was cheap; we move it to GPU anyway because on the incremental path the *transfer* of ATs back to host to run a CPU backward pass would cost more than the kernel, and because slack (`rat - at`) then materializes on-device where the K2 accuracy-tiering filter and the reporting top-K selection want it.

### 6.5 K5 — CPPR

Credit computation via on-GPU nearest-common-ancestor over the clock tree: clock tree paths are stored as jump-pointer (Euler tour + sparse table) structures built once per clock network, giving O(1) LCA per endpoint pair on GPU. CPPR-corrected slacks are computed lazily for the endpoint set requested by reporting, not eagerly for all endpoints, matching OpenSTA semantics.

### 6.6 K6 — Reporting / top-K path extraction (Phase 5)

GBA worst-slack and per-endpoint histograms are trivial reductions. PBA top-K path enumeration adopts the implicit-path-representation approach of the GPU-PBA literature (Guo et al., DAC'21): a GPU priority-queue-free K-best search over deviation edges, with per-GPU work stealing when running on `B200:8`.

## 7. Incremental timing (the ECO loop)

The engine maintains a damage set on device: netlist edit commands (repower, resize, buffer insert, net reroute → new SPEF fragment) arrive over the command channel as compact records, are applied to the graph overlay (freed slots + append arenas; full graph-image rebuild is an offline compaction, triggered when fragmentation exceeds a threshold), and mark seed pins. `update_timing` then runs: incremental K1 from seeds → K2 on damaged nets only → K3/K4 over the affected cone with early termination when AT/RAT deltas underflow a picosecond epsilon. The platform calibration table (§3.1) decides whether a microscopic ripple (a handful of pins) runs on the host mirror instead; on Modal/PCIe this CPU path matters, on GB200 it may never win.

Latency budget for a 10K-pin ripple on B200: sub-millisecond kernel time; the bound is command-channel round trips, so the API batches edit lists per `update_timing` call, matching how placers already use OpenTimer.

## 8. Compatibility and verification

Frontend: OpenSTA's Tcl interpreter and readers are embedded; its `dbSta`/`Sta` interfaces are implemented against BlackTimer's query API so OpenROAD tools link unchanged. A `set_timing_engine cpu|gpu` escape hatch keeps the original OpenSTA graph engine alive in-process as the golden reference.

Verification is continuous, not a phase: every CI run executes `golden_diff()` — full OpenSTA on CPU, full BlackTimer on GPU, endpoint-by-endpoint slack/AT/slew/RAT comparison with a 1 ps gate — across the TAU 2015/2019 suites and the leon/netcard/vga designs. Known modeling divergences (e.g., slew merge policy differences) are encoded as explicit waivers with comments, so the diff is always expected-clean. Kernel-level unit tests pin down each kernel against a NumPy reference implementation of the same math (the NumPy model is itself diffed against OpenSTA once). Determinism tests run each design 5x and require bit-identical s32-ps results.

## 9. Implementation plan on Modal

### Phase 0 — Scaffolding (2 weeks)
Modal app, CUDA image with pinned toolkit, build-cache volume, ccache wiring; vendored OpenSTA building inside the image; TAU/leon benchmark suites + OpenSTA golden results generated once into the designs volume; `golden_diff` harness running end-to-end with the *CPU* engine on both sides (a null test proving the harness). Exit: green null-diff CI on every push, <5 min wall clock.

### Phase 1 — Graph image + single-corner forward path (6 weeks)
Graph-image builder from OpenSTA's parsed model; K1 levelization (per-level launch version first, persistent version behind a flag); K2 bins S/M with Elmore; K3 forward propagation with inline NLDM interpolation; ATs diffed against golden. Exit: AT agreement ≤1 ps on all TAU designs; first `bench()` numbers vs. OpenTimer-40T recorded on B200.

### Phase 2 — Full GBA (5 weeks)
K4 RAT/slack on GPU, K2 bins L/XL + accuracy tiering, clock propagation and generated clocks, K5 CPPR, `report_checks`-compatible GBA reporting. Exit: full slack golden-diff clean; single-corner target (≥10x vs OpenTimer-40T) demonstrated on leon2-class designs; NCU roofline analysis in the volume showing K2/K3 within 70% of bandwidth bound.

### Phase 3 — MMMC scale-out (3 weeks)
Corner batching (BC up to 8) inside one GPU; `mmmc_sweep` Modal fan-out mapping corner batches over containers reading the shared graph image; cost/perf tuning of container count vs. BC. Exit: ≥50x on 16 corners vs OpenTimer; a 64-corner sweep of leon2 completing in under 2 minutes wall clock and under $2.

### Phase 4 — Incremental engine + TimerSession (5 weeks)
Graph overlay + damage tracking, incremental K1–K4, command channel, OpenTimer-API shim, Modal `TimerSession` class with warm-container ECO serving and calibration-table dispatch. Exit: replay of an OpenROAD gate-sizing trace (edit/update/query log) with per-update latency <10 ms at the 95th percentile and slack agreement maintained throughout the replay.

### Phase 5 — PBA, polish, GB200 port (6 weeks)
K6 top-K PBA on `B200:8`; OpenROAD `dbSta` integration branch; the GB200 port itself — implement the coherent-memory IngestChannel/command-channel variants, re-run the calibration, benchmark on a reserved GB200 node (CoreWeave or Azure ND GB200-v6) and publish the Modal-vs-GB200 delta. Exit: OpenROAD flow (floorplan→CTS→route on a public design) runs end-to-end with BlackTimer as the timer.

Total: ~27 engineer-weeks of critical path; phases 3 and 4 can overlap with two engineers.

### CI/cost model
Per-merge gate: build (CPU, cached) + unit tests + golden diff on 3 small designs on one B200 ≈ 4 GPU-minutes ≈ $0.42. Nightly: full suite + bench + 16-corner sweep ≈ 45 GPU-minutes ≈ $4.70. A month of active development lands around $300–500 of Modal spend — negligible against the alternative of a reserved Blackwell box.

## 10. Risks and trade-offs

The largest technical risk is correctness surface, not speed: STA semantics (slew merging, clock reconvergence, CPPR corner cases, exceptions like multicycle/false paths) are where GPU timers historically diverge from signoff. Mitigation is the always-on golden diff and deferring exception handling to explicit phase gates rather than sprinkling it in. Second risk: the persistent cooperative kernel requires all blocks co-resident (cooperative launch), capping grid size; if frontiers ever exceed what a co-resident grid sustains, we fall back to the per-level launch path, which is why both are kept behind a flag. Third: Modal containers lack NVLink between them, so if MMMC corners ever stop being embarrassingly parallel (e.g., shared CCS waveform caches), the fan-out model weakens — the mitigation is that such coupling lives inside a multi-GPU container instead. Fourth: reusing OpenSTA's readers couples us to its parsing performance; the parallel SPEF chunker is scoped as a replaceable module precisely because parse time will dominate wall clock at 100M gates. Finally, the trade of developing on B200-without-Grace means the GB200-specific wins (coherent incremental editing, Grace parse bandwidth) are validated last; the HAC keeps this a port, not a rewrite, but the §2.2 latency targets are only *proven* on GB200 in Phase 5.

## 11. What we'd revisit at scale

If designs exceed single-GPU memory: graph partitioning across an NVL72 domain with halo exchange at partition-boundary pins (the level structure makes the exchange schedule static). If CCS/waveform accuracy becomes mandatory: the K2 tiering hook becomes a first-class waveform engine, likely the new dominant cost, and the FP4/FP8 tensor path becomes interesting for batched waveform convolution. If the ECO loop becomes the product (timing-driven placement inner loop): move the placer's cost model on-device too and eliminate the command channel from the inner loop entirely.
