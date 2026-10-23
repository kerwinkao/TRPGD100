"""resolve.py 的規則對照測試。可用 pytest,也可直接 `python3 sim/tests/test_resolve.py`。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from resolve import (Layer, Params, UsageLog, check, crit_zone, damage, difficulty,
                     erode, fumble_floor, overflow)


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


def test_damage():
    assert damage(8, 10, 25, True, Fixed()) == 10          # 溢出致命滿骰 8 × 1.25
    assert damage(8, 10, 0, True, Fixed(3)) == 3           # 無溢出致命不滿骰(A3)
    assert damage(8, 10, 25, False, Fixed(4), Params(overflow_damage="flat")) == 29
    assert damage(8, 0, 25, True, Fixed()) == 0


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
