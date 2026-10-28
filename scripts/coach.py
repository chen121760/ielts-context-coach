#!/usr/bin/env python3
"""Persistent, portable vocabulary practice; standard library only."""
import argparse
import copy
import fcntl
import json
import os
import re
import tempfile
import unicodedata
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
SKILL = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / "ielts-context-coach-data"
RESULTS = {"correct", "wrong", "unsure", "not-tested"}


def key(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 160:
        raise ValueError("词条必须是非空短字符串")
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def today():
    return datetime.now(TZ).date().isoformat()


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def uid():
    return str(uuid.uuid4())


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        name = f.name
    os.replace(name, path)


def empty_state():
    return {"schema_version": 1, "learner_id": uid(), "revision": 0,
            "timezone": "Asia/Shanghai", "words": {}, "sessions": {}}


def load(root):
    path = root / "progress.json"
    return read(path) if path.exists() else empty_state()


def save(root, state):
    state["revision"] += 1
    state["updated_at"] = now()
    write(root / "progress.json", state)


def prior(day, days=1):
    return (date.fromisoformat(day) - timedelta(days=days)).isoformat()


def repeat_info(entry, day):
    dates = entry["day_sources"]
    recent = [d for d in dates if prior(day, 6) <= d <= day and "复习" in dates[d]]
    consecutive = 0
    cursor = day
    while "复习" in dates.get(cursor, []):
        consecutive += 1
        cursor = prior(cursor)
    normal_new_review = "复习" in dates.get(day, []) and "学习" in dates.get(prior(day), [])
    return {"review_days_7": len(recent), "consecutive_review_days": consecutive,
            "normal_new_review": normal_new_review,
            "focus": not normal_new_review and (consecutive >= 2 or len(recent) >= 3)}


def required(entry, day):
    recent_misses = sum(e["result"] in {"wrong", "unsure"} for e in entry["reading"]["history"]
                        if prior(day, 6) <= e["date"] <= day)
    if recent_misses >= 3:
        return 3
    return 2 if entry["reading"]["needs_repair"] or repeat_info(entry, day)["focus"] else 1


def ingest(state, daily):
    day = daily["date"]
    date.fromisoformat(day)
    lists = daily["lists"]
    for source, words in lists.items():
        if source not in {"学习", "复习", "已标熟"} or not isinstance(words, list):
            raise ValueError("日档来源或词表格式错误")
        normalized = [key(w) for w in words]
        if len(set(normalized)) != len(words) or daily["counts"].get(source) != len(words):
            raise ValueError(f"{source} 日档计数不符或包含同页重复词")
    previous_items = {}
    for d in sorted(state["sessions"]):
        if d < day:
            previous_items.update(state["sessions"][d]["items"])
    unfinished = {k for k, item in previous_items.items() if item["status"] != "passed"}
    session = state["sessions"].setdefault(day, {"date": day, "round": 0, "daily_keys": [],
        "items": {}, "pending": None, "history": []})
    for source, words in lists.items():
        for word in words:
            k = key(word)
            entry = state["words"].setdefault(k, {"word": word, "first_seen": day,
                "day_sources": {}, "reading": {"history": [], "needs_repair": False,
                "retention_due": None}, "listening": [], "speaking": []})
            entry["first_seen"] = min(day, entry["first_seen"])
            sources = entry["day_sources"].setdefault(day, [])
            if source not in sources:
                sources.append(source)
            if k not in session["daily_keys"]:
                session["daily_keys"].append(k)
    for k, entry in state["words"].items():
        reading = entry["reading"]
        due = reading["retention_due"]
        if k not in session["daily_keys"] and k not in unfinished and not reading["needs_repair"] and not (due and due <= day):
            continue
        need = required(entry, day)
        if k not in session["items"]:
            session["items"][k] = {"status": "unseen", "attempts": 0, "streak": 0,
                "misses": 0, "required_successes": need, "due_round": 1,
                "retention_check": bool(due and due <= day), "had_miss": reading["needs_repair"]}
        elif session["items"][k]["status"] != "passed":
            # A second collection never resets progress or manufactures new exposures.
            session["items"][k]["required_successes"] = max(need, session["items"][k]["required_successes"])
    return session


def session_for(state, day):
    if day not in state["sessions"]:
        raise ValueError("该日期尚未导入日档；先 collect / ingest")
    return state["sessions"][day]


def add_word(state, day, word, surface, meaning, source_round, reason):
    session = session_for(state, day)
    k = key(word)
    if not surface.strip() or not meaning.strip() or not reason.strip():
        raise ValueError("补充词需有实际词形、语境义和识别依据")
    if not isinstance(source_round, int) or not 1 <= source_round <= session["round"]:
        raise ValueError("补充词必须关联已回答的真实段落")
    source = next(r for r in session["history"] if r["round"] == source_round)
    if not re.search(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", source["text"], re.I):
        raise ValueError("补充词形未出现在来源段落")
    entry = state["words"].setdefault(k, {"word": word, "first_seen": day, "day_sources": {},
        "reading": {"history": [], "needs_repair": False, "retention_due": None}, "listening": [], "speaking": []})
    notes = entry.setdefault("manual_sources", [])
    if any(n.get("type") == "incidental_word" and n.get("passage_id") == source["id"] for n in notes):
        return
    notes.append({"date": day, "type": "incidental_word", "passage_id": source["id"],
        "source_round": source_round, "surface": surface, "meaning": meaning, "reason": reason})
    entry["gloss"] = meaning
    entry["reading"]["needs_repair"] = True
    entry["reading"]["retention_due"] = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    item = session["items"].setdefault(k, {"status": "unseen", "attempts": 0, "streak": 0,
        "misses": 0, "required_successes": 2, "due_round": 1, "retention_check": False, "had_miss": False})
    item.update(status="retry", streak=0, required_successes=max(2, item["required_successes"]),
        due_round=max(item["due_round"], source_round + 4), focus_reason="句中暴露的非目标生词")


def plan(state, day):
    session = session_for(state, day)
    items = session["items"]
    yesterday = prior(day)
    candidates = []
    for k, item in items.items():
        entry = state["words"][k]
        if item["status"] == "passed" or item["due_round"] > session["round"] + 1:
            continue
        flags = repeat_info(entry, day)
        missed_yesterday = any(e["date"] == yesterday and e["result"] in {"wrong", "unsure"}
                               for e in entry["reading"]["history"])
        score = (100 * (item["status"] == "retry") + 50 * entry["reading"]["needs_repair"]
                 + 30 * missed_yesterday + 15 * flags["focus"] + 2 * flags["review_days_7"])
        candidates.append({"word": entry["word"], "key": k, "score": score,
            "yesterday_wrong": missed_yesterday, "first_recorded_today": entry["first_seen"] == day,
            "app_new_today": "学习" in entry["day_sources"].get(day, []),
            "today_sources": entry["day_sources"].get(day, []), **flags,
            "required_successes": item["required_successes"], "streak": item["streak"],
            "recent_errors": [e for e in entry["reading"]["history"] if e["result"] in {"wrong", "unsure"}][-2:]})
    candidates.sort(key=lambda c: -c["score"])
    pending_count = sum(i["status"] != "passed" for i in items.values())
    return {"date": day, "round": session["round"], "daily_total": len(session["daily_keys"]),
        "daily_tested": sum(items[k]["attempts"] > 0 for k in session["daily_keys"]),
        "daily_passed": sum(items[k]["status"] == "passed" for k in session["daily_keys"]),
        "pool_total": len(items), "remaining": pending_count,
        "complete": pending_count == 0 and session["pending"] is None,
        "pending": session["pending"], "eligible": candidates,
        "bridge": pending_count > 0 and not candidates and session["pending"] is None}


def ask(state, day, text, targets, bridge=False):
    p = plan(state, day)
    if p["pending"]:
        raise ValueError("上一段尚未作答；保留并继续 pending，不覆盖题目")
    if not isinstance(text, str) or not text.strip() or not isinstance(targets, list):
        raise ValueError("需要英文段落和 targets 列表")
    eligible = {c["key"] for c in p["eligible"]}
    if bridge:
        if targets or not p["bridge"]:
            raise ValueError("仅在所有词等待间隔时使用无目标 bridge")
    elif not 1 <= len(targets) <= 4:
        raise ValueError("每段需要 1–4 个目标词")
    seen = set()
    for target in targets:
        k = key(target["word"])
        if k in seen or k not in eligible:
            raise ValueError(f"目标重复、已通过或未到间隔：{target['word']}")
        surface = target["surface"].strip()
        if not surface or not re.search(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", text, re.I):
            raise ValueError(f"段落中未找到目标实际词形：{surface}")
        if not target.get("meaning", "").strip():
            raise ValueError("每个目标需有当前语境义，保存但不提前展示")
        seen.add(k)
    question = {"id": uid(), "round": p["round"] + 1, "kind": "bridge" if bridge else "reading",
                "text": text, "targets": targets, "created_at": now()}
    state["sessions"][day]["pending"] = question
    return question


def grade(state, day, answer, results):
    session = session_for(state, day)
    q = session["pending"]
    if not q:
        raise ValueError("没有待答题目")
    if not isinstance(answer, str) or not answer.strip() or not isinstance(results, dict):
        raise ValueError("需要真实用户原答和逐词结果")
    normalized = {key(k): copy.deepcopy(v) for k, v in results.items()}
    if len(normalized) != len(results) or set(normalized) != {key(t["word"]) for t in q["targets"]}:
        raise ValueError("必须恰好判定 pending 的每个目标，不能添加或遗漏")
    for value in normalized.values():
        if not isinstance(value, dict) or value.get("result") not in RESULTS:
            raise ValueError("结果只可用 correct/wrong/unsure/not-tested")
        if not isinstance(value.get("reason"), str) or not value["reason"].strip():
            raise ValueError("逐词判定必须有简短依据")
        if value.get("hinted") and value["result"] == "correct":
            value["result"] = "unsure"
    for k, reason in q.get("grading_constraints", {}).items():
        if normalized[k]["result"] == "correct":
            normalized[k]["source_result"] = "correct"
            normalized[k]["result"] = "not-tested"
            normalized[k]["reason"] = reason
    r = session["round"] + 1
    for target in q["targets"]:
        k = key(target["word"])
        item, reading = session["items"][k], state["words"][k]["reading"]
        result = normalized[k]["result"]
        reading["history"].append({"event_id": uid(), "date": day, "round": r, "passage_id": q["id"],
            "result": result, "reason": normalized[k]["reason"], "meaning": target["meaning"],
            "surface": target["surface"], "hinted": bool(normalized[k].get("hinted")), "at": now()})
        if result != "not-tested":
            item["attempts"] += 1
        if result == "correct":
            item["streak"] += 1
            if item["streak"] >= item["required_successes"]:
                item["status"] = "passed"
                reading["needs_repair"] = False
                if item["had_miss"]:
                    reading["retention_due"] = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                elif item["retention_check"]:
                    reading["retention_due"] = None
            else:
                item["status"], item["due_round"] = "retry", r + 4
        elif result in {"wrong", "unsure"}:
            item["streak"] = 0
            item["misses"] += 1
            item["had_miss"] = True
            reading["needs_repair"] = True
            item["required_successes"] = max(2, required(state["words"][k], day))
            item["status"], item["due_round"] = "retry", r + 4
        else:
            item["due_round"] = max(item["due_round"], r + 1)
    session["round"] = r
    session["history"].append({**q, "answer": answer, "results": normalized, "answered_at": now()})
    session["pending"] = None
    return plan(state, day)


def export_pack(root, state, day):
    p = plan(state, day)
    folder = root / "exports" / f"{day}-练习包-r{state['revision']}"
    folder.mkdir(parents=True, exist_ok=True)
    portable = copy.deepcopy(state)
    portable["export_meta"] = {"base_revision": state["revision"], "day": day, "exported_at": now()}
    write(folder / "progress.json", portable)
    for d in [prior(day), day]:
        source = root / "daily" / f"{d}.json"
        if source.exists():
            write(folder / "daily" / source.name, read(source))
    rules = (SKILL / "references/portable-rules.md").read_text(encoding="utf-8")
    voice_enabled = state.get("preferences", {}).get("voice_enabled", False)
    voice_path = folder / "语音聊天.md"
    voice_tip = "想聊天则发送《语音聊天.md》和词表。" if voice_enabled else ""
    intro = (f"# {day} 雅思词汇练习包\n\n"
        "上传本文件和 progress.json 给任意支持文件的 AI，或只发送《手机粘贴版.md》。"
        f"说“开始练习”即可。{voice_tip}\n\n"
        "结束时说：“给我完整更新后的 progress.json，保留 export_meta.base_revision。”"
        "把返回文件带回本机导入。一次只让一位 AI 接力修改；没有工具的 AI 需输出 JSON 供保存。\n\n"
        f"当前：今日词表 {p['daily_total']} 词，已考 {p['daily_tested']}，本轮池待巩固 {p['remaining']}。"
        "首次建档之前的错误未知，不凭 App 重复反推具体错题。\n\n")
    (folder / "开始这里.md").write_text(intro + rules, encoding="utf-8")
    rows = []
    session = session_for(state, day)
    yesterday = prior(day)
    for k, item in session["items"].items():
        entry = state["words"][k]
        flags = repeat_info(entry, day)
        labels = list(entry["day_sources"].get(day, []))
        if entry["first_seen"] == day:
            labels.append("本档案首次收录")
        if any(e["date"] == yesterday and e["result"] in {"wrong", "unsure"} for e in entry["reading"]["history"]):
            labels.append("昨日错词")
        if flags["normal_new_review"]:
            labels.append("昨日新学·今日正常巩固")
        if flags["focus"]:
            labels.append("App反复复习·重点")
        if entry["reading"]["needs_repair"]:
            labels.append("待加强复测")
        if entry.get("self_reports"):
            labels.append("用户自报易忘／易混")
        if not entry["day_sources"].get(day) and entry.get("manual_sources"):
            labels.append("补充生词·非App日档")
        rows.append(f"| {entry['word']} | {entry.get('gloss', '')} | {'；'.join(labels) or '旧词保持检查'} | {item['status']} | {item['streak']}/{item['required_successes']} |")
    table = (f"# {day} 词表与待巩固项\n\n"
             "同词跨日保留，App 重复与陪练错答分别统计；unknown 不等于没有错误。\n\n"
             "中文为辅助整理的常用义；App今日词表与额外旧词依来源列区分。\n\n"
             "| 词条 | 常用义 | 来源 / 重点原因 | 状态 | 当前连续成功 / 本轮要求 |\n| --- | --- | --- | --- | --- |\n"
             + "\n".join(rows) + "\n")
    (folder / "今日词表.md").write_text(table, encoding="utf-8")
    handoff = {"kind": "practice-handoff", "schema_version": 1, "learner_id": state["learner_id"],
        "base_revision": state["revision"], "day": day, "completed": [], "pending": None, "voice_events": [], "extra_words": []}
    write(folder / "接力记录模板.json", handoff)
    light_rules = ("\n\n## 手机轻量接力方式\n\n"
        "本文件提供紧凑快照，无需完整历史或运行代码。按下方 schedule 调度；columns 对应每行的列。"
        "到期判断为 due_round <= round+1；status=passed 的词退出本轮。"
        "优先参考 focus 的原因，但不得跳过其他未考词。pending 存在时先续答原题。\n\n"
        "每次真实回答后，把题目与判定依序追加到接力记录 completed；每项格式为 "
        '`{"text":"英文原段","targets":[{"word":"词条","surface":"实际词形","meaning":"本句义"}],'
        '"kind":"reading","answer":"用户原答","results":{"词条":{"result":"correct","reason":"依据"}}}`。'
        'bridge 用 kind="bridge"、targets=[]、results={}。已出未答题放 pending（text/targets/kind），'
        "不要也放进 completed。快照中原有 pending 若已回答，应成为 completed 第一项。\n\n"
        "若用户不认识题中的非目标词，也加入补测，不因它不在App词表而忽略。"
        "在 extra_words 追加 word/surface/meaning/source_round/reason；source_round 是实际已答轮次。"
        "单复数可以明确对应，例如 facility / facilities；不要擅自合并派生词。"
        "补充词不计入App日档，加入 schedule，required_successes=2，due_round=source_round+4；"
        "未读懂不代表已有一次正式测验，不虚构 attempts 或错答。\n\n"
        "语音事件追加到 voice_events，每项 word/mode/result/note；mode 为 listening 或 speaking，"
        "result 为 exposed/correct/wrong/unsure/prompted。不算阅读尝试。\n\n"
        "结束时输出完整的《接力记录.json》，保留 learner_id、base_revision、day；"
        "只需携带本次新增问答和语音事件，旧历史保留在电脑。本机导入时会重放和验证间隔、覆盖率。"
        "轻量模式按这一接力格式回传，不必输出完整 progress.json。\n\n")
    context = {"day": day, "round": session["round"], "daily_keys": session["daily_keys"],
        "columns": ["word", "status", "attempts", "streak", "required_successes", "due_round", "had_miss", "retention_check"],
        "schedule": [[state["words"][k]["word"], i["status"], i["attempts"], i["streak"],
                      i["required_successes"], i["due_round"], i["had_miss"], i["retention_check"]]
                     for k, i in session["items"].items()],
        "focus": [{"word": c["word"], "yesterday_wrong": c["yesterday_wrong"],
                   "app_repeat": c["focus"], "recent_errors": c["recent_errors"]}
                  for c in p["eligible"] if c["yesterday_wrong"] or c["focus"] or c["recent_errors"]],
        "pending": session["pending"], "focus_groups": state.get("focus_groups", [])}
    if voice_enabled:
        voice = (SKILL / "references/voice-rules.md").read_text(encoding="utf-8")
        voice_path.write_text(voice + "\n\n" + table + light_rules
            + "\n```json\n" + json.dumps(handoff, ensure_ascii=False, separators=(",", ":")) + "\n```\n", encoding="utf-8")
    else:
        voice_path.unlink(missing_ok=True)
        light_rules = light_rules.replace(
            "语音事件追加到 voice_events，每项 word/mode/result/note；mode 为 listening 或 speaking，"
            "result 为 exposed/correct/wrong/unsure/prompted。不算阅读尝试。\n\n", "")
        light_rules = light_rules.replace("本次新增问答和语音事件", "本次新增问答")
    # Compact schedules and append-only new answers keep old history off phone context.
    mobile = f"# {day} 雅思词汇手机陪练\n\n发送本文件后说“开始练习”。此包对应 {day}，不自动读取手机 App。\n\n"
    mobile += rules + light_rules + "## 当前快照\n\n```json\n"
    mobile += json.dumps(context, ensure_ascii=False, separators=(",", ":")) + "\n```\n\n## 接力记录初始模板\n\n```json\n"
    mobile += json.dumps(handoff, ensure_ascii=False, separators=(",", ":")) + "\n```\n"
    (folder / "手机粘贴版.md").write_text(mobile, encoding="utf-8")
    archive = folder.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                z.write(file, str(Path(folder.name) / file.relative_to(folder)))
    return {"folder": str(folder), "zip": str(archive), "mobile": str(folder / "手机粘贴版.md"),
            "voice": str(voice_path) if voice_enabled else None, "base_revision": state["revision"]}


def import_progress(root, local, remote):
    if remote.get("schema_version") != 1 or remote.get("learner_id") != local["learner_id"]:
        raise ValueError("不是同一份学习档案或版本不兼容")
    if remote.get("export_meta", {}).get("base_revision") != local["revision"]:
        raise ValueError("导出后本机已有新进度，拒绝用旧包覆盖；保留两份后核对合并")
    if not isinstance(remote.get("revision"), int) or remote["revision"] < local["revision"]:
        raise ValueError("回传 revision 无效")
    for k, old in local["words"].items():
        new = remote.get("words", {}).get(k)
        if not new or key(new["word"]) != k or new["first_seen"] != old["first_seen"]:
            raise ValueError(f"回传丢失或改写词条：{k}")
        for d, sources in old["day_sources"].items():
            if not set(sources) <= set(new["day_sources"].get(d, [])):
                raise ValueError("回传删除了既有日档来源")
        for mode in ["reading", "listening", "speaking"]:
            a = old[mode]["history"] if mode == "reading" else old[mode]
            b = new[mode]["history"] if mode == "reading" else new[mode]
            if not isinstance(b, list) or b[:len(a)] != a:
                raise ValueError("回传改写了既有答题历史")
    for d, old in local["sessions"].items():
        new = remote.get("sessions", {}).get(d)
        if not new or new["history"][:len(old["history"])] != old["history"]:
            raise ValueError("回传丢失或改写既有题目")
        if new["round"] != len(new["history"]) or new["round"] < old["round"]:
            raise ValueError("回传轮数与真实答题记录不符")
        if not set(old["daily_keys"]) <= set(new["daily_keys"]):
            raise ValueError("回传遗漏当天词表")
        if not set(old["items"]) <= set(new["items"]) or not set(new["daily_keys"]) <= set(new["items"]):
            raise ValueError("回传遗漏本轮目标词")
        if old["pending"] and len(old["history"]) == len(new["history"]):
            if not new["pending"] or old["pending"]["text"] != new["pending"]["text"]:
                raise ValueError("回传删除了未答题目")
        for k, item in new["items"].items():
            if k not in remote["words"] or item["status"] not in {"unseen", "retry", "passed"}:
                raise ValueError("回传目标状态无效")
            if item["status"] == "passed" and item["streak"] < item["required_successes"]:
                raise ValueError("回传把未满足次数的词标成通过")
    if set(remote["sessions"]) != set(local["sessions"]) or set(remote["words"]) != set(local["words"]):
        raise ValueError("回传包含未导入的日期或词表；先单独核对新日档")
    # Derive counters from new answer evidence rather than trusting AI-edited totals.
    work = copy.deepcopy(local)
    for d, old in local["sessions"].items():
        new = remote["sessions"][d]
        handoff = {"kind": "practice-handoff", "schema_version": 1, "learner_id": local["learner_id"],
            "base_revision": work["revision"], "day": d,
            "completed": new["history"][len(old["history"]):], "pending": new["pending"], "voice_events": []}
        work = import_handoff(work, handoff)
    for k, old in local["words"].items():
        for mode in ["listening", "speaking"]:
            for event in remote["words"][k][mode][len(old[mode]):]:
                if event.get("result") not in {"exposed", "correct", "wrong", "unsure", "prompted"} or not event.get("note"):
                    raise ValueError("语音记录缺少有效证据")
                work["words"][k][mode].append(copy.deepcopy(event))
                work["revision"] += 1
    write(root / "backups" / f"before-import-r{local['revision']}-{uid()[:8]}.json", local)
    return work


def observed_question(state, day, record):
    """Keep real external questions even when incidental vocabulary breaks planning rules."""
    session = session_for(state, day)
    targets, text = record["targets"], record["text"]
    if not isinstance(text, str) or not text.strip() or not isinstance(targets, list):
        raise ValueError("外部原题格式无效")
    constraints, seen = {}, set()
    for target in targets:
        k = key(target["word"])
        if k in seen or k not in session["items"]:
            raise ValueError("外部原题含未知或重复目标")
        seen.add(k)
        surface = target["surface"].strip()
        if not surface or not re.search(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", text, re.I) or not target.get("meaning"):
            raise ValueError("外部原题目标没有真实出现或缺少语境义")
        item = session["items"][k]
        if item["status"] == "passed":
            constraints[k] = "已通过词在原段中顺带出现，保留作答证据，不重复累计成功。"
        elif item["due_round"] > session["round"] + 1:
            constraints[k] = "原段复测尚未隔开三段真实问答，保留作答证据，不计独立巩固成功。"
    question = {"id": uid(), "round": session["round"] + 1, "kind": record.get("kind", "reading"),
        "text": text, "targets": copy.deepcopy(targets), "created_at": now(),
        "external_observation": True, "grading_constraints": constraints}
    if "results" in record:
        question["source_results"] = copy.deepcopy(record["results"])
    session["pending"] = question
    return question


def import_handoff(local, handoff, observed=False):
    if (handoff.get("kind") != "practice-handoff" or handoff.get("schema_version") != 1
            or handoff.get("learner_id") != local["learner_id"]):
        raise ValueError("接力文件不属于这份学习档案")
    if handoff.get("base_revision") != local["revision"]:
        raise ValueError("导出后本机已有新进度，保留两份记录，不能用旧接力包覆盖")
    day = handoff["day"]
    work = copy.deepcopy(local)
    session = session_for(work, day)
    extras = list(handoff.get("extra_words", []))
    def add_due_extras():
        for extra in extras[:]:
            if not isinstance(extra.get("source_round"), int):
                raise ValueError("补充词缺少有效来源轮次")
            if extra["source_round"] <= session["round"]:
                add_word(work, day, extra["word"], extra["surface"], extra["meaning"], extra["source_round"], extra["reason"])
                extras.remove(extra)
    add_due_extras()
    for record in handoff.get("completed", []):
        was_pending = session["pending"] is not None
        if session["pending"]:
            q = session["pending"]
            if record["text"] != q["text"] or record["targets"] != q["targets"]:
                raise ValueError("接力第一题未续答原先 pending，保留文件后核对")
        else:
            if observed:
                observed_question(work, day, record)
            else:
                ask(work, day, record["text"], record["targets"], record.get("kind") == "bridge")
        grade(work, day, record["answer"], record["results"])
        add_due_extras()
        work["revision"] += 1 if was_pending else 2
    if extras:
        raise ValueError("补充词的来源轮次尚未实际作答")
    pending = handoff.get("pending")
    if pending:
        if session["pending"]:
            if pending["text"] != session["pending"]["text"] or pending["targets"] != session["pending"]["targets"]:
                raise ValueError("回传 pending 与本机原题不一致")
        else:
            if observed:
                observed_question(work, day, pending)
            else:
                ask(work, day, pending["text"], pending["targets"], pending.get("kind") == "bridge")
            work["revision"] += 1
    for event in handoff.get("voice_events", []):
        k = key(event["word"])
        if k not in work["words"] or event["mode"] not in {"listening", "speaking"}:
            raise ValueError("语音接力的词条或模式无效")
        if event["result"] not in {"exposed", "correct", "wrong", "unsure", "prompted"} or not event.get("note"):
            raise ValueError("语音接力缺少有效证据")
        work["words"][k][event["mode"]].append({"event_id": uid(), "date": day,
            "result": event["result"], "note": event["note"], "at": now()})
        work["revision"] += 1
    return work


def self_test():
    # Exercise actual state transitions in isolation; never touch the real archive.
    def daily(d, learn=(), review=()):
        lists = {"学习": list(learn), "复习": list(review), "已标熟": []}
        return {"date": d, "lists": lists, "counts": {k: len(v) for k, v in lists.items()}}
    def question(s, d, word):
        return ask(s, d, f"They allocate resources to {word}.", [{"word": word, "surface": word, "meaning": "测试义"}])
    def result(s, d, word, value):
        return grade(s, d, "用户真实测试回答", {word: {"result": value, "reason": "测试证据"}})
    d1, d2, d3 = "2026-10-24", "2026-10-25", "2026-10-26"
    supplemental = empty_state()
    ingest(supplemental, daily(d1, review=["alpha"]))
    ask(supplemental, d1, "Alpha needs better facilities.", [{"word": "alpha", "surface": "Alpha", "meaning": "测试义"}])
    result(supplemental, d1, "alpha", "correct")
    add_word(supplemental, d1, "facility", "facilities", "设施", 1, "用户未认出")
    snapshot = copy.deepcopy(supplemental)
    add_word(supplemental, d1, "facility", "facilities", "设施", 1, "用户未认出")
    assert supplemental == snapshot
    assert plan(supplemental, d1)["daily_total"] == 1 and plan(supplemental, d1)["pool_total"] == 2
    assert supplemental["sessions"][d1]["items"]["facility"]["due_round"] == 5
    assert supplemental["words"]["facility"]["day_sources"] == {}
    handoff = {"kind": "practice-handoff", "schema_version": 1, "learner_id": supplemental["learner_id"],
        "base_revision": supplemental["revision"], "day": d1, "completed": [],
        "extra_words": [{"word": "facility", "surface": "facilities", "meaning": "设施", "source_round": 1, "reason": "用户未认出"}]}
    assert import_handoff(supplemental, handoff)["sessions"][d1]["items"]["facility"]["due_round"] == 5
    s = empty_state()
    carry = empty_state()
    ingest(carry, daily(d1, review=["alpha", "beta"]))
    question(carry, d1, "alpha")
    result(carry, d1, "alpha", "correct")
    ingest(carry, daily(d2))
    assert set(carry["sessions"][d2]["items"]) == {"beta"}  # unfinished coverage survives day change
    ingest(s, daily(d1, learn=["newword"], review=["repeat", "forget"]))
    question(s, d1, "forget")
    result(s, d1, "forget", "wrong")
    assert "forget" not in [x["key"] for x in plan(s, d1)["eligible"]]
    question(s, d1, "newword")
    # Existing pending survives subsequent plan and same-day ingestion.
    pending = copy.deepcopy(plan(s, d1)["pending"])
    ingest(s, daily(d1, learn=["newword"], review=["repeat", "forget"]))
    assert plan(s, d1)["pending"] == pending
    result(s, d1, "newword", "correct")
    question(s, d1, "repeat")
    result(s, d1, "repeat", "not-tested")
    assert s["sessions"][d1]["items"]["repeat"]["attempts"] == 0
    question(s, d1, "repeat")
    result(s, d1, "repeat", "correct")
    assert "forget" in [x["key"] for x in plan(s, d1)["eligible"]]
    question(s, d1, "forget")
    result(s, d1, "forget", "correct")
    assert plan(s, d1)["bridge"]
    for _ in range(3):
        ask(s, d1, "People need clean water. What do they need?", [], True)
        grade(s, d1, "干净的水", {})
    question(s, d1, "forget")
    result(s, d1, "forget", "correct")
    assert plan(s, d1)["complete"]
    assert s["words"]["forget"]["reading"]["retention_due"] == d2
    observed = empty_state()
    ingest(observed, daily(d1, review=["alpha"]))
    question(observed, d1, "alpha")
    result(observed, d1, "alpha", "wrong")
    due = observed["sessions"][d1]["items"]["alpha"]["due_round"]
    early = {"text": "They allocated resources to alpha.", "targets": [{"word": "alpha", "surface": "alpha", "meaning": "测试义"}]}
    observed_question(observed, d1, early)
    result(observed, d1, "alpha", "correct")
    item = observed["sessions"][d1]["items"]["alpha"]
    assert item["streak"] == 0 and item["due_round"] == due and item["attempts"] == 1
    assert observed["sessions"][d1]["history"][-1]["results"]["alpha"]["source_result"] == "correct"
    # A genuine error in an incidental repeat remains useful evidence.
    observed_question(observed, d1, early)
    result(observed, d1, "alpha", "wrong")
    assert item["misses"] == 2
    ingest(s, daily(d2, review=["newword", "repeat"]))
    assert repeat_info(s["words"]["newword"], d2)["normal_new_review"]
    assert not repeat_info(s["words"]["newword"], d2)["focus"]
    assert repeat_info(s["words"]["repeat"], d2)["focus"]
    assert s["sessions"][d2]["items"]["repeat"]["required_successes"] == 2
    assert "forget" in s["sessions"][d2]["items"]  # retention even absent from today's App list
    question(s, d2, "forget")
    result(s, d2, "forget", "correct")
    assert s["words"]["forget"]["reading"]["retention_due"] is None
    question(s, d2, "repeat")
    result(s, d2, "repeat", "wrong")
    ingest(s, daily(d3, review=["repeat", "newword"]))
    assert repeat_info(s["words"]["newword"], d3)["focus"]  # new -> review -> review signals priority
    info = plan(s, d3)["eligible"][0]
    assert info["key"] == "repeat" and info["yesterday_wrong"]
    assert len(s["words"]["repeat"]["day_sources"]) == 3
    before = copy.deepcopy(s)
    ingest(s, daily(d3, review=["repeat", "newword"]))
    assert s == before  # same-day collection is idempotent
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        s["preferences"] = {"voice_enabled": True}
        save(root, s)
        pack = export_pack(root, s, d3)
        remote = read(Path(pack["folder"]) / "progress.json")
        question(remote, d3, "repeat")
        result(remote, d3, "repeat", "correct")
        remote["revision"] += 2
        merged = import_progress(root, s, remote)
        assert merged["sessions"][d3]["round"] == 1
        stale = copy.deepcopy(s)
        stale["revision"] += 1
        try:
            import_progress(root, stale, remote)
        except ValueError:
            pass
        else:
            raise AssertionError("stale exports must not overwrite local work")
        assert read(root / "progress.json")["revision"] == s["revision"]
        with zipfile.ZipFile(pack["zip"]) as z:
            assert any(name.endswith("语音聊天.md") for name in z.namelist())
        reading_only = copy.deepcopy(s)
        reading_only["preferences"]["voice_enabled"] = False
        quiet_pack = export_pack(root / "reading-only", reading_only, d3)
        assert quiet_pack["voice"] is None
        assert not (Path(quiet_pack["folder"]) / "语音聊天.md").exists()
        with zipfile.ZipFile(quiet_pack["zip"]) as z:
            assert not any(name.endswith("语音聊天.md") for name in z.namelist())
        handoff = read(Path(pack["folder"]) / "接力记录模板.json")
        trial = copy.deepcopy(s)
        q = question(trial, d3, "repeat")
        handoff["completed"] = [{"text": q["text"], "targets": q["targets"], "kind": "reading",
            "answer": "真实回答", "results": {"repeat": {"result": "correct", "reason": "语义正确"}}}]
        handoff["voice_events"] = [{"word": "repeat", "mode": "listening", "result": "exposed", "note": "模型自然提到"}]
        replayed = import_handoff(s, handoff)
        assert replayed["sessions"][d3]["round"] == 1
        assert replayed["words"]["repeat"]["listening"][-1]["result"] == "exposed"
        assert replayed["sessions"][d3]["items"]["repeat"]["streak"] == 1
        # Early retests cannot bypass spacing through the lightweight importer.
        handoff["completed"].append(copy.deepcopy(handoff["completed"][0]))
        try:
            import_handoff(s, handoff)
        except ValueError:
            pass
        else:
            raise AssertionError("handoff must respect spacing")
    print("PASS: 跨日重复、三段间隔、补充生词、断点续接、跨设备回传、旧包冲突、ZIP")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--day", default=today())
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ingest"); p.add_argument("--file", type=Path)
    sub.add_parser("plan")
    p = sub.add_parser("ask"); p.add_argument("--text", required=True); p.add_argument("--targets-json", required=True); p.add_argument("--bridge", action="store_true")
    p = sub.add_parser("grade"); p.add_argument("--answer", required=True); p.add_argument("--results-json", required=True)
    p = sub.add_parser("add-word"); p.add_argument("--word", required=True); p.add_argument("--surface", required=True); p.add_argument("--meaning", required=True); p.add_argument("--source-round", type=int, required=True); p.add_argument("--reason", required=True)
    sub.add_parser("export")
    p = sub.add_parser("import-progress"); p.add_argument("--file", type=Path, required=True); p.add_argument("--observed", action="store_true", help="接纳已核对的真实外部题目；顺带出现和过早复测不累计成功")
    p = sub.add_parser("log-voice"); p.add_argument("--word", required=True); p.add_argument("--mode", choices=["listening", "speaking"], required=True); p.add_argument("--result", choices=["exposed", "correct", "wrong", "unsure", "prompted"], required=True); p.add_argument("--note", required=True)
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "self-test":
        self_test(); return
    date.fromisoformat(args.day)
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(root)
        if args.command == "ingest":
            daily = read(args.file or root / "daily" / f"{args.day}.json")
            if daily["date"] != args.day:
                raise ValueError("日档日期与所选日期不一致，禁止把旧日期当今天")
            before = copy.deepcopy(state)
            ingest(state, daily)
            if before != state:
                save(root, state)
            output = plan(state, args.day)
        elif args.command == "plan":
            output = plan(state, args.day)
        elif args.command == "ask":
            output = ask(state, args.day, args.text, json.loads(args.targets_json), args.bridge)
            save(root, state)
        elif args.command == "grade":
            output = grade(state, args.day, args.answer, json.loads(args.results_json))
            save(root, state)
        elif args.command == "add-word":
            before = copy.deepcopy(state)
            add_word(state, args.day, args.word, args.surface, args.meaning, args.source_round, args.reason)
            if state != before:
                save(root, state)
            output = {"word": args.word, "daily_total": len(session_for(state, args.day)["daily_keys"]),
                "item": session_for(state, args.day)["items"][key(args.word)]}
        elif args.command == "export":
            output = export_pack(root, state, args.day)
        elif args.command == "import-progress":
            remote = read(args.file)
            if remote.get("kind") == "practice-handoff":
                next_state = import_handoff(state, remote, observed=args.observed)
                write(root / "backups" / f"before-handoff-r{state['revision']}-{uid()[:8]}.json", state)
                state = next_state
            else:
                state = import_progress(root, state, remote)
            save(root, state)
            output = {"imported": True, "revision": state["revision"]}
        else:
            k = key(args.word)
            if k not in state["words"]:
                raise ValueError("词条不在档案中，先导入词表")
            state["words"][k][args.mode].append({"event_id": uid(), "date": args.day,
                "result": args.result, "note": args.note, "at": now()})
            save(root, state)
            output = {"recorded": args.mode, "word": state["words"][k]["word"]}
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise SystemExit(f"未完成：{exc}")
