# Done: Prevent `remote_only` interrupted push from leaving missing local blobs

Implemented the client-side safety work:

- `push_all()` now runs under a store-level `_remote_sync.lock`, so cron,
  background, web, and manual pushes do not overlap in one store.
- `remote_only` eviction only runs when the entire `SyncOutcome` has no
  failures.
- Pulled `remote-src_*` archives are skipped by key prefix, even if their
  archived status is stale or inconsistent.
- Pending local reference checks also skip pushed keys only after the whole
  push succeeds, so failed/interrupted pushes leave local blobs available for a
  retry.

Regression coverage:

- `test_remote_only_push_failure_preserves_uploaded_source_blobs`
- `test_push_all_skips_pulled_remote_index_archives`
- `test_remote_only_push_after_eviction_uploads_delta`
