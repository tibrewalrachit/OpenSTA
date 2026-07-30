#!/usr/bin/env python3
"""Compare OpenSTA NLDM output arrivals vs PrimeTime CCS references.

Usage: gcs_correlate.py <gcs_checkout> <results_dir> [designs...]
  <results_dir> holds <design>.sta_nldm.csv produced by gcs_sta.tcl;
  <gcs_checkout>/bm/<design>/test.pt holds the PT CCS GBA arrivals.
test.pt values are negative (slack-style); magnitudes are arrivals in ps.
Column order is auto-detected by picking the rise/fall pairing with the
smaller mean error.
"""
import json
import statistics
import sys


def main():
    gcs, results = sys.argv[1], sys.argv[2]
    designs = sys.argv[3:] or ["mul", "log2", "div", "hyp"]
    out = {}
    for d in designs:
        pt = {}
        for line in open(f"{gcs}/bm/{d}/test.pt"):
            parts = line.split()
            if len(parts) == 3:
                pt[parts[0]] = (abs(float(parts[1])), abs(float(parts[2])))
        sta = {}
        with open(f"{results}/{d}.sta_nldm.csv") as fh:
            next(fh)
            for line in fh:
                name, rise, fall = line.strip().split(",")
                try:
                    sta[name] = (float(rise), float(fall))
                except ValueError:
                    pass

        def errors(swap):
            errs = []
            for k, (sr, sf) in sta.items():
                if k not in pt:
                    continue
                pr, pf = pt[k] if not swap else (pt[k][1], pt[k][0])
                errs += [100 * (sr - pr) / pr, 100 * (sf - pf) / pf]
            return errs

        e0, e1 = errors(False), errors(True)
        errs = min((e0, e1), key=lambda e: statistics.fmean(map(abs, e)))
        abs_errs = sorted(abs(e) for e in errs)
        out[d] = {
            "matched_outputs": len(errs) // 2,
            "mean_signed_err_pct": round(statistics.fmean(errs), 2),
            "mean_abs_err_pct": round(statistics.fmean(abs_errs), 2),
            "p95_abs_err_pct": round(
                statistics.quantiles(abs_errs, n=20)[18], 2),
            "max_abs_err_pct": round(abs_errs[-1], 2),
        }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
