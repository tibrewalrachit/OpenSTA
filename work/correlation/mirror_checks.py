#!/usr/bin/env python3
"""Mirror FF check arcs between TAU2015 Early/Late liberty files.

The TAU generator puts hold checks only in *_Early.lib and setup checks only
in *_Late.lib. OpenTimer reads the files as independent early/late views, but
OpenSTA links each cell once and matches timing groups across the min/max
pair, so the un-mirrored check simply disappears (Warning 1111). This script
copies every timing() group with timing_type hold_* from the Early file into
the same cell/pin of the Late file, and setup_* groups the other way,
producing <name>_Early.mirror.lib / <name>_Late.mirror.lib.

The check VALUES are therefore identical in both views, which matches TAU
semantics (one hold table, one setup table per arc — no early/late split of
the constraint itself).

Usage: mirror_checks.py <bench_dir> <top>
"""
import re
import sys
from pathlib import Path


def parse_blocks(text):
    """Index of (cell, pin) -> list of timing-group texts that are checks,
    plus (cell, pin) -> insertion offset (end of pin block, before its
    closing brace) in the original text."""
    checks = {}
    insert_at = {}
    cell_re = re.compile(r'^\s*cell\s*\(\s*"?([\w]+)"?\s*\)\s*\{', re.M)
    pin_re = re.compile(r'^\s*pin\s*\(\s*"?([\w\[\]]+)"?\s*\)\s*\{', re.M)
    timing_re = re.compile(r'^\s*timing\s*\(\s*\)\s*\{', re.M)

    def block_end(start):
        depth = 0
        i = text.index("{", start)
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    return j  # index of closing brace
        raise ValueError("unbalanced braces")

    for cm in cell_re.finditer(text):
        cell = cm.group(1)
        cend = block_end(cm.start())
        for pm in pin_re.finditer(text, cm.end(), cend):
            if pm.start() > cend:
                break
            pin = pm.group(1)
            pend = block_end(pm.start())
            insert_at[(cell, pin)] = pend  # before pin's closing brace
            for tm in timing_re.finditer(text, pm.end(), pend):
                tend = block_end(tm.start())
                block = text[tm.start():tend + 1]
                if re.search(r'timing_type\s*:\s*(hold|setup)\w*', block):
                    checks.setdefault((cell, pin), []).append(block)
    return checks, insert_at


def inject(target_text, donor_checks, kind):
    """Insert donor check blocks of the given kind into target text."""
    _, insert_at = parse_blocks(target_text)
    edits = []  # (offset, text)
    for key, blocks in donor_checks.items():
        wanted = [b for b in blocks
                  if re.search(rf'timing_type\s*:\s*{kind}\w*', b)]
        if not wanted or key not in insert_at:
            continue
        payload = "\n" + "\n".join(w.rstrip() for w in wanted) + "\n"
        edits.append((insert_at[key], payload))
    for offset, payload in sorted(edits, key=lambda e: -e[0]):
        target_text = target_text[:offset] + payload + target_text[offset:]
    return target_text, len(edits)


def copy_templates(target_text, donor_text):
    """Copy lu_table_template definitions the target lacks (the injected
    check groups reference templates defined only in the donor header)."""
    tmpl_re = re.compile(r'^\s*lu_table_template\s*\(\s*"?([\w]+)"?\s*\)\s*\{',
                         re.M)

    def blocks(text):
        out = {}
        for m in tmpl_re.finditer(text):
            depth, start = 0, text.index("{", m.start())
            for j in range(start, len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        out[m.group(1)] = text[m.start():j + 1]
                        break
        return out

    have, donor = blocks(target_text), blocks(donor_text)
    missing = [t for name, t in donor.items() if name not in have]
    if not missing:
        return target_text, 0
    anchor = target_text.index("cell (")
    payload = "\n".join(missing) + "\n"
    return target_text[:anchor] + payload + target_text[anchor:], len(missing)


def main():
    bench, top = Path(sys.argv[1]), sys.argv[2]
    early = (bench / f"{top}_Early.lib").read_text()
    late = (bench / f"{top}_Late.lib").read_text()
    e_checks, _ = parse_blocks(early)
    l_checks, _ = parse_blocks(late)
    late2, n1 = inject(late, e_checks, "hold")     # hold: Early -> Late
    early2, n2 = inject(early, l_checks, "setup")  # setup: Late -> Early
    late2, t1 = copy_templates(late2, early)
    early2, t2 = copy_templates(early2, late)
    (bench / f"{top}_Late.mirror.lib").write_text(late2)
    (bench / f"{top}_Early.mirror.lib").write_text(early2)
    print(f"{top}: hold->Late {n1} pins (+{t1} templates), "
          f"setup->Early {n2} pins (+{t2} templates)")


if __name__ == "__main__":
    main()
