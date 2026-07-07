# Smoke design for the sta-claude MCP layer: example1 netlist on Nangate45
# min/max libraries with SPEF parasitics -- gives real setup AND hold paths
# plus a clock network for full_clock_expanded reports.
# Usage: sta -no_splash work/design.tcl   (from the workspace root)

set dir [file dirname [info script]]
read_liberty -max $dir/../examples/nangate45_slow.lib.gz
read_liberty -min $dir/../examples/nangate45_fast.lib.gz
read_verilog $dir/../examples/example1.v
link_design top
read_spef $dir/../examples/example1.dspef
create_clock -name clk -period 10 {clk1 clk2 clk3}
set_input_delay -clock clk 0 {in1 in2}
set_output_delay -clock clk 0 {out}
set_propagated_clock [all_clocks]
