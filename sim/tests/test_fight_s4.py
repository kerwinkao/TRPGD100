"""Session 4 新規則測試:發動費 0.2 與浮點體力、各副技能的池(劍意燒神識)、多骰武器骰、link 加成參數、
內力三用途(池量與等級上限、單次輸出上限、攻擊灌注、防禦吸收、療傷、疊發分發、冷卻、不回)。
可用 pytest,也可直接 `python3 sim/tests/test_fight_s4.py`。"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import npc
from fight import (Build, _attack, _heal, _plan, _try_seq, fight, neili_cap, neili_level, neili_pool,
                   new_state, shenshi_max, sub_costs)
from resolve import Ctx, Params, damage_s3, max_dice, parry_ex, roll_dice, s3_params, s4_params


class Fixed:
    def __init__(self, *vals):
        self.vals = list(vals)

    def randint(self, a, b):
        v = self.vals.pop(0)
        assert a <= v <= b, (a, b, v)
        return v


def close(a, b, eps=1e-9):
    return abs(a - b) < eps


S4 = s4_params()


# ---------- 開關 ----------
def test_s4_params_switches():
    assert S4.sub_fee == 0.2 and S4.reroll_pool == "shenshi" and S4.neili_model == "s4"
    assert S4.end_on == "incap" and S4.damage_model == "s3" and S4.hp_model == "str_con"
    assert Params().sub_fee == 1.0 and Params().neili_model == "skill_value"      # 預設仍是舊行為


# ---------- 發動費 0.2、體力浮點 ----------
def test_sub_costs_by_pool():
    stam, shen = sub_costs(["link_w2p", "link_p2w", "counter"], S4)
    assert close(stam, 0.6) and shen == 0.0
    stam, shen = sub_costs(["crit_sub", "reroll_any"], S4)           # 劍意燒神識,致命燒體力
    assert close(stam, 0.2) and shen == 1.0
    stam, shen = sub_costs(["reroll_any"], s3_params())              # 舊行為:劍意燒體力 1 點
    assert stam == 1.0 and shen == 0.0


def test_fee_is_fractional_and_not_in_k():
    # 招架持有三個副技能,發動 = 價碼 1 + 0.6;格的累進仍只 +1
    d = Build("守", link_w2p=1, link_p2w=1, counter=1)
    sd = new_state(d, S4)
    held = d.held_parry(S4)
    variants = [([("parry", sub_costs(held[:k], S4)[0])], 0.0) for k in range(len(held), -1, -1)]
    assert _try_seq(sd, d, S4, variants) == 0
    assert close(sd.stamina, 28 - 1.6) and sd.slots["hand"] == 1 and close(sd.st.sub_fee, 0.6)


def test_threshold_3_now_affords_all_three_subskills():
    # Session 3(費 1):門檻 3 付不起三個(1+3=4);費 0.2:1.6 ≤ 3,三個都發動
    d = Build("守", link_w2p=1, link_p2w=1, counter=1)
    for p, n_active in ((s3_params(act_threshold=3), 2), (s4_params(act_threshold=3), 3)):
        sd = new_state(d, p)
        held = d.held_parry(p)
        variants = [([("parry", sub_costs(held[:k], p)[0])], 0.0) for k in range(len(held), -1, -1)]
        idx = _try_seq(sd, d, p, variants)
        assert len(held) - idx == n_active


def test_float_tolerance_in_plan():
    # 0.2 × 3 的浮點累加不能讓剛好付得起的動作被誤判成付不起
    p = s4_params()
    d = Build("守")
    sd = new_state(d, p, stamina=1 + 0.2 + 0.2 + 0.2)
    why, costs = _plan(sd, p, [("parry", 0.2 + 0.2 + 0.2)])
    assert why is None
    sd = new_state(d, p, stamina=1.5)
    assert _plan(sd, p, [("parry", 0.6)])[0] == "wind"           # 1.6 > 1.5 付不起


def test_fee_counts_toward_threshold():
    p = s4_params(act_threshold=1.1)
    d = Build("守", link_w2p=1)
    sd = new_state(d, p)
    assert _plan(sd, p, [("parry", 0.2)])[0] == "refuse"          # 價碼 1 + 0.2 > 1.1
    assert _plan(sd, p, [("parry", 0.0)])[0] is None


# ---------- 劍意燒神識 ----------
def test_reroll_any_burns_shenshi_not_stamina():
    a = Build("攻", reroll_any=True)
    d = Build("守")
    sa, sd = new_state(a, S4), new_state(d, S4)
    assert sa.shen == shenshi_max(a) == 28
    rec = _attack(0, "weapon", a, sa, d, sd, S4, Fixed(90, 10, 90, 90, 5), None, Ctx(), 0)
    assert rec[2]                                                 # 第一擲失敗 → 重擲成功
    assert close(sa.stamina, 28 - 1) and close(sa.shen, 28 - 1)   # 只付出手 1 點體力;神識 1
    assert sa.st.sub_fee == 0 and close(sa.st.shen_spent, 1)


def test_shenshi_fee_ignores_stamina_threshold_and_slot():
    # 門檻 1:出手價碼 1,體力費為 0,所以劍意照樣能發動;不佔格
    p = s4_params(act_threshold=1)
    a = Build("攻", reroll_any=True)
    d = Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    _attack(0, "weapon", a, sa, d, sd, p, Fixed(90, 10, 90, 90, 5), None, Ctx(), 0)
    assert close(sa.st.shen_spent, 1) and sa.slots == {"hand": 1, "foot": 0, "brain": 0}


def test_reroll_falls_back_when_shenshi_empty():
    a = Build("攻", reroll_any=True)
    d = Build("守")
    sa, sd = new_state(a, S4), new_state(d, S4)
    sa.shen = 0.5
    rec = _attack(0, "weapon", a, sa, d, sd, S4, Fixed(90), None, Ctx(), 0)
    assert not rec[2] and sa.shen == 0.5 and sa.st.shen_spent == 0     # 沒發動,第一擲 90 就失敗


def test_old_behaviour_reroll_burns_stamina_when_pool_is_stamina():
    p = s3_params()
    a = Build("攻", reroll_any=True)
    d = Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    _attack(0, "weapon", a, sa, d, sd, p, Fixed(90, 10, 90, 90, 5), None, Ctx(), 0)
    assert close(sa.stamina, 28 - 2)


# ---------- 武器骰 ----------
def test_multi_dice_rolls_and_max():
    assert roll_dice((2, 4), Fixed(3, 4)) == 7
    assert roll_dice(6, Fixed(5)) == 5 and roll_dice((1, 8), Fixed(2)) == 2
    assert max_dice((2, 4)) == 8 and max_dice(12) == 12 and max_dice((2, 6)) == 12


def test_damage_with_2d4_and_crit_maxdie():
    assert damage_s3((2, 4), 2, 0, False, 0, Fixed(3, 4)) == (9, 9)             # 3+4+2
    assert damage_s3((2, 4), 2, 25, True, 0, Fixed()) == (13, 13)                # 滿骰 8:(8+2)×1.25 = 13
    assert damage_s3((1, 6), 2, 0, False, 3, Fixed(4), extra=5) == (8, 11)       # 4+2+5 − 3


def test_parry_uses_defender_dice():
    d_def, margin = parry_ex(60, (2, 6), Fixed(10, 5, 6), params=S4)
    assert d_def == 11 and margin == 50                                          # 擲 10 成功;2d6 = 5+6


def test_build_dice_default_and_override():
    assert Build("甲").dice() == (1, 8)
    assert Build("甲", weapon_die=6).dice() == (1, 6)
    assert Build("甲", weapon_dice=(2, 4)).dice() == (2, 4)


def test_attack_uses_build_dice():
    a = Build("攻", weapon_dice=(2, 4))
    d = Build("守")
    sa, sd = new_state(a, S4), new_state(d, S4)
    rec = _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 90, 90, 3, 4), None, Ctx(), 0)
    assert rec[12] == 3 + 4 + 2


def test_npc_baseline_dice_override():
    core, tpl = npc.load_core(), npc.load_templates()
    t = next(x for x in tpl if x["name"] == "鏢師")
    assert npc.instantiate(t, "中", core).dice() == (1, 8)                       # 預設沿用 Session 3 表
    assert npc.instantiate(t, "中", core, baseline_dice=npc.BASELINE_DICE).dice() == (1, 6)
    assert npc.instantiate(t, "中", core, baseline_dice=npc.DICE["2d4"]).dice() == (2, 4)
    assert set(npc.DICE) == {"1d6", "1d8", "2d4", "1d10", "1d12", "2d6"}


# ---------- link 加成參數 ----------
def test_link_bonus_param():
    # 招架 42 擲 70:+10×2=62 失敗;link 每級 +15 → 72 成功
    a = Build("攻")
    for bonus, ok in ((10, False), (15, True)):
        p = s4_params(link_bonus=bonus)
        d = Build("守", link_w2p=1, link_p2w=1)
        sa, sd = new_state(a, p), new_state(d, p)
        seq = [10, 90, 70] + ([3] if ok else []) + [5]
        rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(*seq), None, Ctx(), 0)
        assert (rec[9] is not None) == ok


# ---------- 內力:池量與等級上限 ----------
def test_neili_pool_formula():
    # 效益值全 +2:一級 6、二級 12;難度階 2(內門)二級 24、3(嫡傳)二級 36(attributes.md 範例)
    assert neili_pool(Build("甲", neigong=1), S4) == 6
    assert neili_pool(Build("甲", neigong=2), S4) == 12
    assert neili_pool(Build("甲", neigong=2, neili_tier=2), S4) == 24
    assert neili_pool(Build("甲", neigong=2, neili_tier=3), S4) == 36
    assert neili_pool(Build("甲", neigong=0), S4) == 0


def test_neili_level_cap_is_min_effect():
    assert neili_level(Build("甲", neigong=5), S4) == 2                          # 全 +2 → 二級
    assert neili_level(Build("甲", neigong=5, attrs={"Con": 16, "Cha": 16, "Wis": 16}), S4) == 3
    assert neili_level(Build("甲", neigong=3, attrs={"Con": 12, "Cha": 14, "Wis": 14}), S4) == 1
    assert neili_level(Build("甲", neigong=3, attrs={"Con": 14, "Cha": 14, "Wis": 11}), S4) == 0   # Wis 效益 0:修不出
    b = Build("甲", neigong=3, attrs={"Con": 12, "Cha": 14, "Wis": 14})
    assert neili_pool(b, S4) == (1 + 2 + 2) * 1 * 1                              # 以上限後的等級算池


def test_neili_single_output_cap_table():
    assert [neili_cap(l) for l in (0, 1, 2, 3, 4)] == [0, 3, 5, 7, 9]


def test_neili_off_by_default_and_not_a_stamina_wallet():
    old = Build("甲", neigong=1, neili_first=True)
    assert new_state(old, s3_params()).neili == 52                                # 舊模型:內功技能值,可代付體力
    s = new_state(Build("甲", neigong=1, neili_use=("attack",)), S4)
    assert s.neili == 0 and s.qi == 6 and s.avail == 28                           # s4:獨立池,不進體力


# ---------- 內力:攻擊灌注 ----------
def _mk(neili_level=1, use=("attack",), **kw):
    return Build("甲", neigong=neili_level, neili_use=use, **kw)


def test_attack_infusion_adds_1d3_per_point():
    # 一級:單次輸出上限 3 點 → +3d3。守方無防;傷害骰 5 + 補正 2 + 灌注(1+2+3 = 6)= 13
    a, d = _mk(), Build("守")
    sa, sd = new_state(a, S4), new_state(d, S4)
    assert sa.allow == {"attack": 3}
    rec = _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 90, 90, 1, 2, 3, 5), None, Ctx(), 0)
    assert rec[12] == 13 and sd.hp == 28 - 13
    assert sa.qi == 3 and sa.st.qi_atk_pts == 3 and sa.st.qi_atk_dmg == 6


def test_attack_infusion_once_per_round_budget():
    # 一級每回合一次輸出(3 點):同回合第二擊沒有灌注
    a, d = _mk(), Build("守")
    sa, sd = new_state(a, S4), new_state(d, S4)
    _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 90, 90, 1, 1, 1, 5), None, Ctx(), 0)
    rec = _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 90, 90, 5), None, Ctx(), 0)
    assert rec[12] == 7


def test_attack_infusion_not_spent_when_fully_dodged():
    a, d = _mk(), Build("守")
    sa, sd = new_state(a, S4), new_state(d, S4)
    _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 1), None, Ctx(), 0)          # 守方閃避擲 1 → 全閃
    assert sa.qi == 6 and sa.st.qi_atk_pts == 0 and not sa.cd_now


def test_stack_vs_one_output_per_action():
    # 三級(效益值 +3):單次輸出上限 7、每回合 2 次 → 額度 14。疊發:一擊灌 14 點;不疊發:一擊最多 7 點
    attrs = {"Con": 16, "Cha": 16, "Wis": 16}
    for stack, pts in ((True, 14), (False, 7)):
        p = s4_params(neili_stack=stack)
        a, d = _mk(3, attrs=attrs), Build("守")
        sa, sd = new_state(a, p), new_state(d, p)
        assert sa.allow == {"attack": 14} and sa.qi == 27
        _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 90, 90, *([1] * pts), 5), None, Ctx(), 0)
        assert sa.st.qi_atk_pts == pts


# ---------- 內力:防禦吸收 ----------
def test_defense_absorbs_three_light_one_heavy_per_point():
    # 攻:傷害 5+2 = 7;守方一級灌防禦,需 ⌈7/3⌉ = 3 點(≤ 單次上限 3)→ 吸收 9 ≥ 7,傷害 0
    a, d = Build("攻"), _mk(use=("defense",))
    sa, sd = new_state(a, S4), new_state(d, S4)
    rec = _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 90, 90, 5), None, Ctx(), 0)
    assert rec[12] == 0 and sd.hp == 28 and sd.qi == 3 and sd.st.qi_def_pts == 3 and sd.st.qi_def_abs == 7
    assert sd.heavy == 28 - max(0, 7 // 2 - 3)                                   # 重傷位 ⌊7/2⌋ − 3 → 0


def test_defense_capped_by_single_output_limit():
    # 傷害 10,要 4 點但單次輸出上限 3 → 吸收 9,剩 1
    a = Build("攻", attrs={"Str": 20, "Con": 14})                                # Str 20 → 補正 +5
    d = _mk(use=("defense",))
    sa, sd = new_state(a, S4), new_state(d, S4)
    rec = _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 90, 90, 5), None, Ctx(), 0)
    assert rec[12] == 1 and sd.hp == 27 and sd.qi == 3


def test_defense_not_spent_when_no_damage():
    a, d = Build("攻"), _mk(use=("defense",))
    sa, sd = new_state(a, S4), new_state(d, S4)
    _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 1), None, Ctx(), 0)          # 全閃
    assert sd.qi == 6 and not sd.cd_now


# ---------- 內力:療傷 ----------
def test_heal_costs_brain_action_and_heals_1d4_per_point():
    b = _mk(use=("heal",))
    s = new_state(b, S4)
    s.hp = 10
    _heal(b, s, S4, Fixed(2, 3, 4))                                               # 缺 18 → 想要 8 點,取單次上限 3
    assert s.hp == 19 and s.qi == 3 and s.st.qi_heal_pts == 3 and s.st.qi_heal_amt == 9
    assert s.slots["brain"] == 1 and close(s.stamina, 28 - 1) and s.st.heal_acts == 1
    _heal(b, s, S4, Fixed())                                                      # 一級每回合一次輸出,額度用完
    assert s.hp == 19


def test_heal_sized_to_deficit_and_capped_at_max():
    b = _mk(use=("heal",))
    s = new_state(b, S4)
    s.hp = 26                                                                     # 缺 2 → ⌈2/2.5⌉ = 1 點
    _heal(b, s, S4, Fixed(4))
    assert s.hp == 28 and s.qi == 5
    s.hp = 28
    _heal(b, s, S4, Fixed())                                                      # 滿血不療
    assert s.qi == 5


def test_heal_respects_threshold_and_pool():
    b = _mk(use=("heal",))
    p = s4_params(act_threshold=0.5)                                              # 價碼 1 > 門檻
    s = new_state(b, p)
    s.hp = 10
    _heal(b, s, p, Fixed())
    assert s.hp == 10 and s.qi == 6
    s = new_state(b, S4)
    s.hp, s.qi = 10, 0
    _heal(b, s, S4, Fixed())
    assert s.hp == 10


def test_no_heal_without_heal_use():
    b = _mk(use=("attack",))
    s = new_state(b, S4)
    s.hp = 5
    _heal(b, s, S4, Fixed())
    assert s.hp == 5 and s.qi == 6


# ---------- 內力:冷卻、分發 ----------
def test_cooldown_blocks_next_round_only():
    b = _mk(use=("attack",))
    s = new_state(b, S4)
    assert s.qi_take("attack", 3) == 3 and "attack" in s.cd_now
    s.reset_round(b)
    assert "attack" not in s.allow                                                # 上回合用過 → 本回合不能用
    s.reset_round(b)
    assert s.allow == {"attack": 3}                                               # 再下一回合恢復


def test_cooldown_per_category_independent():
    b = _mk(use=("attack", "defense"), neili_alloc={"attack": 2, "defense": 1})
    s = new_state(b, S4)
    assert s.allow == {"attack": 2, "defense": 1}                                 # 分發:3 點輸出 = 2 攻 1 防
    s.qi_take("attack", 2)
    s.reset_round(b)
    assert s.allow == {"defense": 1}                                              # 只有攻在冷卻
    s.qi_take("defense", 1)
    s.reset_round(b)
    assert s.allow == {"attack": 2}


def test_unused_category_is_not_on_cooldown():
    b = _mk(use=("attack",))
    s = new_state(b, S4)
    s.reset_round(b)
    s.reset_round(b)
    assert s.allow == {"attack": 3}


def test_neili_does_not_regenerate_in_fight():
    a = Build("甲", weapon=1, neigong=2, neili_use=("attack",))
    f = fight(a, Build("乙", weapon=1), s4_params(act_threshold=3), random.Random(7), max_rounds=3)
    assert f.states[0].qi <= f.states[0].qi_max == 12


def test_fight_smoke_with_all_neili_uses():
    for use in (("attack",), ("defense",), ("heal",), ("attack", "defense")):
        for seed in range(30):
            a = Build("甲", weapon=2, neigong=2, neili_use=use,
                      neili_alloc={"attack": 3, "defense": 2} if len(use) == 2 else None)
            f = fight(a, Build("乙", weapon=2), s4_params(act_threshold=3), random.Random(seed), first=seed % 2)
            if f.winner is not None:
                assert f.states[1 - f.winner].hp <= 0 and f.states[f.winner].hp > 0
            assert f.states[0].qi >= 0 and f.states[0].stamina >= -1e-9


def test_activation_counters():
    d = Build("守", link_w2p=1, link_p2w=1)
    a = Build("攻")
    sa, sd = new_state(a, S4), new_state(d, S4)
    _attack(0, "weapon", a, sa, d, sd, S4, Fixed(10, 90, 90, 5), None, Ctx(), 0)
    assert sd.st.sub_offered == 2 and sd.st.sub_active == 2


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            n += 1
    print(f"{n} tests passed")
