# BlackTimer engine (DESIGN.md sections 5-7).
#
# Phase 0: interface headers only (the HAC and graph-image contracts).
# Phase 1 adds the graph-image builder (links against the parent OpenSTA
# targets for its readers) and the CUDA kernel library K1-K3; this file
# gains enable_language(CUDA) and the fatbin targets then.

cmake_minimum_required(VERSION 3.24)

project(blacktimer LANGUAGES CXX)

add_library(blacktimer_interfaces INTERFACE)
target_include_directories(blacktimer_interfaces INTERFACE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_features(blacktimer_interfaces INTERFACE cxx_std_17)
