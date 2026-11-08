# Build-bound field metadata

Use the project venv. Extraction requires optional `.[analysis]` dependencies; schema search/inspection/editing need only the saved JSON and the standard library.

```powershell
aow4mod schema-extract
aow4mod schema-extract --refresh
aow4mod schema-search FriendlyFire --validated-only --limit 10
aow4mod schema-search TMagicFriendlyFireType --kind enums --details --limit 1
aow4mod schema-search Research --kind types --limit 10
aow4mod show "<resource ID>" --file "<indexed file>" --property Enabled --limit 10
```

Default cache: `.aow4mod/schema.json`. Refresh archives the previous cache under `schema-history/` and atomically replaces it. Other JSON files cannot be refreshed as schemas. Extraction reads the installed official ResourceED binary and original RPK corpus without executing the editor or game. Schemas bind both the editor SHA256 and extractor implementation hash. Unknown editor builds require a reviewed matching extraction profile (`--profile`); do not reuse RVAs from a previous version blindly.

Extraction and curated gameplay rules have separate build-bound profiles. Counts and corpus observations describe only their exact inputs; nested fields do not automatically inherit top-level storage validation. Unparsed enums and ambiguous declarations remain candidates. Regenerate after a tool/extractor change and consult [current compatibility](../docs/MANTA_COMPATIBILITY.md).

Evidence levels:

- **Curated property semantics:** an explicitly investigated field, including its enum/default where verified. Currently FriendlyFireType has this evidence.
- **Validated storage:** consistent serializer declarations, at least one function without conditional branches, plus observed serialized widths/boolean values. An integer's signedness, value range, unit and enum assignment remain unknown.
- **Candidate labels/types/enums:** useful for discovery, but do not justify default inference or automatic edits. Enum tables are searchable; similarly named fields are not automatically bound to them.
- **Opaque values:** preserved byte-for-byte unless the author explicitly stages a structural edit.

`show` merges curated information over extracted metadata. It reports omitted validated defaults, and signed/unsigned integer interpretations separately. `edit-plan --codec auto` can choose a validated bool. For other extracted integers, review values and use an explicit matching-width codec. These plans report `evidenced_storage_codec`, pin the schema file, and refuse stale evidence. This does not prove gameplay logic or perform runtime tests.

The extraction mechanism uses a small conservative x64 constant/alias tracker over generated serializers. Unrecognized instructions invalidate affected registers; unknown, conditional and conflicting declarations do not become editable fields. It is neither a full decompiler nor a complete game API.
