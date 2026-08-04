# 变更记录（CHANGELOG）

> 本文件记录 Lujo-MCP 项目对外文档与代码的变更历史。
> 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [Unreleased] - 2026-08-04

> v0.4.0 开发中。M1 Quality Foundation ✅ / M2 Debug Case Schema + 种子知识 ✅ / M3 Fault Localization 2.0 + KB 准确度提升 ✅ / M4 Agent Verify Loop ✅ / M5 全量回归测试 + 文档同步 ✅ 已完成。

### 新增

#### 代码

- **Quality System 核心框架**（`app/quality/`）
  - `schemas.py`：`QualityReport` / `ContextCompleteness` / `AnalysisConfidence` / `EvidenceItem` / `DimensionScore` 数据模型
  - `scorer.py`：规则引擎 `QualityScorer.evaluate()`——9 维度加权评分 + 证据提取 + 可信度评分 + 改进建议，纯函数 + 静默降级
  - M2-M4 增强：`_score_code_snippet` 识别 M3 `fault_locations`；`_score_llm_analysis` 识别 M4 `case_confidence`/`verify_count`；`_extract_evidence` 提取静态分析证据；可信度公式优化（高相关度证据绝对值计数）
  - `__init__.py`：包导出
- **配置项**（`app/config.py`）：`quality_scoring_enabled` / `agent_iterative_repair_enabled` / `agent_max_iterations` / M2-M4 新增 `kb_vector_index_autosync` / `kb_type_level_fallback` / `kb_seed_jaccard_min_score` / `agent_verify_loop_enabled` / `agent_verify_loop_max_iterations` / `agent_verify_loop_pass_threshold` / `agent_verify_loop_high_confidence_pass_threshold` / `agent_verify_loop_partial_threshold` / `agent_verify_loop_kb_writeback_enabled`
- **Context Assembler 质量注入 + 静态分析集成**（`app/agent/context_assembler.py`）：`assemble()` 返回新增 `quality_report` + `fault_locations` 字段，4 路并发子装配，feature flag 控制，失败静默降级
- **LLM 分析增强**（`app/llm/analyzer.py`）：SYSTEM_PROMPT 新增 `reasoning_chain` + `evidence_items`；`_validate_and_normalize` 向后兼容旧格式；M2 三级 fallback 种子匹配（精确指纹→归一化指纹→类型级 Jaccard）；M4 沉淀统一走 DebugCase Schema
- **Debug Case Schema 增强**（`app/rag/debug_case.py`）：新增 `case_confidence`/`verify_count` 字段 + `compute_type_fingerprint` + `normalize_message_for_similarity`；`to_kb_entry`/`from_kb_entry` 往返保留 `_kb_meta`
- **KnowledgeBaseStore 向量同步**（`app/rag/knowledge_base.py`）：`_sync_entry_to_vector_store` / `_sync_all_to_vector_store` 双写同步；种子加载后自动重建向量索引
- **URL Resolver**（`app/mcp/collectors/url_resolver.py`）：无堆栈场景下通过 HTTP 方法+路径反查 FastAPI 路由表定位 handler 源码
- **StaticAnalyzer 增强**（`app/mcp/collectors/static_analyzer.py`）：新增 `analyze_source_code`/`analyze_handler` 支持无堆栈场景；函数名全文件扫描 fallback
- **Agent Verify Loop**（`app/agent/schemas.py` + `app/agent/coordinator.py`）：`VerifyRecord` / `IterationResult` / `LoopState` 数据模型；Coordinator 三层开关调度（Phase1 → Phase2 → M4 Loop）；`_compute_verify_record` 合成 Test/Git/Security 信号；`_compute_iteration_verdict` 四级判定（passed/partial/rejected/skipped）；`_persist_kb_verify` 写回 KnowledgeBase `verify_count`/`case_confidence` 递增
- **Dashboard 质量报告**（`app/api/dashboard.py` + `app/web/dashboard.html`）
  - `GET /api/dashboard/trace/{tid}/quality` 独立端点
  - `get_trace_detail` 注入 `quality_report` 字段
  - 前端 Quality 卡片：综合评分进度条 + 9 维度网格 + 证据列表 + 改进建议
- **测试**（`tests/unit/test_quality.py`）：86 个用例覆盖 19 个测试类；`tests/unit/test_verify_loop.py`：38 个用例覆盖 VerifyRecord/verdict/score/KB 写回/三层开关；`tests/unit/test_dashboard.py` 新增 6 个质量报告测试用例；M3 新增 `test_static_analyzer.py`（18 例）+ `test_url_resolver.py`（16 例）+ `test_context_assembler.py` 静态分析集成（3 例）+ `test_knowledge_base.py` 三级 fallback/向量双写（11 例）

#### 文档

- **PRD.md §12.2**：v0.4.0 路线图——Milestone 概览 + M1 评分基线（5 场景对比）+ M2-M4 评分提升预期 + 各 Milestone 贡献分解
- **DESIGN.md §19**：v0.4.0 架构评审决策（§19.1-19.6）
  - §19.1 项目当前状态评估（Beta 偏 Demo 判定）
  - §19.2 Quality System 评分模型设计（9 维度权重 + 模块结构 + 设计约束）
  - §19.3 M1 评分基线（5 场景对比 + 基线分析要点）
  - §19.4 M2-M4 改进逻辑与评分推演（逐场景维度变化 + 综合评分推演汇总）
  - §19.5 架构稳定性约束（6 个禁止大改模块）
  - §19.6 v0.4.0 明确不做（7 项）

### 修复

#### 代码

- **HTML Demo 安全修复**：`app/web/silent_failure_demo.html` + `app/web/network_capture_demo.html` 中 3 处硬编码 `apiKey: 'test_secret_key_456'` 改为 `localStorage.getItem("mcp_api_key") || "YOUR_API_KEY_HERE"`，消除源码中的真实 API Key 泄露
- **KnowledgeBaseStore 双写同步 bug**：KB 写入后向量索引不同步导致种子知识无法被 L2 向量召回；新增 `_sync_entry_to_vector_store` / `_sync_all_to_vector_store` 自动同步
- **_persist_kb_verify from_kb_entry 调用 bug**：手动构造 dict 与 `from_kb_entry` 期望的 KB entry 格式不匹配；修复为直接传 entry
- **best_iteration 逻辑 bug**：`repair_plan=None` 时 `best_iteration` 被错误更新；增加 `repair_plan is not None` 前置条件
- **可信度公式 bug**：旧 `(high/total)*0.3` 导致添加 MEDIUM 证据稀释高相关度占比；改为 `min(high/5, 1.0)*0.3` 绝对值计数

#### 文档

- **PRD.md**：修订记录 v5.6 中的 README.md 链接 `../README.md` → `../../README.md`（路径修正）
- **DESIGN.md**：3 处 `§6.1` 死链修复为 `§6`（§6 无子章节）
- **安全修复**：11 处 external→internal 文档引用泄露（`docs/public/` 下 7 个文件引用"内部文档"/"内部审计报告"/`docs/internal/` 路径）改为自洽表述
- **.gitignore**：补充 `*.key` / `*.pem` / `*.p12` / `*.pfx` / `*.keystore` / `credentials*` / `secrets*` / `id_rsa*` / `*.ppk` / `.aws/` / `.gcloud/` 密钥文件模式
- **async_pg_store.py**：内部文档路径引用 `docs/internal/ROADMAP.md` 改为通用提示

- **PRD.md**：修订记录新增 v5.6（v0.4.0 开发路线制定 + M1 Quality Foundation 交付）

> 测试基线：857 passed, 6 skipped, 0 failed

---

## [v0.3.0] - 2026-07-30

### 新增

- Dashboard 实时 SSE 推送（`DASH-SSE-001`）：`DashboardEventBus` 广播总线 + `GET /api/dashboard/stream` SSE 端点 + 前端 EventSource 集成
- FR20 Dashboard 实时 SSE 推送功能需求

> 测试基线：654 passed, 6 skipped, 0 failed

---

## [v0.2.0] - 2026-07-25

### 新增

- 三轨并行交付：异步分析队列 + 向量检索 RAG（in-process + Qdrant）+ RBAC + API Key 轮换
- Browser SDK V3/V6（网络错误自动标记、UI 静默失败自动检测）
- 指纹知识库基础能力（命中优先 + 自动沉淀）
- Phase 5 数据层长期优化（分区、归档、批量写入、降级、熔断器）

> 测试基线：520 passed, 6 skipped, 0 failed

---

## [v0.1.0] - 2026-07-08

### 新增

- 项目首版发布
- 8 个 Phase 全部落地：trace_repo / network / ui_event / git / silent_failure / ingest_error / build_debug_context / redaction
- FR13 assert_engine + verify / FR14 Playwright UI 遍历 + verify_ui / FR15 spec_store + 闭环
- 多 LLM provider 支持（openai / zhipu / custom）
- Web 控制台 Dashboard
- 17 个 MCP 工具双传输注册（stdio + HTTP）

> 测试基线：369 passed, 6 skipped, 0 failed
