# Novel-Engine：简写生产与外部扩写包

本项目当前交付目标：3,800 章完整叙事简写，支撑约 1,600 万汉字正文；每章外部扩写目标 4,000～5,000 汉字。

正文由项目外的人或 LLM 扩写。本模式自动生成事件链、逻辑质检、定向修复、连续性记录，以及资料和提示词齐全的独立扩写包。简写不受旧文学评分和 10,000 字正文门限制。

## 使用

使用 Python 3.12+，按 `pyproject.toml` 安装依赖。模型沿用 `src/novel_engine/config/llm_providers.json` 中的配置，密钥通过对应环境变量提供。

从本目录运行，Linux、macOS 和 Windows 均可使用同一入口：

```bash
python scripts/condensed.py run --output /path/to/my-manuscript --end 3
python scripts/condensed.py run --output /path/to/my-manuscript --end 3800
python scripts/condensed.py status --output /path/to/my-manuscript
python scripts/condensed.py verify --output /path/to/my-manuscript
python scripts/condensed.py compile --output /path/to/my-manuscript
python scripts/condensed.py archive --output /path/to/my-manuscript --archive /path/to/expansion.zip
python scripts/condensed.py archive --output /path/to/my-manuscript --compact --from-chapter 1 --end 100 --archive /path/to/expansion-0001-0100.zip
```

将示例路径替换为本机输出路径。输出目录必须与源码分开。同目录重跑自动续接，已提交章节不会重复请求模型。放置输出目录下的 `STOP` 文件可在下一章开始前暂停。

交付文件：

- `expansion/chapter_0001/`：章节简写、场景卡、设定、上下文、文风指南，以及扩写、验收、修订提示词。
- `manuscript/全部章节简写.md`：仅包含已提交章节的合订本。
- `manuscript/章节索引.csv`：字数、扩写预算和来源哈希。
- `status.json`：实际完成数量、请求和 token 记录；未完成时明确 `complete=false`。
- `audit/`、`drafts/`：自动质检、修复、原始响应及失败诊断。

打开每章 `扩写提示词.md` 即可在项目外使用，无需本项目数据库或专用插件。扩写后的实际字数、文风和事实仍需在外部验收；导出提示词不等于正文已经扩写完成。

详见 [简写生产与外部扩写](docs/简写生产与外部扩写.md)。原正文流水线仍保留，其接口和历史数据不与简写生产混用。
