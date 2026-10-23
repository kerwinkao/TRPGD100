"""資料檢查:skills/core.json、play/npc/templates.json 符合 schema 約束;模板能正確轉成 Build。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import npc
from progression import cum_cost

CORE = npc.load_core()
TPL = npc.load_templates()


def test_core_table_size_and_constraints():
    assert 30 <= len(CORE) <= 40
    assert npc.validate(CORE, TPL) == []


def test_core_covers_required_categories():
    need = ["長劍", "單刀", "長槍", "長棍", "拳腳", "招架", "格擋", "閃避", "步法"]
    assert all(n in CORE for n in need)
    assert CORE["讀寫"]["untrained"] == "不可"
    assert sum(1 for s in CORE.values() if s["untrained"] == "不可") <= 3     # 不可清單極短


def test_core_matches_schema_examples():
    # schema example / calibration 給定的三屬性不得被改
    assert set(CORE["長劍"]["attributes"]) == {"Str", "Ski", "Agi"}
    assert set(CORE["招架"]["attributes"]) == {"Ski", "Str", "Wis"}
    assert set(CORE["閃避"]["attributes"]) == {"Agi", "Con", "Wis"}
    assert set(CORE["戰鬥反應"]["attributes"]) == {"Agi", "Ski", "Wis"}
    assert set(CORE["察覺"]["attributes"]) == {"Wis", "Int", "Cha"}
    assert set(CORE["內功"]["attributes"]) == {"Con", "Cha", "Wis"}
    assert set(CORE["化妝"]["attributes"]) == {"Ski", "Cha", "Wis"}


def test_template_set():
    names = {t["name"] for t in TPL}
    assert {"鏢師", "宗門弟子", "山賊"} <= names and len(TPL) == 6
    for t in TPL:
        tiers = {e["skill"]: e["tier"] for e in t["skills"]}
        for k, v in tiers.items():
            assert v == (2 if t["name"] == "宗門弟子" and k == "本門劍法" else 1), (t["name"], k)


def test_readme_numbers_preserved():
    t = {x["name"]: x for x in TPL}
    bs = {e["skill"]: e for e in t["鏢師"]["skills"]}
    assert bs["單刀"]["level"] == [2, 4] and bs["招架"]["level"] == [2, 3]
    assert bs["招架"]["subskills"] == [{"name": "反擊", "level": 1}]
    assert t["鏢師"]["equipment"] == ["單刀", "小盾"] and t["鏢師"]["attr_lean"]["high"] == ["Str", "Con"]
    zs = {e["skill"]: e for e in t["宗門弟子"]["skills"]}
    assert zs["本門劍法"]["level"] == [2, 3] and zs["本門劍法"]["tier"] == 2 and zs["步法"]["level"] == [2, 2]
    sz = t["山賊"]
    assert all(e["tier"] == 1 and 1 <= e["level"][0] and e["level"][1] <= 2 for e in sz["skills"])


def test_instantiate_biaoshi():
    t = next(x for x in TPL if x["name"] == "鏢師")
    b = npc.instantiate(t, "中", CORE)
    assert (b.attrs["Str"], b.attrs["Con"], b.attrs["Ski"]) == (16, 16, 14)
    # 單刀 Str+Ski+Con = 46,中檔 3 級 → 76
    assert b.weapon == 3 and b.value("weapon") == 16 + 14 + 16 + 30
    assert b.shield == "small" and b.block == 1 and b.counter == 1 and b.link_w2p == 0
    assert b.weapon_die == 8 and b.weapon_attr == "Str" and b.strength == 16


def test_instantiate_grades_and_placeholder():
    t = next(x for x in TPL if x["name"] == "宗門弟子")
    lo, hi = npc.instantiate(t, "弱", CORE), npc.instantiate(t, "強", CORE)
    assert (lo.weapon, hi.weapon) == (2, 3) and lo.step == hi.step == 2 and lo.step_def == "dodge"
    # 本門劍法沿用長劍三屬性(Str+Ski+Agi),Ski、Agi 傾向高 → 14+16+16
    assert lo.base["weapon"] == 46


def test_link_from_weapon_subskill():
    t = next(x for x in TPL if x["name"] == "遊俠")
    assert npc.instantiate(t, "中", CORE).link_w2p == 1


def test_xp_cost_uses_tier_multiplier():
    t = next(x for x in TPL if x["name"] == "宗門弟子")
    lv = npc.level_map(t, "弱")
    cost = npc.xp_cost(t, lv)
    expect = (cum_cost(2, 2) + cum_cost(2) + cum_cost(1) + cum_cost(1) + cum_cost(1)   # 本門2 步法2 招架 閃避 讀寫
              + cum_cost(1, 2) / 10)                       # 專屬副技能位置 1 級:主技能難度 2 → 12
    assert abs(cost - expect) < 1e-6


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            n += 1
    print(f"{n} tests passed")
