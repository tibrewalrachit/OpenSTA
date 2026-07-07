// BlackTimer graph image: the position-independent binary serialization of
// the whole data model (DESIGN.md section 5.4). Offsets, never pointers.
//
// The image is the unit of: ingest across the HAC boundary, Modal MMMC
// fan-out (written once to the designs volume, mapped by corner workers),
// and TimerSession restore after a container snapshot (device memory is not
// snapshotted; the image makes reload bandwidth-bound, not parse-bound).

#ifndef BLACKTIMER_GRAPH_IMAGE_HPP
#define BLACKTIMER_GRAPH_IMAGE_HPP

#include <cstdint>

namespace blacktimer {

inline constexpr uint32_t kGraphImageMagic = 0x424C4B54;  // "BLKT"
inline constexpr uint32_t kGraphImageVersion = 1;

// Section ids. Append-only: never renumber, gaps are fine. Section layout
// follows the structure-of-arrays model of DESIGN.md section 5.1-5.3 with
// the condition-innermost, corner-next interleaving validated by GPUTimer.
enum class SectionId : uint32_t {
  kPins = 1,          // pin table: names hashed out-of-line, flags
  kCsrForward = 2,    // row_ptr[pins+1], col[arcs], arc_id[arcs]
  kCsrReverse = 3,
  kArcTables = 4,     // per-arc NLDM table references, per cond per corner
  kRcArena = 5,       // flattened BFS-order RC forests + per-net offsets
  kRcBins = 6,        // net-size bin index (S/M/L/XL, section 5.2)
  kLibertyTemplates = 7,  // dedup'd LUT axis vectors
  kLibertyGrids = 8,      // per-arc value grids, texture-friendly layout
  kLevels = 9,        // level[pin] + level-sorted permutation (K1 output)
  kClockTree = 10,    // Euler tour + sparse table for CPPR LCA (K5)
  kStringPool = 11,   // pin/net names for reporting, not touched by kernels
};

struct SectionEntry
{
  SectionId id;
  uint32_t reserved = 0;
  uint64_t offset;  // from start of image, 256-byte aligned
  uint64_t bytes;
  uint64_t checksum;  // xxh3 of the section payload
};

// Fixed-size header at offset 0, followed by the section table, followed by
// aligned section payloads. Every field an offset or a count -- the image
// may be mapped or copied anywhere.
struct GraphImageHeader
{
  uint32_t magic = kGraphImageMagic;
  uint32_t version = kGraphImageVersion;
  uint64_t total_bytes = 0;
  uint64_t image_checksum = 0;  // xxh3 over everything after the header
  uint32_t section_count = 0;
  uint32_t corner_batch = 0;    // BC baked into array strides (section 5.5)
  uint64_t pin_count = 0;
  uint64_t arc_count = 0;
  uint64_t net_count = 0;
  uint64_t rc_node_count = 0;
  // SectionEntry table follows immediately.
};

static_assert(sizeof(GraphImageHeader) == 64,
              "header layout is part of the on-disk format");

}  // namespace blacktimer

#endif  // BLACKTIMER_GRAPH_IMAGE_HPP
