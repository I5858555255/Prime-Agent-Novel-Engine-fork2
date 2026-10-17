# LLM 选择器使用说明

## 概述

`config/llm_providers.json` 是 LLM 供应商的**主配置来源**。
切换 `active_profile` 即可把整个引擎切换到另一个供应商/模型配置；切换工具会**同时原子同步**`config/runtime_config.json` 中的 `llm` / `fallback_llm` / `provider` / `review_llm` 端点，因此生产运行（`production_runner`）与各阶段路由都会用同一个供应商，无需手工改两处。

**当前默认 `agnes`（模型 `agnes-2.5-flash`，密钥环境变量 `AGNES_API_KEY`）；已停用硅基流动。**

## 文件位置

- 配置文件：`novel_engine/config/llm_providers.json`
- 切换工具：`novel_engine/config/switch_llm.py`（零依赖，标准库）
- 密钥文件：`novel_engine/.env`（**不要**把真实密钥写进 `llm_providers.json`）

## 快速使用

```bash
# 查看当前配置与所有可用 profiles
python config/switch_llm.py

# 切换到另一个 profile（两种写法等价）
python config/switch_llm.py agnes
python config/switch_llm.py switch agnes
python config/switch_llm.py switch siliconflow_example
```

## 两个配置文件的关系

- `llm_providers.json`：你编辑/切换的主文件，含每阶段模型、并发、流式、超时。
- `runtime_config.json`：生产 failover 客户端读取的运行端点。**不要手改它的 LLM 段**，运行 `switch_llm.py <profile>` 会自动同步 `model/api_base/api_key_env`。
- 若同级没有 `runtime_config.json`（隔离测试目录），同步自动跳过。

切换后可用 `python config/switch_llm.py` 复查；真实运行时日志的 HTTP 请求主机应与所选 `base_url` 一致。

## 如何换供应商

1. 在 `llm_providers.json` 的 `profiles` 对象中新增一个条目，填写 `base_url`、`api_key_env`、`phases`。
2. 在 `.env` 中添加对应环境变量（例如 `SILICONFLOW_API_KEY=xxx`）。
3. 运行 `python config/switch_llm.py <新profile名>` 或 `python config/switch_llm.py switch <新profile名>` 切换。
4. 工具会自动校验必填字段并检查 `.env` 中是否已设置对应环境变量。

## 如何换模型

在每个 profile 的 `phases.<phase>.models` 数组里修改模型 ID。
例如：
```json
"scenes": { "models": ["new-model-id"], "response_format": "json_object" }
```

## 如何调节各阶段思考强度

在 profile 的 `default_extra_body` 里设置全局默认，或在 `phases.<phase>.extra_body` 里覆盖特定阶段：

```json
"default_extra_body": { "reasoning_effort": "none" },
"phases": {
  "review": {
    "models": ["agnes-2.5-flash"],
    "response_format": "json_object",
    "extra_body": { "reasoning_effort": "none" }
  }
}
```

有效值：`"none"`（关闭思考，评审默认值）、`"low"`（轻度思考，适合评审类任务）。

## 如何调节并发度

在对应 phase 里加 `"concurrency": N`：
```json
"polish": { "models": ["agnes-2.5-flash"], "concurrency": 4 }
```
默认值 4，可按硬件资源调整。

## 密钥管理

- 真实 API Key **只**写入 `.env` 文件，不要写入 `llm_providers.json`。
- `llm_providers.json` 中只写环境变量名（`api_key_env` 字段）。
- 引擎启动时从 `.env` 文件读取密钥（通过 `env_bootstrap.load_project_env()`），缺失会 fail-fast 报错。
- 切换 profile 时，工具会检查 `.env` 中对应 key 是否已存在。

## Schema 说明

```json
{
  "active_profile": "<当前生效的 profile 名>",
  "profiles": {
    "<profile名>": {
      "base_url": "https://...",
      "api_key_env": "ENV_VAR_NAME",
      "timeout_s": 120,
      "max_retries": 2,
      "default_extra_body": { "reasoning_effort": "none" },
      "phases": {
        "outline":  { "models": [...], "response_format": null },
        "director": { "models": [...], "response_format": "json_object" },
        "synopsis": { "models": [...], "response_format": "json_object" },
        "scenes":   { "models": [...], "response_format": "json_object", "concurrency": 4 },
        "polish":   { "models": [...], "response_format": null, "concurrency": 4 },
        "review":   { "models": [...], "response_format": "json_object", "extra_body": { "reasoning_effort": "none" } }
      }
    }
  }
}
```

字段含义：
- `active_profile`：当前使用的 profile，改此字段等价于切换全引擎供应商。
- `base_url`：OpenAI 兼容 API 端点根 URL（不带 `/v1` 后缀）。
- `api_key_env`：从哪个环境变量（或 `.env` 文件）读密钥。
- `timeout_s`：单次请求超时秒数。
- `max_retries`：同一模型失败后的重试次数。
- `default_extra_body`：所有 phase 的默认额外请求体（如 reasoning_effort）。
- `phases.<phase>.models`：该阶段的候选模型列表，故障转移按顺序依次尝试。
- `phases.<phase>.response_format`：注入 OpenAI `response_format` 参数，`null` 表示不注入。
- `phases.<phase>.concurrency`：该阶段最大并发数（仅 scenes/polish 有效）。
- `phases.<phase>.extra_body`：覆盖 default_extra_body 的阶段级参数。

## 评审为什么默认 reasoning_effort=none

评审任务需要的是精确的结构化 JSON 输出，而非冗长的推理过程。启用 thinking 会增加 Token 消耗且对评分质量无实质提升，因此 review phase 默认关闭思考。

## 即将支持

以下功能属于下一包（pkg2b2），目前尚未实现，仅在架构上预留：

- 流式 SSE 输出与空闲/按 token 总超时控制
- HALT/成本哨兵熔断机制
- 实时进度心跳
