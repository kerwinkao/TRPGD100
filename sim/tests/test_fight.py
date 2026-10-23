"""fight.py 的規則對照測試(Session 2)。可用 pytest,也可直接 `python3 sim/tests/test_fight.py`。"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fight import (Build, _attack, _price, _try, _try_pair, cheap_count, dual_penalty, fight,
                   new_state)
from resolve import Ctx, Params, shield_value


class Fixed:
    def __init__(self, *vals):
        self.vals = list(vals)

    def randint(self, a, b):
        v = self.vals.pop(0)
        assert a <= v <= b, (a, b, v)
        return v


def test_cheap_count():
    assert [cheap_count(l) for l in (0, 1, 2, 3, 5, 7, 9)] == [0, 1, 1, 2, 3, 4, 5]


def test_cost_progression_same_slot_any_skill():
    # 零級:同格第 k 動耗 k 點,不分技能(長劍攻、格擋、長劍攻 = 1、2、3)
    p = Params(shield="large")
    b = Build("甲", block=0)
    s = new_state(b, p)
    costs = []
    for key in ("weapon", "block", "weapon"):
        costs.append(_price(s, key, p))
        assert _try(s, b, key, p)
    assert costs == [1, 2, 3] and s.stamina == 28 - 6


def test_cheap_then_progressive():
    # 一級:低價 1 次;第二動回到格內累進(位置 2 → 耗 2)
    p = Params()
    b = Build("甲", weapon=1)
    s = new_state(b, p)
    got = []
    for _ in range(3):
        got.append(_price(s, "weapon", p))
        _try(s, b, "weapon", p)
    assert got == [1, 2, 3]
    # 三級:低價 2 次 → 1、1、3
    b3 = Build("乙", weapon=3)
    s = new_state(b3, p)
    got = []
    for _ in range(3):
        got.append(_price(s, "weapon", p))
        _try(s, b3, "weapon", p)
    assert got == [1, 1, 3]


def test_round_reset():
    p = Params()
    b = Build("甲", weapon=1)
    s = new_state(b, p)
    _try(s, b, "weapon", p)
    _try(s, b, "weapon", p)
    s.reset_round(b)
    assert _price(s, "weapon", p) == 1 and s.stamina == 28 - 3   # 體力不回,格與低價歸零


def test_step_cross_slot_vs_same_slot():
    p = Params()
    b = Build("甲")                                   # 全零級:無低價
    s = new_state(b, p)
    assert _try_pair(s, b, "step", "dodge", p)        # 同格:腳 1,下一動腳 2
    assert _price(s, "dodge", p) == 2
    s = new_state(b, p)
    assert _try_pair(s, b, "step", "parry", p)        # 跨格:腳 1 + 手 1
    assert _price(s, "parry", p) == 1


def test_threshold_and_winded_flags():
    b = Build("甲")
    s = new_state(b, Params(act_threshold=2))
    p = Params(act_threshold=2)
    assert _try(s, b, "weapon", p) and _try(s, b, "weapon", p)
    assert not _try(s, b, "weapon", p)                # 第 3 動價碼 3 > 門檻 2
    assert s.st.first_refuse == 0 and s.st.first_winded is None
    p = Params()
    s = new_state(b, p, stamina=2)
    assert _try(s, b, "weapon", p)                    # 耗 1,剩 1
    assert not _try(s, b, "weapon", p)                # 價碼 2 > 剩 1 → 喘
    assert s.st.first_winded == 0


def test_neili_first_pay():
    p = Params()
    b = Build("甲", weapon=0, neigong=1, neili_first=True)
    s = new_state(b, p)
    assert s.neili == 52
    _try(s, b, "weapon", p)
    assert s.neili == 51 and s.stamina == 28
    b0 = Build("乙", neigong=0)
    assert new_state(b0, p).neili == 0                # 零級內功無內力(A12)


def test_dual_penalty():
    assert dual_penalty(Params(dual_wield="none")) == (10, 30)
    assert dual_penalty(Params(dual_wield="lv1")) == (10, 10)
    assert dual_penalty(Params(dual_wield="full")) == (0, 0)


def test_attack_scenario_parry_absorbs():
    # 攻:擲 10 成功(m=32);守閃避 42 擲 90 失敗;招架 42 擲 20 成功、守方骰 3;無盾;傷害骰 7 → 7 − 3 = 4
    p = Params()
    a, d = Build("攻"), Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 90, 20, 3, 7), None, Ctx(), 0)
    assert rec[2] and not rec[6] and rec[11] == 3 and rec[12] == 4
    assert sd.hp == 26 and rec[13] == "through_parry"
    # 守方付了閃避 + 招架:腳 1、手 1
    assert sd.stamina == 28 - 2


def test_attack_scenario_full_dodge_skips_hand_layers():
    # 攻擲 10(m=32);守閃避擲 5(m=37 ≥ 32)→ 全閃,不擲招架
    p = Params()
    a, d = Build("攻"), Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 5), None, Ctx(), 0)
    assert rec[6] and rec[12] == 0 and sd.hp == 30 and sd.stamina == 27


def test_block_needs_shield_param():
    a, d = Build("攻"), Build("守", block=0)
    for shield, expect_rolls in ((None, 3), ("small", 4)):
        p = Params(shield=shield)
        sa, sd = new_state(a, p), new_state(d, p)
        # 攻 10;閃避 90 敗;招架 90 敗;(有盾才擲格擋 90 敗);傷害骰 5
        seq = [10, 90, 90] + ([90] if shield else []) + [5]
        rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(*seq), None, Ctx(), 0)
        assert rec[12] == 5 and rec[13] == "no_guard"


def test_step_boosts_declared_action():
    # 守方步法宣告輔助閃避:步法 52 擲 17 → S=35;閃避 42+35=77,擲 30 → m=47 ≥ T=32 → 全閃
    p = Params()
    a = Build("攻")
    d = Build("守", step=1, step_def="dodge")
    sa, sd = new_state(a, p), new_state(d, p)
    rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 17, 30), None, Ctx(), 0)
    assert rec[6] and sd.hp == 30
    # 沒有步法時同樣的閃避擲 30(m=12 < 32)不會全閃
    d0 = Build("守")
    sa, sd = new_state(a, p), new_state(d0, p)
    rec = _attack(0, "weapon", a, sa, d0, sd, p, Fixed(10, 30, 90, 5), None, Ctx(), 0)
    assert not rec[6]


def test_overflow_flat_and_crit_maxdie():
    # 攻 150(溢出 50):擲 3 致命(區 01–15+5),守方全失敗 → 滿骰 8 + 50 = 58
    p = Params()
    a = Build("攻", crit_bonus=5, skill_override={"weapon": 150})
    d = Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(3, 90, 90), None, Ctx(), 0)
    assert rec[3] and rec[12] == 58 and rec[13] == "crit_maxdie"


def test_defense_combine():
    # 招架 4 + 盾 4:sum 吸收 8,max 吸收 4。攻擲 10;閃避敗;招架成功骰 4;格擋成功(盾值大盾 4);傷害骰 8
    for mode, expect in (("sum", 0), ("max", 4)):
        p = Params(shield="large", defense_combine=mode)
        a, d = Build("攻"), Build("守", block=0)
        sa, sd = new_state(a, p), new_state(d, p)
        rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 90, 20, 4, 20, 8), None, Ctx(), 0)
        assert rec[12] == expect, mode


def test_fight_deterministic_and_stalls():
    b0, b1 = Build("甲", weapon=1), Build("乙", weapon=1)
    p = Params(act_threshold=3)
    r1 = [fight(b0, b1, p, random.Random(5), first=0) for _ in range(1)]
    r2 = [fight(b0, b1, p, random.Random(5), first=0) for _ in range(1)]
    assert (r1[0].winner, r1[0].rounds) == (r2[0].winner, r2[0].rounds)
    # 雙方池皆空且無回復 → 提前平手,不空轉 300 回合
    f = fight(Build("甲"), Build("乙"), Params(hp=10_000), random.Random(1))
    assert f.winner is None and f.stalled and f.rounds < 300


def test_lianci_bonus_only_after_success():
    p = Params()
    a = Build("攻", weapon=0, lianci=True)
    d = Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    # lianci_bonus=10 → 技能 52:擲 50 成功(m=2)。無加成時 42 擲 50 失敗
    rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(50, 90, 90, 5), None, Ctx(), 10)
    assert rec[2]


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            n += 1
    print(f"{n} tests passed")
