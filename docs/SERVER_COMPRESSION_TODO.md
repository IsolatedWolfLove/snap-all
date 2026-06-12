# Server Extreme Compression TODO

本 TODO 记录 snapz server 作为长期存储处时的极致压缩方案。本版不引入
Redis/radius 缓存，也不切换 MySQL；SQLite 继续管理用户、source、任务状态
和索引元数据，文件系统保存压缩后的冷数据。

## Goals

- 客户端本地继续使用当前 CAS/zstd 格式，优先保证 `save`、`restore`、本地读写
  足够快。
- Server 接收 push 时先快速校验并落盘，不让深度压缩阻塞上传结果。
- Server 后台允许消耗更多 CPU、内存和时间，把上传内容拆解成更省空间的冷存储
  格式。
- Pull 支持多种传输模式，默认让客户端处理冷压缩数据；弱客户端可以要求 server
  先转换成客户端可导入包。
- 本版只设计压缩、冷存储、pull 模式、迁移、GC 和测试，不设计缓存系统。

## Storage Layers

- `incoming/`
  - 保存刚上传的原始 `.snapz` bundle。
  - 上传时完成完整校验，校验成功后即可更新 source 状态并返回 push 成功。
  - compact 完成前作为 pull 回退数据源。
  - compact 成功并通过校验后，按保留策略延迟删除。
- `cold/`
  - 保存长期极致压缩数据。
  - 包含 source、snapshot、object、chunk、pack 的引用关系。
  - 包含 pack 文件和 pack offset index。
  - 是 compact 完成后的主存储。
- `hot/`
  - 仅作为未来临时转换区预留。
  - 本版不实现 Redis/radius 或其它缓存系统。
  - `client-bundle` 模式临时生成的包可以先落到临时目录，后续再决定是否纳入
    `hot/` 管理。

## Compact Pipeline

1. Push API 接收 `.snapz` bundle，写入 `incoming/` 临时文件。
2. 校验 bundle sha256、source id、tar 成员安全性、manifest 引用和对象完整性。
3. 校验成功后原子替换到 `incoming/<tenant>/<source>.snapz`。
4. 写入或更新 compact job，状态为 `pending`。
5. 后台 worker 领取 job，状态改为 `running`。
6. 打开 bundle，解析 source meta、snapshot meta、manifest 和 object 成员。
7. 对每个客户端 object 解压成 raw bytes。
8. 计算 raw sha256，并确认它和 manifest 中引用的 object sha 一致。
9. 小对象按完整 object 进入去重流程。
10. 大对象先做内容定义分块，再进入 chunk 去重流程。
11. 对未存在的 chunk 使用 zstd 最高等级 `22` 压缩。
12. 将 chunk 聚合进 cold pack，并写入 offset index。
13. 写入 object 到 chunk list 的映射。
14. 写入 snapshot 到 object refs 的映射。
15. 写入 source 当前 revision、compact status、统计信息。
16. 从 cold 存储重组抽样对象或完整 manifest，验证可读性。
17. 成功后将 job 标记为 `complete`。
18. 按 `SNAPZ_COMPACT_KEEP_INCOMING_DAYS` 延迟删除 incoming bundle。

Compact 必须具备幂等性。任务中断、进程退出或机器重启后，可以重新运行同一个
source revision 的 compact job，不得删除仍作为回退的数据。

## Cold Data Model

建议新增或扩展 SQLite 表来记录冷存储引用关系：

- `compact_jobs`
  - `tenant_id`
  - `source_id`
  - `revision`
  - `status`: `pending | running | complete | failed`
  - `error`
  - `created_at`
  - `updated_at`
  - `finished_at`
- `cold_sources`
  - `tenant_id`
  - `source_id`
  - `revision`
  - `compact_status`
  - `incoming_bundle_sha256`
  - `cold_manifest_sha256`
  - `raw_logical_bytes`
  - `cold_physical_bytes`
- `cold_snapshots`
  - `tenant_id`
  - `source_id`
  - `snapshot_name`
  - `revision`
  - `meta_zstd_sha256`
  - `manifest_zstd_sha256`
- `cold_objects`
  - `tenant_id`
  - `raw_sha256`
  - `raw_size`
  - `chunk_count`
  - `chunks_json`
  - `ref_count`
- `cold_chunks`
  - `tenant_id`
  - `chunk_sha256`
  - `raw_size`
  - `pack_id`
  - `offset`
  - `compressed_size`
  - `zstd_level`
  - `ref_count`
- `cold_packs`
  - `tenant_id`
  - `pack_id`
  - `path`
  - `compressed_size`
  - `raw_size`
  - `chunk_count`
  - `sealed`

默认只做 tenant 内去重，避免跨 tenant 内容存在性侧信道。跨 tenant 去重如果未来要
支持，必须作为显式 opt-in。

## Compression Strategy

- chunk 和 pack 直接使用 zstd 最高等级 `22`。
- manifest、snapshot meta、source meta 也使用 zstd level `22`。
- 大对象使用内容定义分块，默认参数：
  - `SNAPZ_COMPACT_CHUNK_FILE_BYTES=1048576`
  - `SNAPZ_COMPACT_CHUNK_MIN_BYTES=262144`
  - `SNAPZ_COMPACT_CHUNK_AVG_BYTES=1048576`
  - `SNAPZ_COMPACT_CHUNK_MAX_BYTES=4194304`
- 大对象小改动时，只应新增变化 chunk，不应重复保存整个 object。
- 小文件和小 chunk 进入 pack，减少文件系统元数据开销。
- pack 目标大小默认：
  - `SNAPZ_COMPACT_PACK_TARGET_BYTES=268435456`
- pack 可以按 source、文件类型、大小区间或时间批次分组，优先让相似数据进入同一
  pack。
- 首版可以先不训练 zstd dictionary；如果后续要进一步压缩 JSON、源码、日志和
  manifest，再加入 tenant/source 级 dictionary，并在 chunk 元数据中记录
  dictionary id。

注意：server 不应该长期保存“客户端已压缩 blob 再套一层 tar/gzip”的格式作为主
存储。极致压缩的关键是先还原 raw bytes，再重新分块、去重、pack、zstd level 22
压缩。

## Pull Transfer Modes

新增客户端配置 `pull_transfer_mode`：

- `cold`
  - 默认模式。
  - Server 直接发送冷压缩数据或冷对象描述。
  - 客户端负责解压、重组 chunk、导入本地 CAS。
  - 优点是最省带宽，server 临时转换成本最低。
  - 缺点是客户端 CPU 和内存要求最高。
- `client-bundle`
  - 推荐给 CPU 或内存较弱的客户端。
  - Server 从 cold pack 解压 chunk，重组 raw object，再压成当前客户端可导入的
    `.snapz` 或 CAS object 格式。
  - 客户端不需要理解 cold 格式。
  - 缺点是 server 更耗 CPU，并需要临时磁盘空间。
- `raw-stream`
  - 不推荐，只作为显式开启的兜底模式。
  - Server 解压为原始文件数据流发送，客户端尽量少做深度解压。
  - 缺点是网络流量最大，断点恢复和安全校验更复杂。
  - 必须由 `SNAPZ_ENABLE_RAW_STREAM_PULL=true` 显式开启。

配置入口：

- `SNAPZ_PULL_TRANSFER_MODE=cold|client-bundle|raw-stream`
- `snapz pull all --transfer-mode cold|client-bundle|raw-stream`
- `snapz config set pull_transfer_mode client-bundle`

API 协商建议：

- 客户端 pull 请求带 `X-Snapz-Pull-Mode`。
- `/api/sources` 返回 `compact_status` 和 `supported_pull_modes`。
- Server 不支持请求模式时返回明确错误。
- 如果 compact 尚未完成，server 可以从 `incoming/` 走 legacy bundle 回退。

## Server Configuration Defaults

```text
SNAPZ_SERVER_STORAGE=hot-cold
SNAPZ_COMPACT_ZSTD_LEVEL=22
SNAPZ_COMPACT_MANIFEST_ZSTD_LEVEL=22
SNAPZ_COMPACT_CHUNK_FILE_BYTES=1048576
SNAPZ_COMPACT_CHUNK_MIN_BYTES=262144
SNAPZ_COMPACT_CHUNK_AVG_BYTES=1048576
SNAPZ_COMPACT_CHUNK_MAX_BYTES=4194304
SNAPZ_COMPACT_PACK_TARGET_BYTES=268435456
SNAPZ_COMPACT_KEEP_INCOMING_DAYS=1
SNAPZ_COMPACT_SCOPE=tenant
SNAPZ_PULL_TRANSFER_MODE=cold
SNAPZ_ENABLE_RAW_STREAM_PULL=false
```

## Implementation Phases

### Phase 1: Configuration and Schema

- Add server config parsing for hot-cold storage and compact options.
- Add compact job schema and cold storage metadata schema.
- Add source compact status to serializers and admin API.
- Add migration tests for existing SQLite data directories.

### Phase 2: Incoming and Job Creation

- Keep current push validation behavior.
- Store accepted bundles under `incoming/`.
- Create or replace compact jobs after successful push or delta merge.
- Ensure compact jobs are idempotent and survive server restart.

### Phase 3: Cold Compactor

- Implement bundle reader to extract raw objects from client blobs.
- Implement raw sha verification.
- Implement content-defined chunking for large objects.
- Implement tenant-scoped chunk/object deduplication.
- Implement zstd level 22 chunk compression.
- Implement pack writer and offset index.
- Verify compact output before marking job complete.

### Phase 4: Cold Pull and Client Bundle Pull

- Serve `/index` from cold metadata when compact is complete.
- Serve object requests from cold chunks in `cold` mode.
- Implement `client-bundle` mode by rebuilding client-compatible payloads server-side.
- Fall back to incoming bundle when compact is pending or failed.

### Phase 5: Raw Stream Pull

- Add explicit raw-stream enable flag.
- Implement original file stream protocol.
- Add strict path traversal protection and interrupted-transfer handling.
- Keep raw-stream out of default mode and admin recommendations.

### Phase 6: Maintenance Commands

- `snapz-server compact --data <dir>` to compact existing incoming or legacy bundles.
- `snapz-server gc --data <dir>` to clean unreferenced chunks, packs, and expired incoming.
- `snapz-server stats --data <dir>` to show logical bytes, incoming bytes, cold physical
  bytes, dedup savings, compression savings, and compact job counts.

## Test Checklist

- Push returns successfully before compact finishes.
- Pull works while compact is pending by using incoming fallback.
- Compact complete source can be pulled in `cold` mode and restored byte-for-byte.
- Compact complete source can be pulled in `client-bundle` mode by a client that does not
  understand cold format.
- `raw-stream` is rejected unless explicitly enabled.
- `raw-stream` restore matches original source when enabled.
- Large file with small middle insertion only adds a small number of new chunks.
- Two sources with identical files share cold chunks inside the same tenant.
- zstd level 22 chunks, packs, manifest, and meta decompress correctly.
- Compact interruption never deletes incoming fallback data.
- Compact retry for the same revision is safe.
- Deleting a snapshot decrements refs but does not remove chunks still referenced elsewhere.
- GC removes only unreferenced chunks and expired incoming files.
- Corrupt pack index, missing chunk, or missing dictionary returns a clear error.
- Existing legacy `.snapz` bundle sources remain readable until migrated.

## Open Decisions

- Whether pack grouping should initially be by source, by tenant, or by approximate content
  type.
- Whether `cold` mode should transfer individual cold chunks or a compact source-level cold
  payload.
- Whether dictionary compression is worth adding in v1 or should wait until after baseline
  hot-cold storage lands.
- Whether admin UI should expose compact job controls in the first implementation.
