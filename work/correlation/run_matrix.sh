#!/bin/bash
# OpenSTA vs OpenTimer endpoint-slack correlation matrix (TAU2015 designs).
# Usage: OT_ROOT=<OpenTimer checkout> [STA=build/sta] [RES=work/correlation/out] \
#          work/correlation/run_matrix.sh aes_core ac97_ctrl des_perf vga_lcd
set -u
HERE=$(dirname "$(readlink -f "$0")")
OT_ROOT=${OT_ROOT:?path to OpenTimer checkout (built: bin/ot-shell)}
STA=${STA:-$HERE/../../build/sta}
RES=${RES:-$HERE/out}
mkdir -p "$RES"
for d in "$@"; do
  B=$OT_ROOT/benchmark/$d
  echo "=== $d ==="
  python3 "$HERE/mirror_checks.py" "$B" "$d" >/dev/null || { echo "$d mirror FAILED"; continue; }
  printf 'read_celllib -early %s\nread_celllib -late %s\nread_verilog %s\nread_spef %s\nread_sdc %s\nupdate_timing\nreport_wns\nreport_tns\n' \
    "$B/${d}_Early.lib" "$B/${d}_Late.lib" "$B/$d.v" "$B/$d.spef" "$B/$d.sdc" > "$RES/$d.ot.conf"
  t0=$(date +%s%N); "$OT_ROOT/bin/ot-shell" < "$RES/$d.ot.conf" > "$RES/$d.ot.log" 2>&1; t1=$(date +%s%N)
  echo "OT_WALL_MS $(( (t1-t0)/1000000 ))"
  printf 'dump_slack -o %s\n' "$RES/$d.ot.slack" | cat "$RES/$d.ot.conf" - | "$OT_ROOT/bin/ot-shell" >/dev/null 2>&1
  t0=$(date +%s%N)
  BENCH_DIR=$B BENCH_TOP=$d BENCH_OUT=$RES/$d.sta.csv \
    "$STA" -no_splash -exit "$HERE/ota_dump.tcl" > "$RES/$d.sta.log" 2>&1
  t1=$(date +%s%N)
  echo "STA_WALL_MS $(( (t1-t0)/1000000 ))"
  grep -hE "PHASES|GLOBAL" "$RES/$d.sta.log"
  python3 "$HERE/correlate.py" "$RES/$d.sta.csv" "$RES/$d.ot.slack" "$d" \
    > "$RES/$d.corr.json" 2> "$RES/$d.worst.txt" && head -c 400 "$RES/$d.corr.json"
done
