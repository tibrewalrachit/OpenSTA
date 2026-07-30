#!/usr/bin/env python3
"""Correlate OpenSTA endpoint slacks against an OpenTimer dump_slack file.

OpenSTA csv: endpoint,slack_min,slack_max   (ns floats, INF for unconstrained)
OpenTimer:   fixed-width table, columns E/R E/F L/R L/F Pin ('n/a' absent),
             pin names inst:pin (OpenSTA uses inst/pin).

slack_min (hold)  = min(E/R, E/F);  slack_max (setup) = min(L/R, L/F).
"""
import json
import math
import sys


def load_opensta(path):
    eps = {}
    with open(path) as fh:
        next(fh)
        for line in fh:
            name, smin, smax = line.rstrip("\n").split(",")
            def f(t):
                try:
                    v = float(t)
                    return v if math.isfinite(v) else None
                except ValueError:
                    return None
            eps[name] = (f(smin), f(smax))
    return eps


def load_opentimer(path):
    pins = {}
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) != 5 or parts[0].startswith("--"):
                continue
            vals = []
            for tok in parts[:4]:
                try:
                    vals.append(float(tok))
                except ValueError:
                    vals.append(None)
            er, ef, lr, lf = vals
            smin = min((v for v in (er, ef) if v is not None), default=None)
            smax = min((v for v in (lr, lf) if v is not None), default=None)
            pins[parts[4].replace(":", "/")] = (smin, smax)
    return pins


def agree(a, b, abs_tol, rel_tol):
    if a is None or b is None:
        return a is None and b is None
    d = abs(a - b)
    return d <= abs_tol or d <= rel_tol * abs(a)


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / math.sqrt(vx * vy) if vx and vy else None


def main():
    sta_csv, ot_dump, design = sys.argv[1], sys.argv[2], sys.argv[3]
    abs_tol, rel_tol = 0.002, 0.001  # 2 ps (dump quantization) or 0.1%
    sta = load_opensta(sta_csv)
    ot = load_opentimer(ot_dump)

    matched = missing = 0
    deltas = {"hold": [], "setup": []}
    pairs = {"hold": ([], []), "setup": ([], [])}
    mismatches = []
    for ep, (s_min, s_max) in sorted(sta.items()):
        if ep not in ot:
            missing += 1
            continue
        o_min, o_max = ot[ep]
        matched += 1
        for kind, s, o in (("hold", s_min, o_min), ("setup", s_max, o_max)):
            if s is None or o is None:
                if (s is None) != (o is None):
                    mismatches.append((ep, kind, s, o, "presence"))
                continue
            deltas[kind].append(abs(s - o))
            pairs[kind][0].append(s)
            pairs[kind][1].append(o)
            if not agree(s, o, abs_tol, rel_tol):
                mismatches.append((ep, kind, s, o, abs(s - o)))

    out = {"design": design, "endpoints_opensta": len(sta),
           "matched": matched, "missing_in_opentimer": missing}
    for kind in ("hold", "setup"):
        ds = deltas[kind]
        xs, ys = pairs[kind]
        wns = abs(min(ys)) if ys else 0
        out[kind] = {
            "n": len(ds),
            "max_abs_delta_ns": max(ds) if ds else None,
            "mean_abs_delta_ns": sum(ds) / len(ds) if ds else None,
            "pearson_r": pearson(xs, ys),
            "within_gate": sum(
                1 for s, o in zip(xs, ys) if agree(s, o, abs_tol, rel_tol)),
            "within_1pct_wns": sum(
                1 for d in ds if wns and d <= 0.01 * wns),
            "wns_opensta": min(xs) if xs else None,
            "wns_opentimer": min(ys) if ys else None,
        }
    out["gate"] = f"|d|<={abs_tol}ns or 0.1% rel"
    out["mismatch_count"] = len(mismatches)
    print(json.dumps(out, indent=2))
    mismatches.sort(key=lambda m: -(m[4] if isinstance(m[4], float) else 1e9))
    for ep, kind, s, o, d in mismatches[:10]:
        print(f"  WORST {kind:5s} {ep}: sta={s} ot={o} d={d}", file=sys.stderr)


if __name__ == "__main__":
    main()
