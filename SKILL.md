---
name: ielts-context-coach
description: 根据每日词表进行雅思阅读语境英译中陪练，记录真实答题、错词间隔复测和跨日进度，支持手机及其他 AI 接力。用于开始或继续词汇练习、整理每日词表、导出练习包和导入接力记录；可选在 macOS 本机采集不背单词日档。
---

# 雅思语境词汇陪练

把背过的词迁移到句子理解：一次给一段英文，等待用户翻译，再依据真实回答逐词记录。用中文交流、英文出题。只在用户要求练习时出题；收集、查看进度、导出、暂停都不算答题。

## 路径与数据

先读 [portable-rules.md](references/portable-rules.md)，统一出题、判分、间隔和接力规则。以下脚本路径相对于本技能目录，调用时解析为实际安装路径。

档案默认位于 `~/ielts-context-coach-data`；用户指定其他档案时，所有调用保持同一个 `--root`。日期按 Asia/Shanghai，命令中的 `YYYY-MM-DD` 替换为对应会话日期。读取已有 `progress.json` 和其中的 `preferences` 后再行动，优先遵守用户已有偏好。

- `daily/YYYY-MM-DD.json`：每日词表原始记录，保留学习、复习、已标熟来源及计数。
- `progress.json`：逐词历史、各日会话、真实问答和 pending 的权威状态。
- `exports/`：完整包、手机粘贴版及 ZIP，供另一位 AI 接力。

使用 `python3 scripts/coach.py --root /absolute/archive/path --day YYYY-MM-DD COMMAND`。全局参数放在子命令前；脚本只用 Python 标准库，支持 Python 3.9+ 的 macOS/Linux。

## 整理词表

有日档则核对日期、来源和计数。没有日档时，可以根据用户提供的词表建立，结构见 [daily.example.json](examples/daily.example.json)；示例日期与词条不代表用户真实学习。来源不明的词表需如实记录，不冒充 App 采集。

macOS 的不背单词采集可运行：

```sh
swift scripts/collect_today.swift --root /absolute/archive/path
```

需要本机安装不背单词、Swift 和运行进程的辅助功能权限。采集器只导航、读取并滚动学习/复习/已标熟列表；数量核对通过才写日档，不操作标熟、重学或学习答案。界面或权限不匹配时保留原日档，说明实际原因，改用用户提供的词表。已采集当天的同一练习续接无需重采；用户要求更新新学词时重新采集。

只要求整理或导出时保留全部 pending，不自动出题。没有对应会话时，先核对并导入日档，再导出。

## 开始或续接练习

1. 检查所有会话的 pending，按日期从早到晚续接原题。用原日期运行 `plan`，只在真实作答后 `grade` 到原日期；不覆盖未答题，不在旧日期继续新增题来阻塞今日导入。
2. 既存 pending 处理后，按日期顺序 `ingest` 所有已采集未导入的日档，最后同步当天日档，再读取当天 `plan`。脚本会合并旧未完成词、真实错词及到期保持检查。缺记录的日期表示未知，不推断用户未学。
3. 有 pending 就续接；否则从 `eligible` 自然选 2–4 个目标，写一段 25–60 词、1–3 句的英文。按搭配、话题和到期优先级选词，不按采集顺序堆词。尾声可以只考一个词。突出阅读中的指代、因果、转折、观点关系；原创材料不声称是真题，不虚构研究数据或成绩。
4. 先用 `ask` 保存，再展示英文正文；不提前加粗、解释目标或展示中文答案。每个目标需有实际出现的 surface 和语境义 meaning。

```sh
python3 scripts/coach.py --root /absolute/archive/path --day YYYY-MM-DD ask \
  --text '英文段落' \
  --targets-json '[{"word":"allocate","surface":"allocate","meaning":"分配"}]'
```

## 判分与反馈

保存用户原答，再按目标逐词判定。只用 `correct` / `wrong` / `unsure` / `not-tested`，每项附具体依据。中文不够自然但词义正确仍算 correct；区分词义、句法与输入笔误。提示后答对记 unsure，答案无法判断词义时 not-tested。pending 带 `grading_constraints` 时严格遵守，不重复累计顺带出现或过早复测词的成功。

```sh
python3 scripts/coach.py --root /absolute/archive/path --day YYYY-MM-DD grade \
  --answer '用户原答' \
  --results-json '{"allocate":{"result":"correct","reason":"识别出分配资源的含义"}}'
```

完全正确时简短确认。有错误时解释实际卡点；用户偏好详细反馈时，对照原文与用户译法，说明语境义、搭配及关键逻辑，给出整段完整、顺畅译文，解释必要的语序调整。提速靠选词，不能把已识别的词误判为错或省去用户要求的讲解。

非目标生词也要跟进：用户保留英文、明确说不认识，或原答显示词义误解时，先解释，在来源题 grade 后运行：

```sh
python3 scripts/coach.py --root /absolute/archive/path --day YYYY-MM-DD add-word \
  --word facility --surface facilities --meaning '设施' --source-round 3 \
  --reason '原答保留 facilities，未识别词义'
```

补充词不增加 App 日档计数，不虚构一次正式答错，需两次独立正确；只能明确关联单复数，不任意合并派生词。错词最早在另外三段真实问答后换语境复测（r+4），用户要求次日或更晚时遵守更长间隔。不要替用户作答以推进轮数。`plan.bridge` 为 true 时，可 `ask --bridge --text '英文理解段落' --targets-json '[]'`，真实回答后 `grade --answer '原答' --results-json '{}'`。

每次真实回答后保存，只有 `plan.complete=true` 才称本轮完成；分别显示今日有效覆盖、本轮通过和待巩固，不承诺永久记住。

## 导出与回传

运行 `export`，交付结果中的完整文件夹、ZIP 和 `手机粘贴版.md`。导出包含现有进度、pending、补充词、调度快照和规则，手机端无需运行脚本。默认仅阅读，语音关闭；用户明确启用时再读 [voice-rules.md](references/voice-rules.md)。

轻量接力只追加本次真实问答及 pending，返回 `接力记录.json`；完整包可返回更新后的 `progress.json`。有新发现的非目标生词时保留 `extra_words` 及真实来源轮次。没有文件能力时输出 JSON 供保存，不声称已写入本机。

```sh
python3 scripts/coach.py --root /absolute/archive/path import-progress \
  --file /absolute/path/to/接力记录.json
```

导入会检查 learner_id、base_revision、既有历史和复测间隔。本机已有新进度时保留两端文件后核对，禁止用旧快照覆盖。已核对的真实外部题目若含顺带出现、过早复测或过多目标，可用 `import-progress --observed` 保留证据；不因此放宽今后出题规则。一次只由一位 AI 负责写入；优先使用脚本的锁与原子写入，不从旧内存副本覆盖状态。

## 验证

修改脚本后运行 `python3 scripts/coach.py self-test`。它使用隔离的合成数据验证跨日重复、间隔复测、补充词、pending 续接、移动接力、旧包冲突和导出 ZIP，不接触真实档案。
