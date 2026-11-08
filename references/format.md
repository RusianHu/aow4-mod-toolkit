# Observed AoW4 serialization

These are observed, bounded layouts implemented by this toolkit, not an official SDK or a complete format specification. Their applicability depends on the selected game/tool inputs and reviewed profiles.

## Bounded offset map

One tag byte: low 7 bits are the number of compact entries. If the high bit is set, a following little-endian u32 gives the number of extended entries. Compact entries are `(u8 key, u8 offset)`, followed by extended `(u32 key, u32 offset)` entries. Offsets are relative to the payload after the entire table. Each field ends at the next sorted offset, or at its enclosing node's end. Keys must be unique, the first payload offset zero, and every boundary inside its parent. An empty map is exactly one byte `00` (or a zero-count extended table with no payload).

A typed object starts with a little-endian u32 type ID, then an offset map. An observed object array is an outer map with key `1`, containing a contiguous zero-based index map. All strings used here are a u32 UTF-8 byte length followed by exactly that many bytes.

## ACP

Signature `CPR\0`; observed typed root at byte 19, preceded by bytes `01 00 00` at byte 16. Root type `0x0040009D`; outer map has that same class key. The nested class fields include:

| Field | Observed content |
|---|---|
| 0x14 | Uppercase package name |
| 0x15 | Display name |
| 0x16 | Description |
| 0x17 | Author |
| 0x18 / 0x19 | Version minor / major |
| 0x1D | Embedded image, not scanned for resources |
| 0x26 | Pack-change array |

Pack-change type `0x00401204`, class map fields `1` pack name and `2` resource-change array. Resource-change type `0x00401206`, class fields `1` resource object, `2` operation integer, `3` original locator. The investigated four records use operation `0x40`; other operations are reported unverified, not interpreted as overrides.

Resource maps have metadata fields `0x14` locator, `0x15` name, `0x18` u64 ID; higher keys identify serialized class sections. Their values are property offset maps. The auditor compares property payloads after removing offset-table relocation noise. It deliberately does not infer defaults for omitted fields.

## RPK

Signature `RPK\0`, observed bytes 4–11 `1a 00 00 04 00 00 00 00`; u32 at byte 12 is file size minus `0xB0`. Root offset map begins at `0xB0`.

- Field `0x16`: package name.
- Field `0x1E`: counted string vector of imported RPKs and asset libraries/files; it is not limited to RPK dependencies.
- Field `0x0D`: container index map.
- Observed container types: `0x0400017F`, `0x04000172`, `0x04000173`, `0x04000174`, `0x04013404`.
- Container field `0x33` holds a map; its `0x15` holds resource objects in the array envelope above.
- Resource IDs/names are read from actual objects, not string searches. Empty arrays are valid.

Unsupported headers/containers fail explicitly. The audit indexes installed original RPKs and RPKs inside the selected Mod. It does not resolve other ACP dependencies or launcher playsets. Multiple candidates and missing targets stay visible in reports.

Resource names may be omitted. Preserve their IDs and keep `name: null` instead of dropping such objects or fabricating names.

## Recognized resource links

1. Untyped map with exactly `0x14` locator string, `0x16` u64 ID, `0x17` label. Locator `[#hhhhhhhhhhhhhhhh]category\index` must match the ID.
2. Typed `0x0040004A`, map with exactly `0x14` u64 ID and `0x16` label.

Nested maps/objects are traversed only within validated bounds with a depth cap. Opaque fields may contain other link formats, so counts are not exhaustive. Name mismatches alone are not broken links: labels can be stale while IDs remain valid.

## Authoring and extracted metadata

Complete RPK clones retain the supported header revision, update its 160-byte UTF-8/zero-padded uppercase filename area and payload length, rewrite root/container package names, and keep category/index locator suffixes. Each resource receives a new 64-bit ID; high-bit IDs occur in real community files and are valid. Only the two recognized link encodings are rewritten. Unknown occurrences of old IDs block cloning.

Resource locator category/index suffixes are not positions in the serialized resource array. For example, the original Mayhem pack has 71 objects in one container but several locator categories. Shipped `Audio_ArchonProphecy.rpk`, `Equipment_ArchonProphecy.rpk` and `Culture_Architect.rpk` contain gaps within locator categories. Selected cloning/removal therefore keeps surviving locators stable while reindexing the serialized array. This is observed-format evidence, not a runtime playtest.

ACP field `0x1A` is a registered RPK string vector; `0x25` is the byte length of the `0x26` change blob. Rebuilding overrides updates the latter together. The string vector uses a u32 count followed by length-prefixed strings. Package UUID field `0x2A` uses UUID little-endian bytes.

`schema-extract` reads the current ResourceED generated serializer/class/enum registrations with build-bound function RVAs. It observes top-level original resource fields independently for storage widths and bool values. The current corpus validates 912 fields; this is not a full type system. Profiles, partial-register tracking, branches, stale schema rejection and editing boundaries have synthetic tests. See [schema-workflows.md](schema-workflows.md) for practical use and limitations.

## Future directions

Official editor invocation observed: `ResourceEd.exe -modpath "<Mods parent>" -mod "<Mod name>"`. This starts the GUI and is not a batch-edit API. Executable strings `headless` or `DUMPALL` alone do not establish a usable terminal interface.

Game console `DUMPALL`, `DUMPSPELLS`, `DUMPTOMES` are a possible additional JSON data source, but the auditor does not invoke them; their output may omit low-level targeter settings. [Data author explanation](https://steamcommunity.com/app/1669000/discussions/0/591810883669266729/?l=dutch).

The community [aow4_mcp](https://github.com/spookyzn/aow4_mcp) serves wiki tome data and is not a local Mod editor interface. The present CLI can be wrapped as MCP later; wrapping does not expand parser coverage.
