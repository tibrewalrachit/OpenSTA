// BlackTimer Hardware Abstraction Contract (DESIGN.md section 3.1).
//
// Everything below these interfaces is identical on Modal/B200 (PCIe) and
// GB200 (NVLink-C2C coherent). The contract has three clauses:
//
//   1. Residency rule: the timing graph, RC arrays, and all per-pin state
//      live in device memory. The host never dereferences them. Host code
//      holds DeviceSpan handles, not pointers it may read through.
//   2. Ingest channel: bulk state crosses the boundary only as graph-image
//      segments (graph_image.hpp), via cudaMemcpyAsync on PCIe targets and
//      coherent stores on GB200.
//   3. Command channel: incremental edits and queries cross as small
//      fixed-format records, so link latency bounds round-trip count only.
//
// Dispatch thresholds (GPU vs host-mirror path for tiny ECOs) come from a
// CalibrationTable measured at startup, never from compile-time constants.

#ifndef BLACKTIMER_HAC_HPP
#define BLACKTIMER_HAC_HPP

#include <cstddef>
#include <cstdint>

namespace blacktimer {

// Opaque handle to device-resident memory. Host code may pass it to kernels
// and channels but must not dereference it (residency rule).
struct DeviceSpan
{
  void *device_ptr = nullptr;  // valid only on the device
  size_t bytes = 0;
};

// Clause 2: one-way bulk path from the graph-image builder into device
// memory. Implementations: PcieIngestChannel (cudaMemcpyAsync staging,
// Modal/B200), CoherentIngestChannel (Grace stores, GB200).
class IngestChannel
{
public:
  virtual ~IngestChannel() = default;
  // Allocate a device-resident destination for one graph-image segment.
  virtual DeviceSpan allocate(size_t bytes) = 0;
  // Enqueue a copy of host bytes into a previously allocated span.
  virtual void push(const void *host_src, DeviceSpan dst, size_t bytes) = 0;
  // Block until all pushed segments are visible to device kernels.
  virtual void sync() = 0;
};

// Clause 3: fixed-format command records. Layout is versioned with the
// graph image; new commands append to the enum, never renumber.
enum class CommandOp : uint32_t {
  kRepowerGate = 1,
  kResizeGate = 2,
  kInsertBuffer = 3,
  kRerouteNet = 4,  // payload: SPEF fragment appended via IngestChannel
  kUpdateTiming = 16,
  kQuerySlack = 32,
  kQueryWns = 33,
};

struct CommandRecord
{
  CommandOp op;
  uint32_t object_id;   // pin/net/instance id in graph-image numbering
  uint32_t payload_off; // offset into the batch payload arena
  uint32_t payload_len;
};

class CommandChannel
{
public:
  virtual ~CommandChannel() = default;
  // Submit a batch of edit/query records (one round trip regardless of
  // batch size -- callers batch per update_timing(), DESIGN.md section 7).
  virtual void submit(const CommandRecord *records, size_t count,
                      const void *payload, size_t payload_bytes) = 0;
  // Retrieve results for the queries in the last submitted batch.
  virtual size_t collect(void *results, size_t max_bytes) = 0;
};

// Measured per platform at startup (DESIGN.md section 3.1 clause 3): the
// GPUTimer ~67K-candidate CPU/GPU crossover was a PCIe-era artifact and is
// remeasured, not assumed, on every target.
struct CalibrationTable
{
  double command_round_trip_us = 0.0;  // measured command-channel latency
  double ingest_gbps = 0.0;            // measured bulk ingest bandwidth
  double kernel_launch_us = 0.0;       // measured empty-launch latency
  // ECO ripples with fewer seed pins than this run on the host mirror.
  uint32_t eco_gpu_dispatch_threshold = 0;
};

CalibrationTable calibrate();  // implemented per platform, Phase 1/5

}  // namespace blacktimer

#endif  // BLACKTIMER_HAC_HPP
