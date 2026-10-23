"""resolve.py 的規則對照測試。可用 pytest,也可直接 `python3 sim/tests/test_resolve.py`。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from resolve import (Layer, Params, UsageLog, block, check, crit_zone, damage, damage_s1,
                     difficulty, dodge, erode, fumble_floor, overflow, parry, shield_value, step)


class Fixed:
    """注入式 rng:依序吐出預設值。"""
    def __init__(self, *vals):
        self.vals = list(vals)

    def randint(self, a, b):
        v = self.vals.pop(0)
        assert a <= v <= b
        return v


def test_crit_zone_examples():
    # resolution.md 範例:52 → 01–05;125 → 01–13;150 + 致命副技能三級 → 01–18
    # 公式 ceil 給 52 → 06,與範例不符(A0);half_up 三例皆符
    assert crit_zone(52) == 6
    assert crit_zone(125) == 13
    assert crit_zone(150, 3) == 18
    assert [crit_zone(s, b, "half_up") for s, b in ((52, 0), (125, 0), (150, 3))] == [5, 13, 18]


def test_fumble_examples():
    # 52 → 96–99 + 00;≥100 只剩 00
    assert fumble_floor(52) == 95
    assert fumble_floor(100) == 99 and fumble_floor(150) == 99
    assert check(52, Fixed(96)).fumble
    assert not check(52, Fixed(95)).fumble
    assert check(150, Fixed(100)).fumble


def test_basic_example():
    r = check(52, Fixed(17))
    assert r.success and r.margin == 35 and not r.crit


def test_overflow_always_succeeds_except_00():
    assert check(125, Fixed(99)).success
    assert not check(125, Fixed(100)).success
    assert check(125, Fixed(100), params=Params(fumble_beats_overflow=False)).success
    assert overflow(125) == 25 and overflow(90) == 0


def test_reroll_only_clears_00():
    assert check(52, Fixed(100, 30), rerolls=1).success
    r = check(52, Fixed(80), rerolls=1)        # 一般失敗不重骰
    assert not r.success and r.roll == 80


def test_erode_chain():
    L = [Layer("閃避", 52, 1), Layer("招架", 52, 1), Layer("格擋", 52, 1)]
    # T=44:閃避 m=40 吃 10 → 34;招架 m=41 ≥ 34 → 完全防禦
    T, steps = erode(44, L, Fixed(12, 11))
    assert T == 0 and [s.outcome for s in steps] == ["erode", "full"]
    # 'capped':m=41 但上限 10,不夠完全防禦,吃 10 → 24;格擋失敗
    T, steps = erode(44, L, Fixed(12, 11, 80), Params(full_defense="capped"))
    assert T == 24


def test_erode_fumble_modes():
    L = [Layer("閃避", 52, 1), Layer("招架", 52, 1)]
    T, _ = erode(30, L, Fixed(100, 45))       # 該層失效,下一層 m=7 吃 7
    assert T == 23
    T, steps = erode(30, L, Fixed(100), Params(defense_fumble="chain_break"))
    assert T == 30 and len(steps) == 1


def test_difficulty_example():
    # 陷阱 75,陷阱種類判定二級通過,最多削 20 → 55
    assert difficulty(75, [Layer("陷阱知識", 62, 2)], Fixed(10)) == 55


def test_damage_s1():
    # Session 1 傷判(舊簽名改名 damage_s1;A8 已裁 flat,pct 預設改為明確指定)
    pct = Params(overflow_damage="pct")
    assert damage_s1(8, 10, 25, True, Fixed(), pct) == 10      # 溢出致命滿骰 8 × 1.25
    assert damage_s1(8, 10, 0, True, Fixed(3), pct) == 3       # 無溢出致命不滿骰(A3)
    assert damage_s1(8, 10, 25, False, Fixed(4), Params(overflow_damage="flat")) == 29
    assert damage_s1(8, 0, 25, True, Fixed(), pct) == 0


def test_step():
    assert step(52, Fixed(17)) == 35        # 成功回傳幅度
    assert step(52, Fixed(60)) == 0         # 失敗
    assert step(52, Fixed(100)) == 0        # 00 特誤
    assert step(125, Fixed(100)) == 0       # 溢出也一樣(A1)
    assert step(125, Fixed(30)) == 95


def test_dodge():
    # 全閃用原始 m,不受等級上限(A4):等級 0 也能全閃
    assert dodge(30, 52, 0, Fixed(10)) == (0, True)          # m=42 ≥ 30
    # 否則 T −= min(m, 10 × level)
    assert dodge(60, 52, 1, Fixed(12)) == (50, False)        # m=40 → 吃 10
    assert dodge(60, 52, 5, Fixed(40)) == (48, False)        # m=12 < 上限 50 → 吃 12
    assert dodge(60, 52, 0, Fixed(12)) == (60, False)        # 零級不侵蝕
    assert dodge(60, 52, 3, Fixed(80)) == (60, False)        # 失敗
    assert dodge(60, 52, 3, Fixed(100)) == (60, False)       # 特誤(A5):該層失效、T 不變
    assert dodge(60, 150, 3, Fixed(100)) == (60, False)      # 溢出擲 00 仍特誤(A1)
    assert dodge(60, 52, 3, Fixed(52)) == (60, False)        # m=0:成功但吃 0


def test_parry_block():
    assert parry(52, 8, Fixed(10, 5)) == 5                   # 成功 → 守方擲武器骰
    assert parry(52, 8, Fixed(70)) is None                   # 失敗
    assert parry(52, 8, Fixed(100)) is None                  # 特誤
    # 格擋技能 +10:擲 60 對 52 失敗,對 52+10 成功
    assert block(52, 4, Fixed(60)) == 4
    assert block(52, 4, Fixed(63)) is None
    assert block(52, 0, Fixed(10)) == 0                      # 盾值 0:成功但吸收 0(與失敗不同)


def test_shield_value():
    assert shield_value("small", 14) == 3 and shield_value("large", 14) == 4
    assert shield_value("small", 10) == 1 and shield_value("large", 10) == 2
    assert shield_value("small", 4) == 0                     # 下限 0(1 + ⌊−3⌋ = −2)
    assert shield_value(None, 24) == 0


def test_damage():
    assert damage(8, 0, False, 0, Fixed(5)) == 5
    assert damage(8, 0, False, 3, Fixed(2)) == 0             # 吸收超過 → 下限 0
    assert damage(8, 25, False, 3, Fixed(4)) == 26           # 4 + 25 − 3
    assert damage(8, 25, True, 0, Fixed()) == 33             # 溢出致命滿骰(不擲骰)
    assert damage(8, 0, True, 0, Fixed(3)) == 3              # 無溢出致命不滿骰(A3)
    assert damage(8, 25, True, 10, Fixed()) == 23


def test_usage_log_s2():
    log = UsageLog()
    step(52, Fixed(17), log=log)
    dodge(30, 52, 1, Fixed(40), log=log)
    parry(52, 8, Fixed(20, 3), log=log)
    block(52, 3, Fixed(20), log=log)
    assert [r["skill"] for r in log] == ["步法", "閃避", "招架", "格擋"]
    assert log[1]["challenge_value"] == 30 and log[3]["skill_pct"] == 62


def test_usage_log():
    log = UsageLog()
    check(52, Fixed(17), log=log)
    erode(30, [Layer("閃避", 52, 1)], Fixed(40), log=log)
    assert len(log) == 2 and log[1]["challenge_value"] == 30
    assert log[0]["margin"] == 35


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            n += 1
    print(f"{n} tests passed")
