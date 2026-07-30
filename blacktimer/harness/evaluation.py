"""GPUTimer TCAD'23 evaluation methodology, machine-readable (EVALUATION.md).

Encodes the experimental protocol of Guo/Huang/Lin, "Accelerating Static
Timing Analysis Using CPU-GPU Heterogeneous Parallelism", IEEE TCAD 2023
(https://guozz.cn/publication/gputimertcad-23/gputimertcad-23.pdf), which
BlackTimer adopts so its numbers are directly comparable to the published
ones:

  E1  single-corner full timing   -> Table I rows (one full-timing iteration,
                                     end-to-end incl. memory preparation)
  E2  incremental timing sweeps   -> runtime vs propagation candidates /
                                     nets / gates; crossover thresholds feed
                                     the HAC CalibrationTable (hac.hpp)
  E3  multi-corner analysis       -> 128 corners, batch size BC swept over
                                     {1,2,4,8,12,16} -> Table II rows

The published GPUTimer results are included as reference constants: every
BlackTimer report can show "vs OpenTimer-16T (published)", "vs GPUTimer
(published)" next to freshly measured baselines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

# E3 protocol constants (GPUTimer section IV-C).
CORNER_COUNT = 128
BC_SWEEP = (1, 2, 4, 8, 12, 16)
# E1 CPU-scalability sweep (GPUTimer Fig. 11).
CPU_SWEEP = (1, 2, 4, 8, 16, 32, 40)
# GPUTimer's published GPU/CPU crossovers, measured on A40 + PCIe. These are
# reference points only -- DESIGN.md section 3.1 requires remeasuring per
# platform; never use them as dispatch thresholds directly.
PUBLISHED_CROSSOVERS = {
    "propagation_candidates": 67_000,
    "nets": 40_000,
    "gates": 45_000,  # ~360K LUT interpolations
}


@dataclass(frozen=True)
class BenchmarkStats:
    """One TAU15-14nm design (GPUTimer Table I statistics columns)."""

    name: str
    pis: int
    pos: int
    gates: int
    nets: int
    pins: int
    nodes: int   # STA graph nodes
    edges: int   # STA graph edges


# The 15 TAU 2015 contest designs (>10K gates) re-synthesized under an
# industrial 14nm technology, exactly as evaluated in GPUTimer Table I.
TAU15_14NM: tuple[BenchmarkStats, ...] = (
    BenchmarkStats("aes_core_14nm", 260, 129, 22938, 23199, 66221, 413058, 499688),
    BenchmarkStats("vga_lcd_14nm", 89, 109, 139529, 139635, 380730, 1949332, 2636815),
    BenchmarkStats("vga_lcd_iccad_14nm", 85, 99, 259067, 259152, 662179, 3539206, 4234464),
    BenchmarkStats("b19_14nm", 22, 25, 255278, 255300, 776320, 4416480, 5623578),
    BenchmarkStats("cordic_ispd_14nm", 34, 64, 45359, 45393, 127993, 730590, 910649),
    BenchmarkStats("des_perf_ispd_14nm", 234, 140, 138878, 139112, 371587, 2095933, 2473864),
    BenchmarkStats("edit_dist_ispd_14nm", 2562, 12, 147650, 150212, 416609, 2555873, 3562491),
    BenchmarkStats("fft_ispd_14nm", 1026, 1984, 38158, 39184, 116139, 631491, 868498),
    BenchmarkStats("leon2_14nm", 615, 85, 1616369, 1616984, 4178874, 22450936, 28114268),
    BenchmarkStats("leon3mp_14nm", 254, 79, 1247725, 1247979, 3267993, 17647115, 22807349),
    BenchmarkStats("netcard_14nm", 1836, 10, 1496719, 1498555, 3901343, 21023425, 25014009),
    BenchmarkStats("mgc_edit_dist_14nm", 2562, 12, 161692, 164254, 444693, 2431266, 3355118),
    BenchmarkStats("mgc_matrix_mult_14nm", 3202, 1600, 171282, 174484, 489670, 2710343, 3415291),
    BenchmarkStats("pci_bridge32_14nm", 160, 201, 40790, 40950, 108172, 577083, 696170),
    BenchmarkStats("tip_master_14nm", 778, 857, 37715, 38493, 95524, 533690, 602224),
)

BENCHMARKS = {b.name: b for b in TAU15_14NM}

# GPUTimer Table I published runtimes, milliseconds, one full-timing
# iteration: {design: (OpenTimer 16 CPUs, GPUTimer 16 CPUs + 1 A40)}.
GPUTIMER_TABLE_I: dict[str, tuple[int, int]] = {
    "aes_core_14nm": (276, 283),
    "vga_lcd_14nm": (1368, 659),
    "vga_lcd_iccad_14nm": (2612, 951),
    "b19_14nm": (3520, 1155),
    "cordic_ispd_14nm": (508, 369),
    "des_perf_ispd_14nm": (1679, 649),
    "edit_dist_ispd_14nm": (2056, 799),
    "fft_ispd_14nm": (457, 353),
    "leon2_14nm": (23928, 5879),
    "leon3mp_14nm": (18174, 5174),
    "netcard_14nm": (21320, 5259),
    "mgc_edit_dist_14nm": (1913, 793),
    "mgc_matrix_mult_14nm": (1906, 798),
    "pci_bridge32_14nm": (404, 318),
    "tip_master_14nm": (341, 338),
}

# GPUTimer Table II published runtimes, milliseconds, full 128-corner
# analysis: {design: {BC: runtime}}. BC=1 is the single-corner engine run
# 128 times; BC=k runs 128/k batches.
GPUTIMER_TABLE_II: dict[str, dict[int, int]] = {
    "aes_core_14nm": {1: 36198, 2: 9984, 4: 6005, 8: 4896, 12: 4693, 16: 4661},
    "vga_lcd_14nm": {1: 84369, 2: 28459, 4: 18635, 8: 13611, 12: 13053, 16: 11192},
    "vga_lcd_iccad_14nm": {1: 121711, 2: 43157, 4: 28384, 8: 20672, 12: 18861, 16: 18539},
    "b19_14nm": {1: 147849, 2: 55573, 4: 36032, 8: 27067, 12: 25087, 16: 22251},
    "cordic_ispd_14nm": {1: 47241, 2: 11968, 4: 8352, 8: 6283, 12: 6208, 16: 5752},
    "des_perf_ispd_14nm": {1: 83132, 2: 25067, 4: 16277, 8: 11632, 12: 11158, 16: 9659},
    "edit_dist_ispd_14nm": {1: 102255, 2: 30784, 4: 21216, 8: 15472, 12: 14733, 16: 13275},
    "fft_ispd_14nm": {1: 45244, 2: 10837, 4: 7829, 8: 6997, 12: 6057, 16: 6091},
    "leon2_14nm": {1: 752512, 2: 306496, 4: 195424, 8: 152859, 12: 139099, 16: 133053},
    "leon3mp_14nm": {1: 662263, 2: 250795, 4: 171936, 8: 122939, 12: 110447, 16: 105013},
    "netcard_14nm": {1: 673109, 2: 276992, 4: 172576, 8: 122229, 12: 111485, 16: 106096},
    "mgc_edit_dist_14nm": {1: 101547, 2: 34069, 4: 20811, 8: 15888, 12: 14285, 16: 13523},
    "mgc_matrix_mult_14nm": {1: 102153, 2: 32000, 4: 22944, 8: 15403, 12: 14527, 16: 13608},
    "pci_bridge32_14nm": {1: 40670, 2: 10133, 4: 7275, 8: 5589, 12: 5163, 16: 4939},
    "tip_master_14nm": {1: 43221, 2: 9643, 4: 7499, 8: 5477, 12: 5265, 16: 4861},
}


@dataclass
class Platform:
    """Recorded with every measurement so runs are never silently compared
    across hardware (GPUTimer's was 40 cores @ 2.10 GHz, 512 GB, 1x A40)."""

    gpu: str
    cpus: int
    host: str = ""
    git_sha: str = ""

    def caption(self) -> str:
        sha = f" @ {self.git_sha[:12]}" if self.git_sha else ""
        return f"{self.gpu}, {self.cpus} CPUs{sha}"


@dataclass
class FullTimingRow:
    """E1 / Table I: one full-timing iteration, end-to-end (kernels +
    memory preparation), per GPUTimer section IV measurement rules."""

    benchmark: str
    ours_ms: float
    baseline_ms: float | None = None
    baseline_label: str = "OpenTimer-40T"

    @property
    def stats(self) -> BenchmarkStats:
        return BENCHMARKS[self.benchmark]

    @property
    def speedup(self) -> float | None:
        if self.baseline_ms is None:
            return None
        return self.baseline_ms / self.ours_ms

    def vs_published(self) -> tuple[float, float] | None:
        """(speedup vs published OpenTimer-16T, vs published GPUTimer)."""
        pub = GPUTIMER_TABLE_I.get(self.benchmark)
        if pub is None:
            return None
        return pub[0] / self.ours_ms, pub[1] / self.ours_ms


@dataclass
class CornerSweepRow:
    """E3 / Table II: full CORNER_COUNT-corner analysis at each BC."""

    benchmark: str
    runtime_ms: dict[int, float] = field(default_factory=dict)  # {BC: ms}

    def speedup(self, bc: int) -> float | None:
        if bc not in self.runtime_ms or 1 not in self.runtime_ms:
            return None
        return self.runtime_ms[1] / self.runtime_ms[bc]


@dataclass(frozen=True)
class IncrementalSample:
    """E2: one edit-ripple replay point, measured on both dispatch paths."""

    size: int  # propagation candidates, nets, or gates per sweep axis
    gpu_ms: float
    cpu_ms: float


def crossover(samples: Sequence[IncrementalSample]) -> int | None:
    """Smallest problem size beyond which the GPU path always wins.

    This is the measured replacement for GPUTimer's published ~67K
    propagation-candidate threshold and populates
    CalibrationTable::eco_gpu_dispatch_threshold (hac.hpp). Returns None if
    the GPU never consistently wins in the sampled range.
    """
    ordered = sorted(samples, key=lambda s: s.size)
    threshold = None
    for sample in ordered:
        if sample.gpu_ms <= sample.cpu_ms:
            if threshold is None:
                threshold = sample.size
        else:
            threshold = None  # a later CPU win invalidates the candidate
    return threshold


def _fmt_speedup(value: float | None) -> str:
    return f"{value:.2f}x" if value is not None else "-"


def format_table_i(rows: Sequence[FullTimingRow], platform: Platform) -> str:
    """GPUTimer Table I analogue, markdown."""
    label = rows[0].baseline_label if rows else "baseline"
    lines = [
        f"Table I: single-corner full timing, one iteration ({platform.caption()})",
        "",
        f"| Benchmark | Gates | Pins | Edges | {label} (ms) | Ours (ms) "
        "| Speed-up | vs OpenTimer-16T (pub) | vs GPUTimer (pub) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        s = row.stats
        pub = row.vs_published()
        base = f"{row.baseline_ms:.0f}" if row.baseline_ms is not None else "-"
        lines.append(
            f"| {row.benchmark} | {s.gates} | {s.pins} | {s.edges} "
            f"| {base} | {row.ours_ms:.0f} | {_fmt_speedup(row.speedup)} "
            f"| {_fmt_speedup(pub[0] if pub else None)} "
            f"| {_fmt_speedup(pub[1] if pub else None)} |"
        )
    return "\n".join(lines)


def format_table_ii(rows: Sequence[CornerSweepRow], platform: Platform) -> str:
    """GPUTimer Table II analogue, markdown."""
    lines = [
        f"Table II: {CORNER_COUNT}-corner analysis vs corner batch size BC "
        f"({platform.caption()})",
        "",
        "| Benchmark | " + " | ".join(
            f"BC={bc} (ms / speed-up)" for bc in BC_SWEEP) + " |",
        "|---|" + "---|" * len(BC_SWEEP),
    ]
    for row in rows:
        cells = []
        for bc in BC_SWEEP:
            ms = row.runtime_ms.get(bc)
            cells.append(
                f"{ms:.0f} / {_fmt_speedup(row.speedup(bc))}"
                if ms is not None else "-"
            )
        lines.append(f"| {row.benchmark} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
