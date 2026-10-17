# LLM 选择器使用说明（切换小说引擎使用的大模型）

本引擎**唯一的 LLM 选择入口**是：

```
src/novel_engine/config/llm_providers.json
```

它用 `active_profile` 指定当前供应商；每个 profile 描述 `base_url`、`api_key_env`
（只写环境变量**名**，不写真密钥）和各阶段（outline/director/synopsis/scenes/
polish/review）使用的模型与并发/超时。真实 API Key 存在：

```
src/novel_engine/.env
```

> 当前默认 profile = `agnes`，模型 `agnes-2.5-flash`，密钥环境变量 `AGNES_API_KEY`。

---

## 1. 推荐：用选择器脚本切换（不用手改 JSON）

在本目录双击或命令行运行 `select_llm.bat`（等价于直接跑 `select_llm.py`）：

```bat
select_llm.bat current                 查看当前 profile、各阶段模型、Key 是否就位
select_llm.bat list                    列出所有可选 profile
select_llm.bat switch <profile>       仅切换 active_profile
select_llm.bat set-key <profile> <key> 把该 profile 的 Key 写入 .env
select_llm.bat use <profile> [key]     切换 + 写 Key，一步到位
```

例：继续使用 Agnes（当前默认）：

```bat
select_llm.bat use agnes sk-你的Agnes密钥
```

切换/改 Key 后，**下一次 `start.bat` 或 `resume.bat` 生效**（已在跑的进程不受影响）。
`start/resume` 会自动把 active_profile 对应的 Key 从 `.env` 注入子进程。

脚本不会回显完整密钥（只显示前6后4位掩码），也不会把密钥写进 JSON。

---

## 2. 添加一个新的供应商（编辑 llm_providers.json）

在 `profiles` 里仿照 `agnes` 复制一段，改三处即可：`base_url`、`api_key_env`、
各 phase 的 `models`。最小骨架：

```json
"my_provider": {
  "base_url": "https://你的-openai-兼容端点",
  "api_key_env": "MY_PROVIDER_API_KEY",
  "timeout_s": 120,
  "max_retries": 2,
  "default_extra_body": {},
  "phases": {
    "outline":  {"models": ["你的模型名"], "response_format": null,      "stream": true, "idle_timeout_s": 100, "total_timeout_s": 220},
    "director": {"models": ["你的模型名"], "response_format": "json_object", "stream": true, "idle_timeout_s": 100, "total_timeout_s": 560},
    "synopsis": {"models": ["你的模型名"], "response_format": "json_object", "stream": true, "idle_timeout_s": 100, "total_timeout_s": 560},
    "scenes":   {"models": ["你的模型名"], "response_format": "json_object", "concurrency": 4, "stream": true, "idle_timeout_s": 100, "total_timeout_s": 650},
    "polish":   {"models": ["你的模型名"], "response_format": null,      "concurrency": 4, "stream": true, "idle_timeout_s": 100, "total_timeout_s": 650},
    "review":   {"models": ["你的模型名"], "response_format": "json_object", "stream": true, "idle_timeout_s": 100, "total_timeout_s": 220}
  }
}
```

然后：

```bat
select_llm.bat set-key my_provider sk-你的密钥
select_llm.bat switch my_provider
```

要求：该端点必须是 **OpenAI 兼容的 `/v1/chat/completions`**；director/synopsis/
scenes/review 需要能按 JSON 输出（`response_format=json_object`）。`scenes`/`polish`
的 `concurrency` 控制并发，注意供应商 RPM/TPM 限额。

---

## 3. 常见问题

| 现象 | 处理 |
|---|---|
| 启动报 `env var 'XXX_API_KEY' is not set` | 用 `select_llm set-key <profile> <key>` 写 `.env`，确认 profile 的 `api_key_env` 与之一致 |
| 想验证连通性 | 先 `start.bat 3` 小批量试跑，再全量 |
| 配置改坏了 / JSON 报错 | 参考本文件骨架修复；不要把真实密钥贴进 JSON |
| 同一供应商想用不同模型 | 改对应 profile 里各 phase 的 `models`，或新建一个 profile 再 `switch` |
