"""Session 6 新規則測試:每回合重擲先攻(行動值 = d100 − 先攻效益值×5 − 戰鬥反應×5)、預判(擲戰鬥反應,
幅度 ≥ 10 才看得到)、無預判者盲防(防禦鏈與內力在攻方擲骰前付清)。
可用 pytest,也可直接 `python3 sim/tests/test_fight_s6.py`。"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fight import Build, _exchange, action_value, fight, new_state
from resolve import Ctx, Params, s5_params, s6_params


class Fixed:
    def __init__(self, *vals):
        self.vals = list(vals)

    def randint(self, a, b):
        v = self.vals.pop(0)
        assert a <= v <= b, (a, b, v)
        return v


S6 = s6_params()


def exch(a, d, p, seq, sa=None, sd=None):
    sa, sd = sa or new_state(a, p), sd or new_state(d, p)
    info, rng = {}, Fixed(*seq)
    rec = _exchange(0, "weapon", a, sa, d, sd, p, rng, None, Ctx(), 0, info)
    assert not rng.vals, rng.vals                                     # 腳本骰要剛好用完
    return rec, info, sa, sd


# ---------- 開關 ----------
def test_switches_default_to_old_behaviour():
    assert Params().initiative_roll == "fixed" and Params().info_model == "legacy"
    assert s5_params().initiative_roll == "fixed" and s5_params().info_model == "legacy"
    assert S6.initiative_roll == "reroll" and S6.info_model == "s6" and S6.counter_policy == "lead"
    assert S6.initiative == "swap" and S6.sub_fee == 0.2 and S6.blind_qi == "full"


def test_legacy_ignores_reaction_skill():
    p = s5_params()
    rec, info, sa, sd = exch(Build("甲"), Build("乙", reaction=3), p, [10, 90, 90, 5])
    assert sd.st.fs_opp == 0 and sd.st.blind_n == 0 and sd.shen == 28   # 舊模式不預判、不盲防


# ---------- 先攻 ----------
def test_action_value_formula():
    b = Build("甲", init_eff=5, reaction=2)
    assert action_value(b, Fixed(50)) == 50 - 25 - 10
    assert action_value(Build("乙"), Fixed(50)) == 50 - 10           # 均值 Agi 14 → 效益值 2


def test_reroll_starter_is_smaller_action_value():
    p = s6_params(act_threshold=1)
    # 甲 d100 = 40(− 10 = 30),乙 d100 = 60(− 10 = 50):甲起手;甲落空(90)、乙落空(90)
    f = fight(Build("甲"), Build("乙"), p, Fixed(40, 60, 90, 90), first=1, max_rounds=1)
    assert f.flow["starters"] == [0]
    # 乙有戰鬥反應 4 級 → 行動值 60 − 10 − 20 = 30 < 甲 40 − 10 = 30?  相等 → 由本場先手(first)
    f = fight(Build("甲"), Build("乙", reaction=4), p, Fixed(40, 60, 90, 90), first=0, max_rounds=1)
    assert f.flow["starters"] == [0]
    f = fight(Build("甲"), Build("乙", reaction=4), p, Fixed(40, 60, 90, 90), first=1, max_rounds=1)
    assert f.flow["starters"] == [1]
    f = fight(Build("甲"), Build("乙", reaction=5), p, Fixed(40, 60, 90, 90), first=0, max_rounds=1)
    assert f.flow["starters"] == [1]                                    # 25 > 20,乙嚴格更快


def test_fixed_mode_starter_is_first_every_round():
    f = fight(Build("甲", weapon=2), Build("乙", weapon=2), s5_params(act_threshold=3), random.Random(3), first=1)
    assert set(f.flow["starters"]) == {1}


def test_reroll_flow_invariants():
    p = s6_params(act_threshold=3)
    seen = set()
    for seed in range(60):
        f = fight(Build("甲", weapon=2, reaction=1), Build("乙", weapon=2), p, random.Random(seed), first=seed % 2)
        fl = f.flow
        assert len(fl["round_dmg"]) == len(fl["starters"]) == f.rounds
        seen |= set(fl["starters"])
    assert seen == {0, 1}


def test_initiative_attribute_value():
    rng = random.Random(7)
    a, b = Build("甲", init_eff=5), Build("乙", init_eff=2)
    n = 4000
    wins = sum(action_value(a, rng) < action_value(b, rng) for _ in range(n))
    assert 0.58 < wins / n < 0.70                                       # 差 15 點 ≈ 0.64


def test_reaction_gives_cheap_brain_actions():
    s = new_state(Build("甲", reaction=3), S6)
    assert s.cheap["reaction"] == 2


# ---------- 預判 ----------
def test_foresight_needs_reaction_skill():
    from fight import _foresight
    sd = new_state(Build("乙"), S6)
    assert _foresight(Build("乙"), sd, S6, Fixed(), None, Ctx()) is None
    assert sd.st.fs_opp == 0


def test_foresight_threshold_and_cost():
    from fight import _foresight
    d = Build("乙", reaction=1)                                         # 技能值 52
    sd = new_state(d, S6)
    shen0 = sd.shen
    assert _foresight(d, sd, S6, Fixed(42), None, Ctx()) is True        # 幅度 10 剛好
    assert (sd.stamina, sd.shen, sd.st.fs_try, sd.st.fs_ok) == (sd.stamina_max - 1, shen0 - 1, 1, 1)
    sd = new_state(d, S6)
    assert _foresight(d, sd, S6, Fixed(43), None, Ctx()) is False       # 幅度 9:失敗照付
    assert sd.stamina == sd.stamina_max - 1 and sd.shen == shen0 - 1 and sd.st.fs_ok == 0


def test_foresight_progressive_brain_cost_and_shen_limit():
    from fight import _foresight
    d = Build("乙", reaction=1)
    sd = new_state(d, S6)
    _foresight(d, sd, S6, Fixed(10), None, Ctx())                       # 低價 1 次:1 點
    _foresight(d, sd, S6, Fixed(10), None, Ctx())                       # 腦格第 2 動:2 點
    assert sd.stamina == sd.stamina_max - 3
    sd = new_state(d, S6)
    sd.shen = 0
    assert _foresight(d, sd, S6, Fixed(), None, Ctx()) is None          # 神識不夠 → 不發動、不付體力
    assert sd.stamina == sd.stamina_max


def test_foresight_miss_known_pays_no_defense():
    # 攻方落空(90)→ 乙預判成功(40)→ 知道落空,不付任何防禦;只付預判 1 點
    rec, info, sa, sd = exch(Build("甲"), Build("乙", reaction=1), S6, [90, 40])
    assert rec[2] is False and sd.stamina == sd.stamina_max - 1 and sd.st.blind_n == 0


def test_foresight_hit_skips_hopeless_dodge():
    # 攻方擲 6(幅度 36);乙預判成功(40),預擲傷害 d8 = 4;閃避 42 − 36 = 6 → 全閃機率 6% < 25% 不付閃避
    # 招架擲 90 失敗 → 傷害 (4 + 2) = 6。成本:預判 1 + 招架 1
    rec, info, sa, sd = exch(Build("甲"), Build("乙", reaction=1), S6, [6, 40, 4, 90])
    assert sd.stamina == sd.stamina_max - 2 and rec[12] == 6 and sd.hp == sd.hp_cap - 6
    assert sd.st.dodge_try == 0 and sd.st.parry_try == 1


def test_foresight_hit_pays_dodge_when_chance_is_real():
    # 攻方擲 40(幅度 2);閃避 42 − 2 = 40% ≥ 25% → 付閃避。閃避擲 1 → 全閃
    rec, info, sa, sd = exch(Build("甲"), Build("乙", reaction=1), S6, [40, 40, 4, 1])
    assert rec[6] is True and sd.hp == sd.hp_cap and sd.stamina == sd.stamina_max - 2


def test_foresight_lethal_hit_refuses_counter_attack():
    p = s6_params(counter_policy="always")
    d = Build("乙", reaction=1)
    sd = new_state(d, p)
    sd.hp = 5                                                           # d8 = 4 → d_atk 6 ≥ 5 致命
    rec, info, sa, sd = exch(Build("甲"), d, p, [6, 40, 4, 90], sd=sd)
    assert info["counter"] is False
    # 傷害不致命(d_atk 3 < hp 20 的情況)則照 policy 對攻
    sd2 = new_state(d, p)
    rec, info, sa, sd2 = exch(Build("甲"), d, p, [6, 40, 1, 90], sd=sd2)
    assert info["counter"] is True


# ---------- 盲防 ----------
def test_blind_pays_defense_before_attack_even_on_miss():
    rec, info, sa, sd = exch(Build("甲"), Build("乙"), S6, [90])
    assert rec[2] is False
    assert sd.stamina == sd.stamina_max - 2                             # 閃避(腳 1)+ 招架(手 1)白付
    assert sd.st.blind_n == 1 and sd.st.blind_miss == 1
    # 舊模式:落空不用走防禦鏈,不付費
    rec, info, sa, sd = exch(Build("甲"), Build("乙"), s5_params(), [90])
    assert sd.stamina == sd.stamina_max


def test_blind_full_dodge_wastes_remaining_layers():
    # 攻 40(幅度 2),閃避擲 1 → 全閃;招架已預付 1 點卻沒擲
    rec, info, sa, sd = exch(Build("甲"), Build("乙"), S6, [40, 1])
    assert rec[6] is True and sd.stamina == sd.stamina_max - 2 and sd.st.parry_try == 0
    rec, info, sa, sd = exch(Build("甲"), Build("乙"), s5_params(), [40, 1])
    assert sd.stamina == sd.stamina_max - 1


def test_blind_chain_matches_legacy_when_everything_is_rolled():
    # 閃避敗、招架敗:盲防與舊模式結果一致(擲骰順序相同)
    seq = [10, 90, 90, 5]
    r6, _i, _a, d6 = exch(Build("甲"), Build("乙"), S6, seq)
    r5, _i, _a, d5 = exch(Build("甲"), Build("乙"), s5_params(), seq)
    assert r6 == r5 and d6.hp == d5.hp and d6.stamina == d5.stamina


def test_failed_foresight_still_decides_blind():
    # 乙預判擲 60(幅度 −8)失敗 → 盲防,落空時防禦鏈白付(預判 1 + 閃避 + 招架)
    rec, info, sa, sd = exch(Build("甲"), Build("乙", reaction=1), S6, [90, 60])
    assert sd.stamina == sd.stamina_max - 3 and sd.st.blind_miss == 1


def qi_def(use="full"):
    return s6_params(blind_qi=use)


def test_blind_qi_full_and_half_spent_even_on_miss():
    d = Build("乙", neigong=2, neili_use=("defense",))                  # 均值 2 級:池 12、單次 5
    for mode, spent in (("full", 5), ("half", 3)):
        p = qi_def(mode)
        rec, info, sa, sd = exch(Build("甲"), d, p, [90])
        assert sd.qi == sd.qi_max - spent and sd.st.qi_def_pts == spent


def test_blind_qi_absorbs_what_it_can():
    d = Build("乙", neigong=2, neili_use=("defense",))
    # 攻擲 10(幅度 32)、閃避敗(90)、招架敗(90)、傷害 d8 = 8 → 8 + 2 = 10;灌滿 5 點吸收 15 → 無傷
    rec, info, sa, sd = exch(Build("甲"), d, qi_def("full"), [10, 90, 90, 8])
    assert sd.hp == sd.hp_cap and sd.st.qi_def_abs == 10
    # 灌一半 3 點吸收 9 → 吃 1
    rec, info, sa, sd = exch(Build("甲"), d, qi_def("half"), [10, 90, 90, 8])
    assert sd.hp == sd.hp_cap - 1 and sd.st.qi_def_abs == 9


def test_informed_qi_pays_exact_after_chain():
    d = Build("乙", neigong=2, neili_use=("defense",), reaction=1)
    # 預判成功:閃避 42−32 → 10% 不付;招架敗;傷害 8 + 2 = 10 → 剛好 ⌈10/3⌉ = 4 點
    rec, info, sa, sd = exch(Build("甲"), d, S6, [10, 40, 8, 90])
    assert sd.qi == sd.qi_max - 4 and sd.hp == sd.hp_cap


def test_counter_attack_pays_no_blind_layers():
    p = s6_params(counter_policy="always")
    rec, info, sa, sd = exch(Build("甲"), Build("乙"), p, [10, 10, 5, 5])
    assert info["counter"] is True and sd.st.blind_n == 0


# ---------- 整場 ----------
def test_s6_fights_run_and_record_measures():
    p = s6_params(act_threshold=3)
    wins = [0, 0]
    for seed in range(80):
        f = fight(Build("甲", weapon=2, reaction=1), Build("乙", weapon=2), p, random.Random(seed), first=seed % 2)
        if f.winner is not None:
            wins[f.winner] += 1
        s = f.states[0].st
        assert s.fs_try <= s.fs_opp and s.fs_ok <= s.fs_try
        assert f.states[1].st.fs_opp == 0
    assert sum(wins) > 40


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            n += 1
    print(f"{n} tests passed")
