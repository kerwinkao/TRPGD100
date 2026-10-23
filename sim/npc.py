"""NPC 模板 → fight.Build。資料來源:skills/core.json、play/npc/templates.json(與桌上共用同一份)。

以下是**模擬假設**,不是規則;寫進 sim/REPORT.md 與 design/ambiguities.md(A36 起):
- 屬性傾向數值化:傾向 high = 16、low = 12、其餘 14(模板只寫傾向,沒給數值)。
- 等級檔位:弱 = 範圍下限、強 = 範圍上限、中 = 下限與上限的中位(無條件捨去)。寬度 1 的範圍 中 = 弱。
- 武器骰與傷害補正屬性:劍/刀/槍/棍 1d8、拳腳 1d4;劍/刀/棍 取 Str 效益值(重兵)、槍/拳腳 取 Ski 效益值(輕兵)。
- 有步法等級的 NPC,守方固定宣告步法輔助閃避(沿用 Session 2 的 step_def)。
- 多把武器技能的模板(山賊):取技能清單中第一個「裝備裡有」的武器技能為主用。
- 小盾 / 大盾 在裝備裡才算帶盾;帶盾但模板沒列格擋 → 格擋 0 級(底值照擲)。
"""
from __future__ import annotations

import json
import os
from typing import Optional

from fight import Build
from progression import LEVEL_CAP, cum_cost, subskill_cost

ROOT = os.path.join(os.path.dirname(__file__), "..")
CORE_PATH = os.path.join(ROOT, "skills", "core.json")
TPL_PATH = os.path.join(ROOT, "play", "npc", "templates.json")

WEAPONS = {"長劍": (8, "Str"), "單刀": (8, "Str"), "長槍": (8, "Ski"), "長棍": (8, "Str"),
           "拳腳": (4, "Ski")}
SHIELDS = {"小盾": "small", "大盾": "large"}
COMBAT_KEYS = {"招架": "parry", "格擋": "block", "閃避": "dodge", "步法": "step"}
GRADES = ("弱", "中", "強")
HIGH, LOW, MID = 16, 12, 14


def load_core() -> dict:
    with open(CORE_PATH, encoding="utf-8") as f:
        return {s["name"]: s for s in json.load(f)["skills"]}


def load_templates() -> list:
    with open(TPL_PATH, encoding="utf-8") as f:
        return json.load(f)["templates"]


def lean_attrs(t: dict) -> dict:
    a = {c: MID for c in ("Str", "Con", "Ski", "Agi", "Int", "Wis", "Cha")}
    for c in t["attr_lean"]["high"]:
        a[c] = HIGH
    for c in t["attr_lean"]["low"]:
        a[c] = LOW
    return a


def grade_level(rng2: list, grade: str) -> int:
    lo, hi = rng2
    return {"弱": lo, "強": hi, "中": (lo + hi) // 2}[grade]


def skill_attrs(entry: dict, core: dict) -> list:
    """模板技能條目 → 三屬性。placeholder(本門劍法)沿用 based_on 的核心技能。"""
    name = entry["based_on"] if entry.get("placeholder") else entry["skill"]
    return core[name]["attributes"]


def base_of(entry: dict, core: dict, attrs: dict) -> int:
    return sum(attrs[a] for a in skill_attrs(entry, core))


def weapon_entry(t: dict) -> Optional[dict]:
    """主用武器技能:技能清單中第一個「裝備裡有」的武器技能。"""
    for e in t["skills"]:
        src = e["based_on"] if e.get("placeholder") else e["skill"]
        if src in WEAPONS and src in t["equipment"]:
            return e
    return None


def instantiate(t: dict, grade: str = "中", core: Optional[dict] = None, name: Optional[str] = None,
                levels: Optional[dict] = None) -> Build:
    """levels = {技能名: 等級} 可覆寫(同經驗實驗用);否則依 grade 取範圍檔位。"""
    core = core or load_core()
    attrs = lean_attrs(t)
    lv = {e["skill"]: (levels[e["skill"]] if levels and e["skill"] in levels
                       else grade_level(e["level"], grade)) for e in t["skills"]}
    ent = {e["skill"]: e for e in t["skills"]}
    w = weapon_entry(t)
    kw: dict = dict(name=name or f"{t['name']}·{grade}", attrs=attrs, strength=attrs["Str"], base={})
    if w is not None:
        src = w["based_on"] if w.get("placeholder") else w["skill"]
        die, battr = WEAPONS[src]
        kw.update(weapon=lv[w["skill"]], weapon_name=w["skill"], weapon_die=die, weapon_attr=battr)
        kw["base"]["weapon"] = base_of(w, core, attrs)
        subs = {x["name"]: x["level"] for x in w.get("subskills", []) if x["name"]}
        kw["link_w2p"] = subs.get("招架", 0)
    for cname, key in COMBAT_KEYS.items():
        if cname in ent:
            kw[key] = lv[cname]
            kw["base"][key] = base_of(ent[cname], core, attrs)
        else:
            kw["base"][key] = sum(attrs[a] for a in core[cname]["attributes"])
    if "招架" in ent:
        psubs = {x["name"]: x["level"] for x in ent["招架"].get("subskills", []) if x["name"]}
        kw["counter"] = psubs.get("反擊", 0)
        if w is not None:
            kw["link_p2w"] = psubs.get(w["skill"], 0)
    shield = next((SHIELDS[e] for e in t["equipment"] if e in SHIELDS), None)
    if shield:
        kw["shield"] = shield
        kw["block"] = lv.get("格擋", 0)
    if kw.get("step", 0) > 0:
        kw["step_def"] = "dodge"
    return Build(**kw)


def xp_cost(t: dict, levels: dict) -> float:
    """模板實例的總經驗:各技能累計表 × 持有難度階 + 已持副技能(主技能同級 1/10,A15)。"""
    total = 0.0
    for e in t["skills"]:
        L = levels[e["skill"]]
        total += cum_cost(L, e["tier"])
        for sub in e.get("subskills", []):
            total += subskill_cost(sub["level"], e["tier"])
    return total


def level_map(t: dict, grade: str) -> dict:
    return {e["skill"]: grade_level(e["level"], grade) for e in t["skills"]}


def combat_skills(t: dict) -> list:
    """對戰鬥有作用的技能條目:主用武器 + 招架 / 格擋 / 閃避 / 步法。"""
    w = weapon_entry(t)
    return [e for e in t["skills"] if (w is not None and e is w) or e["skill"] in COMBAT_KEYS]


def validate(core: dict, templates: list) -> list:
    """資料檢查:回傳違規清單(空 = 通過)。"""
    bad = []
    codes = {"Str", "Con", "Ski", "Agi", "Int", "Wis", "Cha"}
    fams = set()
    for n, s in core.items():
        if len(s["attributes"]) != 3 or len(set(s["attributes"])) != 3 or not set(s["attributes"]) <= codes:
            bad.append(f"{n}: 三屬性不合法")
        if s["lineage"]["base_tier"] != 1 or s["max_tier"] != 5:
            bad.append(f"{n}: 核心入門技能應 base_tier 1、max_tier 5")
        if len(s["subskills"]) > 1:
            bad.append(f"{n}: 副技能超過一個")
        if any(x["difficulty"] != 1 for x in s["subskills"]):
            bad.append(f"{n}: 副技能難度非 1")
        if s["family"] in fams:
            bad.append(f"{n}: family 重複 {s['family']}")
        fams.add(s["family"])
    for t in templates:
        for e in t["skills"]:
            nm = e["skill"]
            if not e.get("placeholder") and nm not in core:
                bad.append(f"{t['name']}: 未知技能 {nm}")
            lo, hi = e["level"]
            if not (0 <= lo <= hi <= LEVEL_CAP):
                bad.append(f"{t['name']}.{nm}: 等級範圍")
            for sub in e.get("subskills", []):
                if sub["level"] > e["tier"]:
                    bad.append(f"{t['name']}.{nm}: 副技能等級 {sub['level']} > 難度階 {e['tier']}")
                if sub["name"] and not e.get("placeholder"):
                    held = [x for x in core[nm]["subskills"] if x["name"] == sub["name"]]
                    if not held:
                        bad.append(f"{t['name']}.{nm}: 副技能 {sub['name']} 不在核心表")
                    elif held[0]["difficulty"] > e["tier"]:
                        bad.append(f"{t['name']}.{nm}: 副技能難度 > 難度階")
    return bad
