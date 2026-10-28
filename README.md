# IELTS Context Coach · 雅思语境词汇陪练

把背过的词，放回读得懂的句子里。

一个可安装的 AI skill：根据每日词表生成短段英文，让你译出大意；根据真实回答记录错词，在新语境中复测，并把进度带到手机或另一位 AI。

## 它怎样陪练

- **一次一段**：25–60 个英文词，自然容纳 2–4 个目标词，等待你翻译。
- **按词义判分**：中文不够漂亮也可以答对；词义、句法和输入笔误分别处理。
- **练过就留记录**：保存原题、原答和逐词结果，跨聊天继续未答题。
- **错词换语境再问**：跟进错词、非目标生词和跨日未完成词，区分 App 重复与真实答错。
- **手机接力**：导出粘贴版或完整 ZIP，练完带回接力记录，检查版本冲突后导入。

词表可以手动提供；macOS 用户还可用随附采集器读取不背单词“近日已学”的当日记录。核心练习不依赖任何 MCP 服务。

## 安装

将仓库克隆到支持 `SKILL.md` 的 AI 工具的技能目录。使用 `~/.agents/skills` 的环境可运行：

```sh
git clone https://github.com/chen121760/ielts-context-coach.git \
  "$HOME/.agents/skills/ielts-context-coach"
```

也可以从 [Releases](https://github.com/chen121760/ielts-context-coach/releases) 下载 ZIP，解压成 `ielts-context-coach/` 文件夹后放入技能目录。识别技能后发送：

> 使用 $ielts-context-coach。我想练雅思阅读，这是今天的词表：allocate、evidence、decline。请保存进度，一次出一段让我翻译。

已有学习档案时，告诉 AI 档案路径并说“继续上次练习”。默认档案在 `~/ielts-context-coach-data`，可通过 `--root` 指定其他目录。默认只做阅读翻译，语音需明确启用。

## 本机要求

| 功能 | 要求 |
| --- | --- |
| 管理档案、判分、导出与回传 | macOS 或 Linux，Python 3.9+，仅标准库 |
| 自动读取不背单词 | macOS、不背单词 App、Swift、运行进程的辅助功能权限 |
| 手机或另一位 AI 接力 | 支持粘贴 Markdown/JSON 或上传文件的 AI |

英文段落和语义判分由使用该 skill 的 AI 完成；脚本负责状态、调度与校验。App 采集依赖当前界面的标签和布局，数量不匹配时不会写入日档。

## 先用示例检查安装

在仓库目录执行。示例是合成数据，固定日期为 `2026-01-01`，与默认真实档案分开：

```sh
python3 scripts/coach.py self-test
python3 scripts/coach.py --root /tmp/ielts-context-coach-demo --day 2026-01-01 \
  ingest --file examples/daily.example.json
python3 scripts/coach.py --root /tmp/ielts-context-coach-demo --day 2026-01-01 plan
```

实际使用时，让 AI 根据你提供的词表建立日档，或在 Mac 上打开不背单词“我的内容 → 近日已学 → 今天”后采集：

```sh
swift scripts/collect_today.swift --root "$HOME/ielts-context-coach-data"
```

采集器核对学习、复习、已标熟三页数量，仅保存完整日档，不操作标熟、重学或学习答案。首次使用需在系统设置的“隐私与安全性 → 辅助功能”中授权实际运行脚本的终端或 AI 应用。

## 数据与接力

```text
~/ielts-context-coach-data/
├── daily/          每日原始词表
├── progress.json   真实问答、逐词历史与未答题
├── exports/        完整包、手机粘贴版和 ZIP
└── backups/        导入前备份
```

只要说“导出给手机练习”，AI 就会运行 `export` 并交付文件。手机练完后取回 `接力记录.json`，在本机运行：

```sh
python3 scripts/coach.py --root "$HOME/ielts-context-coach-data" \
  import-progress --file /absolute/path/to/接力记录.json
```

一次由一位 AI 接力；本机已有新进度时导入会拒绝覆盖，保留两端记录供核对。实际档案由你保管，导出到其他 AI 时由你选择分享。

## 文件入口

| 文件 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | AI 使用的入口与操作流程 |
| [portable-rules.md](references/portable-rules.md) | 出题、判分、复测和接力规则 |
| [coach.py](scripts/coach.py) | 档案、调度、导出、回传与自测 |
| [collect_today.swift](scripts/collect_today.swift) | macOS 当日词表采集 |
| [daily.example.json](examples/daily.example.json) | 可导入的合成日档示例 |

这个发行包包含陪练规则与本机脚本，不包含第三方 MCP 的源码、适配器、服务配置，也不包含个人词表、真实答题记录或阅读 PDF。
