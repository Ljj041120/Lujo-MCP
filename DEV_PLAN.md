# 开发计划（每日 Review 用）

> 最近更新：2026-07-08
> 当前进度：参考项目迁移 M1–M10 全部完成。V1-V5 verify 自动断言全部完成（143 passed / 5 skipped）。
> 仓库已清理 `reference/`。工作区干净。

---

## 一、下一阶段目标：`verify` 自动断言（FR13 自动检测 / FR15 闭环）

**为什么先做这个**：现在规范驱动只做到"把规范塞给 AI 看"（被动），还差"系统自动比对期望 vs 实际，偏离即告警"（主动）。这是 P5/P6 痛点（"点了没反应""AI 说没问题但实则有问题"）的最后一公里。做完，静默失败才能从"靠前端 SDK 人工标记期望"升级为"自动判定"。

### 1. 目标
- 输入：一条请求/交互的实际结果 + 一份"期望规范"。
- 输出：`{matched: bool, diffs: [...], silent_failure: bool}`，无异常且 `matched=false` 时判为静默失败。

### 2. 数据模型（新增）
```python
Spec  = { id, kind: "api"|"ui"|"rule", target, expect: {status?, body_rules?, state_change?} }
Diff  = { field, expected, actual }
VerifyResult = { matched, diffs: [Diff], silent_failure, trace_id? }
```

### 3. 断言引擎设计
- 新建 `app/mcp/verifier/assert_engine.py`：
  - `assert_behavior(actual: dict, spec: Spec) -> {matched, diffs}`
  - API 类：比对 status_code、body 字段（JSONPath/键值规则）。
  - UI 类：比对期望状态变化（route_change / dom_change / network_request）是否发生。
  - 静默判定：`matched==false` 且 无异常、无 4xx/5xx → `silent_failure=true`。
- 复用现有 `trace_repo` 存 verify 结果（step=`verify`），复用 `build_debug_context` 注入 `spec_diffs`。

### 4. 工具 / 接口
- MCP 工具 `verify`：`{request_id 或 interaction, spec?}` → `VerifyResult`。
- REST：`POST /api/debug/verify`。
- 规范来源：不传 spec 时，尝试用 `spec.get_related_specs` 推断（可选）。

### 5. 模块拆分（每步少量文件，做完即停）
| 步骤 | 文件 | 说明 |
| --- | --- | --- |
| V1 | `app/mcp/verifier/assert_engine.py` + 单测 | 断言引擎核心，纯函数 |
| V2 | `app/mcp/verifier/spec_store.py` + 单测 | 规范 CRUD（复用 TraceStorage 抽象，step=`spec`） |
| V3 | `app/mcp/tools/verify_api.py` + 双传输注册 | `verify` 工具 |
| V4 | `app/api/debug.py` 增 `/api/debug/verify` | REST 端点 |
| V5 | `build_debug_context` 注入 `spec_diffs` + 文档 | 闭环 |

### 6. 验收
- 构造"返回 200 但 body 字段缺失"请求 → `verify` 输出 `silent_failure=true`。
- 定义规范后，`verify` 自动校验，偏离即告警（即使无传统错误）。

---

## 二、后续 Backlog（按价值排序，V 之后做）

| 优先级 | 项 | 说明 | 改动量 |
| --- | --- | --- | --- |
| 中 | 多 LLM provider | 内置 analyzer 抽 `LLMProvider`，支持智谱 GLM（OpenAI 兼容 base_url）/ 本地 | ~1 文件 |
| 低 | Web 控制台 | 可视化 trace / 静默失败 / verify 结果 | 较大，独立阶段 |
| 低 | 浏览器 SDK TS | 前端开箱即用（后端 ingest 已就绪） | 复制+适配 |
| 低 | Playwright 自动遍历 | FR14，前端 UI 自动点击遍历 | 较大 |

---

## 三、每日 Review 清单

- [ ] 跑测试：`(.venv) python -m pytest tests/unit/ -q`（应 93 passed/5 skipped）
- [ ] 看本文件"一、下一阶段目标"当前做到哪一步（V1–V5 勾选）
- [ ] 决定今天推进哪一步，做完在此勾选 + 提交

### 进度勾选
- [x] V1 断言引擎（2026-07-08 done，16 tests passed）
- [x] V2 spec_store（2026-07-08 done，14 tests passed）
- [x] V3 verify 工具 + 注册（2026-07-08 done，11 tests passed）
- [x] V4 /api/debug/verify（2026-07-08 done，7 tests passed）
- [x] V5 注入 spec_diffs + 文档（2026-07-08 done，4 tests passed，V1-V5 全部完成）

---

## 四、关键约束（不可违反）

- 只改 `app/`；保留 TraceStorage/SessionStorage 抽象、MemoryStore/PGStore、middleware.py 安全栈、error_handlers、metrics/health、测试结构。
- 不复制外部代码，按 proj1 架构重新实现。
- 每模块少量文件、做完即停、汇报"改了什么/为什么/如何测试"。
