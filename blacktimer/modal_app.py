"""BlackTimer Modal application (DESIGN.md section 4).

Phase 0 scaffolding: the app topology -- image, volumes, and the function
families of section 4.1 -- is real and runnable today, with the CPU engine
(vendored OpenSTA, built inside the image from this repository) standing in
for the GPU engine everywhere a kernel will eventually run. `golden_diff`
therefore executes as the null test from section 9 Phase 0: OpenSTA on both
sides, proving the harness end to end before any CUDA lands.

Usage (requires a Modal account and `pip install modal`):

    modal run blacktimer/modal_app.py::seed_designs   # once: upload smoke designs
    modal run blacktimer/modal_app.py::ci             # the per-merge gate
    modal run blacktimer/modal_app.py::diff --design gcd_sky130hd
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import modal

REPO_ROOT = Path(__file__).resolve().parent.parent

app = modal.App("blacktimer")

# ---------------------------------------------------------------------------
# Image: CUDA toolkit + OpenSTA deps + vendored OpenSTA built from this repo.
# Mirrors Dockerfile.ubuntu22.04 at the repo root, on a CUDA devel base so
# Phase 1 kernels compile in the same image.
# ---------------------------------------------------------------------------
image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11"
    )
    .apt_install(
        "git", "wget", "cmake", "ninja-build", "gcc", "g++", "gdb",
        "tcl-dev", "tcl-tclreadline", "swig", "bison", "flex",
        "automake", "autotools-dev", "libgtest-dev", "libeigen3-dev",
        "libfmt-dev", "ccache", "zlib1g-dev",
    )
    .run_commands(
        # CUDD (BDD package OpenSTA links against)
        "wget -q https://raw.githubusercontent.com/davidkebo/cudd/main/"
        "cudd_versions/cudd-3.0.0.tar.gz",
        "tar -xf cudd-3.0.0.tar.gz && rm cudd-3.0.0.tar.gz",
        "cd cudd-3.0.0 && ./configure && make -j$(nproc)",
    )
    .pip_install("pytest")
    # Vendored OpenSTA source == this repository (DESIGN.md section 2.3:
    # reuse OpenSTA's readers rather than rewriting parsers).
    .add_local_dir(
        str(REPO_ROOT),
        "/opt/blacktimer/OpenSTA",
        copy=True,
        ignore=[".git", "build", "**/__pycache__"],
    )
    .run_commands(
        "cd /opt/blacktimer/OpenSTA && mkdir -p build && cd build"
        " && cmake -G Ninja -DCUDD_DIR=/cudd-3.0.0 .."
        " && ninja",
    )
)

STA = "/opt/blacktimer/OpenSTA/build/sta"
BT = "/opt/blacktimer/OpenSTA/blacktimer"

designs_vol = modal.Volume.from_name("blacktimer-designs", create_if_missing=True)
cache_vol = modal.Volume.from_name("blacktimer-build-cache", create_if_missing=True)

DESIGNS = "/designs"
CACHE = "/cache"

# ---------------------------------------------------------------------------
# Engine dispatch. Phase 0 has exactly one engine (OpenSTA on CPU); the GPU
# engine registers itself here in Phase 1. golden_diff always uses "cpu" as
# the reference and DUT_ENGINE as the device under test, so flipping this one
# constant turns the null test into the real diff.
# ---------------------------------------------------------------------------
DUT_ENGINE = "cpu"


def _load_design(name: str) -> dict:
    """Read a design descriptor from the designs volume.

    Layout: /designs/<name>/design.json with keys liberty (list), verilog,
    top, sdc, and optionally spef -- all paths relative to the design dir.
    """
    design_dir = Path(DESIGNS) / name
    meta = json.loads((design_dir / "design.json").read_text())
    meta["dir"] = str(design_dir)
    return meta


def _run_engine(engine: str, design: dict, out_csv: str) -> None:
    """Run one full-timing pass and dump endpoint slacks to out_csv."""
    if engine != "cpu":
        raise NotImplementedError(
            f"engine {engine!r}: GPU engine lands in Phase 1 (DESIGN.md s9)"
        )
    d = Path(design["dir"])
    env = dict(
        os.environ,
        BT_LIBERTY=" ".join(str(d / lib) for lib in design["liberty"]),
        BT_VERILOG=str(d / design["verilog"]),
        BT_TOP=design["top"],
        BT_SDC=str(d / design["sdc"]),
        BT_OUT=out_csv,
    )
    if design.get("spef"):
        env["BT_SPEF"] = str(d / design["spef"])
    subprocess.run(
        [STA, "-no_init", "-exit", f"{BT}/scripts/dump_endpoints.tcl"],
        env=env, check=True,
    )


@app.function(image=image, volumes={CACHE: CACHE}, timeout=3600)
def build() -> str:
    """[CPU] Rebuild the engine with the shared ccache (DESIGN.md s4.1).

    The image already contains a full build; this function exists for
    incremental rebuilds against the build-cache volume once the CUDA engine
    (Phase 1) makes cold nvcc runs expensive.
    """
    env = dict(os.environ, CCACHE_DIR=f"{CACHE}/ccache")
    subprocess.run(
        ["cmake", "--build", "/opt/blacktimer/OpenSTA/build"],
        env=env, check=True,
    )
    cache_vol.commit()
    return "build ok"


@app.function(image=image, gpu="B200", timeout=1800)
def unit_tests() -> str:
    """[B200] Kernel-level tests. Phase 0: harness tests + GPU smoke check."""
    subprocess.run(["nvidia-smi"], check=True)
    subprocess.run(
        ["python", "-m", "pytest", f"{BT}/tests", "-q"], check=True
    )
    return "unit tests ok"


@app.function(image=image, gpu="B200", volumes={DESIGNS: DESIGNS}, timeout=3600)
def full_sta(design_name: str, corner: str = "default") -> str:
    """[B200] One end-to-end timing run; returns the endpoint-slack CSV."""
    design = _load_design(design_name)
    out = f"/tmp/{design_name}.{corner}.{DUT_ENGINE}.csv"
    _run_engine(DUT_ENGINE, design, out)
    return Path(out).read_text()


@app.function(image=image, volumes={DESIGNS: DESIGNS}, timeout=3600)
def mmmc_sweep(design_name: str, corners: list[str]) -> dict[str, str]:
    """[fan-out coordinator] Map corner batches over B200 containers.

    Phase 3 replaces per-corner full runs with a shared graph image on the
    volume plus corner-batched (BC<=8) workers; the fan-out shape via
    .starmap is already the final one.
    """
    results = full_sta.starmap((design_name, c) for c in corners)
    return dict(zip(corners, results))


@app.function(image=image, volumes={DESIGNS: DESIGNS}, memory=32768, timeout=7200)
def golden_diff(design_name: str) -> str:
    """[CPU, high-mem] Reference OpenSTA vs DUT engine, 1 ps endpoint gate.

    Phase 0: DUT is also the CPU engine (null test). Raises on divergence so
    CI fails loudly. Waivers live at /designs/<name>/waivers.json.
    """
    import sys

    sys.path.insert(0, BT)
    from harness import golden_diff as gd

    design = _load_design(design_name)
    golden_csv = f"/tmp/{design_name}.golden.csv"
    dut_csv = f"/tmp/{design_name}.dut.csv"
    _run_engine("cpu", design, golden_csv)
    _run_engine(DUT_ENGINE, design, dut_csv)

    waivers = Path(design["dir"]) / "waivers.json"
    result = gd.diff_files(
        golden_csv, dut_csv, waivers if waivers.exists() else None
    )
    print(result.summary())
    if not result.clean:
        raise RuntimeError(f"golden_diff diverged on {design_name}")
    return result.summary()


@app.function(image=image, gpu="B200", volumes={DESIGNS: DESIGNS}, timeout=3600)
def bench(design_name: str, corner: str = "default") -> dict:
    """[B200] Perf harness following the GPUTimer TCAD'23 protocol
    (EVALUATION.md E1): one full-timing iteration measured end-to-end,
    memory preparation included. Phase 0: wall-clock only; Phase 2 adds the
    per-stage breakdown and NCU capture to the designs volume."""
    import multiprocessing
    import sys

    sys.path.insert(0, BT)
    from harness.evaluation import FullTimingRow, Platform

    design = _load_design(design_name)
    t0 = time.monotonic()
    _run_engine(DUT_ENGINE, design, f"/tmp/{design_name}.bench.csv")
    row = FullTimingRow(design_name,
                        ours_ms=(time.monotonic() - t0) * 1000.0)
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        capture_output=True, text=True,
    ).stdout.strip()
    platform = Platform(gpu=gpu or "unknown",
                        cpus=multiprocessing.cpu_count())
    return {
        "protocol": "E1 full timing (EVALUATION.md)",
        "benchmark": row.benchmark,
        "corner": corner,
        "engine": DUT_ENGINE,
        "ours_ms": row.ours_ms,
        # published-baseline ratios exist only for the TAU15-14nm suite
        "vs_published": row.vs_published(),
        "platform": platform.caption(),
    }


@app.cls(image=image, gpu="B200", volumes={DESIGNS: DESIGNS},
         scaledown_window=600)
class TimerSession:
    """[B200] Long-lived incremental server (DESIGN.md s4.1, s7).

    Phase 0: loads the design and runs full timing once on enter. Phase 4
    adds the command channel, graph overlay, and incremental K1-K4 behind
    the same RPC surface.
    """

    design_name: str = modal.parameter()

    @modal.enter()
    def load(self) -> None:
        self.design = _load_design(self.design_name)
        self.baseline_csv = f"/tmp/{self.design_name}.session.csv"
        _run_engine(DUT_ENGINE, self.design, self.baseline_csv)

    @modal.method()
    def report_wns(self) -> float:
        import sys

        sys.path.insert(0, BT)
        from harness.golden_diff import load_endpoint_csv

        slacks = load_endpoint_csv(self.baseline_csv)
        return min(s.slack_max for s in slacks.values())

    @modal.method()
    def update_timing(self, edits: list[dict]) -> str:
        raise NotImplementedError(
            "incremental ECO path lands in Phase 4 (DESIGN.md s9)"
        )


# ---------------------------------------------------------------------------
# Local entrypoints
# ---------------------------------------------------------------------------

# Smoke designs seeded from this repo's examples/ so the Phase 0 gate needs
# no external benchmark download. TAU15/19 and leon/netcard/vga are added to
# the volume separately (licensing requires fetching them from the contest
# sites; see README.md).
SMOKE_DESIGNS = {
    "gcd_sky130hd": {
        "liberty": ["sky130hd_tt.lib.gz"],
        "verilog": "gcd_sky130hd.v",
        "top": "gcd",
        "spef": "gcd_sky130hd.spef",
        "sdc": "gcd_sky130hd.sdc",
    },
}


@app.local_entrypoint()
def seed_designs() -> None:
    """Upload the in-repo smoke designs to the designs volume (run once)."""
    examples = REPO_ROOT / "examples"
    with designs_vol.batch_upload(force=True) as batch:
        for name, meta in SMOKE_DESIGNS.items():
            files = [meta["verilog"], meta["sdc"], *meta["liberty"]]
            if meta.get("spef"):
                files.append(meta["spef"])
            missing = [f for f in files if not (examples / f).exists()]
            if missing:
                print(f"skipping {name}: missing {missing}")
                continue
            for f in files:
                batch.put_file(str(examples / f), f"/{name}/{f}")
            desc = {k: v for k, v in meta.items()}
            batch.put_file(
                _tmp_json(desc), f"/{name}/design.json"
            )
            print(f"seeded {name}")


def _tmp_json(obj: dict) -> str:
    import tempfile

    fh = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False)
    json.dump(obj, fh, indent=2)
    fh.close()
    return fh.name


@app.local_entrypoint()
def ci() -> None:
    """The per-merge gate (DESIGN.md s9 cost model): build + unit tests +
    golden diff on the smoke designs."""
    print(build.remote())
    print(unit_tests.remote())
    for summary in golden_diff.map(SMOKE_DESIGNS.keys()):
        print(summary)


@app.local_entrypoint()
def diff(design: str) -> None:
    print(golden_diff.remote(design))
