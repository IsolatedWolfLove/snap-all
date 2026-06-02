# snapz 项目 20 轮 Bug 审查 — 最终综合报告

> 报告日期: 2026-06-01
> 审查轮次: 20 轮（含重试）
> 审查范围: snapz/ + snapz_server/ 全部 Python 源码（50+ 文件）

---

## 一、总体统计

| 指标 | 数值 |
|------|------|
| 总发现条目数（含重复/误报/设计取舍） | ~150 |
| 去重后独立 Bug 总数 | **约 110 个** |
| 第1轮误报剔除 | 2 个 (#14, #22) |
| 第1轮设计取舍 | 1 个 (#18 CSP unsafe-inline) |
| 确认有效 Bug | **约 107 个** |

---

## 二、按严重程度分类

| 严重程度 | 数量 | 占比 | 说明 |
|----------|------|------|------|
| **Critical** | 2 | 1.9% | 可导致安全漏洞或数据丢失 |
| **High** | 18 | 16.8% | 可导致崩溃、数据损坏或严重性能问题 |
| **Medium** | 48 | 44.9% | 功能缺陷、非原子操作、用户体验问题 |
| **Low** | 39 | 36.4% | 代码质量、国际化、低概率边界条件 |

### Critical (2 个)
1. **#8** `read_live_bytes` 无路径穿越校验 — 攻击者可读取任意文件 (`_api_core.py:1566`)
2. **#19** `safe_snapshot_name` 允许 `..` 路径遍历 — 服务端路径注入 (`routes.py:97-100`)

### High (18 个)
| 编号 | 问题 | 模块 |
|------|------|------|
| R5-4 | `increment_refs/decrement_refs` 非原子 read-modify-write | core |
| R5-9 | `_touch_registry` 并发 read-modify-write 竞态 | core |
| #11/R16-8 | zstd bundle 全量解压到内存，大文件 OOM | API |
| NEW-5 | `save` 静默丢弃写入失败文件，用户无通知 | API |
| R6-12 | `unpack()` 无解压炸弹防护 | core |
| R8-8 | **全项目零 fsync** — 崩溃时数据丢失 | 全局 |
| R11-11 | **全项目零 logging** — 错误无法诊断 | 全局 |
| R12-1 | 逐字节处理大文件，极度低效 | API |
| R12-5 | `read_blobs_to` 每 chunk 全量内存读取 | core |
| R16-1 | remote 无重试逻辑，网络抖动全部失败 | remote |
| R16-2 | 下载 bundle 非原子写入，断连导致损坏 | remote |
| R16-4 | 认证文件非原子写入，中断后损坏 | remote |
| R16-12 | bundle 导入 blob 无 SHA256 完整性校验 | API |
| R17-2 | `_referenced_blobs` 遗漏 chunk 引用，删除时误删 blob | core |
| R18-2 | 测试断言语义错误（删除v1用v2验证） | tests |
| R5-2 | `read_manifest` 缺字段 KeyError 未处理 | core |
| #15/R8-7 | bundle 接收与 DB 更新非事务性 | server |
| R15-2 | 无请求超时，Slowloris 可耗尽线程 | server |

---

## 三、按模块分类

| 模块 | Bug 数 | Critical | High | Medium | Low |
|------|--------|----------|------|--------|-----|
| **core** (cas.py / store.py / archive.py / bundles.py / config.py) | ~32 | 0 | 8 | 16 | 8 |
| **API** (_api_core.py / _api_bundle.py / _api_find.py / _api_*.py) | ~25 | 1 | 5 | 12 | 7 |
| **CLI** (_cli_*.py) | ~14 | 0 | 0 | 7 | 7 |
| **TUI** (_tui_*.py) | ~10 | 0 | 0 | 5 | 5 |
| **server** (app.py / routes.py / db.py / server_config.py) | ~20 | 1 | 2 | 9 | 8 |
| **remote** (remote.py) | ~12 | 0 | 3 | 6 | 3 |
| **tests** (test_*.py) | ~8 | 0 | 1 | 3 | 4 |

---

## 四、TOP 10 最应优先修复的 Bug

按 **影响面 × 严重程度 × 修复难度** 排序：

| 优先级 | 编号 | 问题 | 影响 | 预估工时 |
|--------|------|------|------|----------|
| **1** | #8 | `read_live_bytes` 路径穿越 — 可读任意文件 | 安全漏洞 | 1h |
| **2** | #19 | `safe_snapshot_name` 允许 `..` — 路径注入 | 安全漏洞 | 30min |
| **3** | R17-2 | `_referenced_blobs` 遗漏 chunk — 删快照误删 blob | 数据丢失 | 2h |
| **4** | R16-2 | 下载 bundle 非原子写入 — 断连致损坏 | 数据损坏 | 2h |
| **5** | R16-12 | bundle 导入无 SHA256 校验 — 数据可被篡改 | 数据完整性 | 3h |
| **6** | R5-4 | `increment_refs` 非原子 — 并发丢失更新 | 引用计数错误 | 3h |
| **7** | R8-8 | 全项目零 fsync — 崩溃时数据丢失 | 数据持久性 | 4h (系统性) |
| **8** | R16-1 | remote 无重试逻辑 — 网络抖动即失败 | 可用性 | 4h |
| **9** | NEW-5 | `save` 静默丢弃失败文件 — 用户不知数据丢失 | 数据完整性 | 2h |
| **10** | R12-1 + R12-5 | 逐字节处理 + 全量内存读取 — 大文件性能灾难 | 性能 | 6h |

**修复这 10 个 Bug 预计需要 ~30 小时，但可消除 90% 以上的数据丢失/安全风险。**

---

## 五、模式分析（系统性问题）

以下模式在多个位置反复出现，建议作为技术债务专项治理：

### 1. 非原子写入 (出现 **12 处**)
- `store.py`: 4处 (R8-1~R8-4) — `write_text` 直接写入无 tmp+replace
- `_api_bundle.py`: 1处 (R8-5)
- `remote.py`: 2处 (R16-2 下载, R16-4 认证)
- `cas.py`: refs index 写入
- **根因**: 无统一的 `atomic_write()` 工具函数

### 2. 非原子 read-modify-write 竞态 (出现 **3 处**)
- `cas.py:737-741` — `increment_refs/decrement_refs`
- `store.py:304-337` — `_touch_registry`
- `preferences.py:205-273` — `set/unset_config_value`
- **根因**: 无文件锁机制

### 3. 内存爆炸 / OOM 风险 (出现 **5 处**)
- `archive.py:377-382` — gzip `getmembers()`
- `_api_bundle.py:131-135` — zstd 全量解压
- `cas.py:466,538` — blob 全量内存读取
- `_api_core.py:92` — 逐字节处理大文件
- **根因**: 缺少流式处理基础设施

### 4. 静默吞掉异常 (出现 **8 处**)
- `R11-1~5` + `R11-13,15` — 多处 `except Exception: pass/return`
- **根因**: 无日志系统 (R11-11)，开发者无法用 logging.warning()

### 5. CJK / Unicode 处理错误 (出现 **5 处**)
- `R7-1~4` — TUI 中 len() 计算宽度、输入过滤仅 ASCII
- `NEW-7` — CLI 列对齐错乱
- **根因**: 缺少统一的 `display_width()` 工具函数

### 6. KeyError 未处理 (出现 **4 处**)
- `#5`, `R5-2`, `R5-10`, `#12` — dict.get() 默认值掩盖缺失字段
- **根因**: 缺少 schema 验证层

### 7. 全项目零基础设施 (出现 2 处，影响全局)
- **零 fsync** — 所有 tmp+replace 路径均不安全
- **零 logging** — 无法线上诊断

---

## 六、代码质量评分

| 维度 | 得分 (1-10) | 说明 |
|------|-------------|------|
| 功能正确性 | 6 | 核心流程基本正确，但边界条件和异常路径问题多 |
| 数据安全性 | 4 | 非原子写入遍布，零 fsync，并发竞态 |
| 安全性 | 5 | 2 个路径穿越 Critical，但有基本防护（hmac.compare_digest、1MB 限制） |
| 性能 | 5 | 存在严重的逐字节处理和全量内存读取，但日常小规模使用尚可 |
| 可维护性 | 6 | 代码结构清晰、分层合理，但缺少日志和统一工具函数 |
| 测试质量 | 5 | 有测试但覆盖不均，存在语义错误断言 |
| 国际化 | 6 | 有 i18n 框架但多处硬编码英文 |
| 错误处理 | 4 | 大量静默吞异常，用户反馈不足 |

### 综合评分: **5.1 / 10**

---

## 七、最终建议

### 第一阶段：紧急修复（1-2 天）
1. 修复 2 个 Critical 安全漏洞（路径穿越）
2. 修复 `read_blob_bytes` 全量内存读取 → 流式处理
3. 为 remote 模块添加基本重试逻辑
4. 添加 `atomic_write()` 工具函数，替换全部非原子写入

### 第二阶段：数据安全（3-5 天）
5. 为 `increment_refs`/`_touch_registry` 添加文件锁
6. 为 bundle 导入添加 SHA256 完整性校验
7. 引入 `logging` 模块，替换所有静默吞异常
8. 修复 `_referenced_blobs` chunk 遗漏（R17-2）

### 第三阶段：性能与质量（1-2 周）
9. 重构大文件处理为流式管线
10. 添加 CJK 宽度计算工具函数
11. 补充 CLI/TUI 测试覆盖
12. 添加 Schema 验证层

### 长期技术债务
- 全项目 fsync 策略
- 统一错误处理框架
- 国际化收尾
- Dockerfile / systemd 部署文件

---

**结论**: snapz 项目架构设计合理、功能覆盖全面，但存在大量「最后一公里」的工程健壮性问题。
最核心的风险集中在 **数据安全**（非原子操作、零 fsync、并发竞态）和 **安全防护**（路径穿越）两个方面。
优先修复 Top 10 Bug 可在 30 小时内将项目可靠性提升一个台阶。
