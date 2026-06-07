# Done: Speed up `snapz pull all`

Implemented a remote unchanged fast path:

- `/api/sources` now includes a stable `bundle_sha256` for each source.
- The server stores that hash in SQLite after uploads, delta merges, and admin
  bundle rewrites.
- Pulled remote archives remember `remote_source_id` and
  `remote_bundle_sha256` in local `_meta.json`.
- `pull_all()` skips `/api/sources/<id>/index` when the remembered hash still
  matches the source summary.
- Server JSON responses are compact instead of pretty-printed.

Regression coverage:

- `test_pull_all_skips_remote_index_when_bundle_hash_unchanged`
- `test_index_pull_replaces_previous_remote_index`
