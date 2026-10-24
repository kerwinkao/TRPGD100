"""Session 3 新規則測試:傷勢三位、Con 效益值回復、溢傷乘數加補正、疊加佔格付費、
副技能發動 +1/個、link、致命、任意重骰、事件→被動反應(反擊)。
可用 pytest,也可直接 `python3 sim/tests/test_fight_s3.py`。"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fight import (Build, _attack, _defend, _plan, _price, _try, _try_seq, fight, hp_max, new_state,
                   regen_of, stamina_max)
from resolve import Ctx, Params, check, damage_s3, effect_value, s3_params


class Fixed:
    def __init__(self, *vals):
        self.vals = list(vals)

    def randint(self, a, b):
        v = self.vals.pop(0)
        assert a <= v <= b, (a, b, v)
        return v


S3 = s3_params()


# ---------- 屬性效益值、上限、回復 ----------
def test_effect_value():
    assert [effect_value(x) for x in (4, 10, 11, 12, 14, 20, 24)] == [0, 0, 0, 1, 2, 5, 7]


def test_pools_follow_attributes():
    b = Build("甲", attrs={"Str": 16, "Con": 18})
    assert hp_max(b, S3) == 34 and stamina_max(b, S3) == 36 and regen_of(b, S3) == 4
    assert hp_max(b, Params()) == 30 and stamina_max(b, Params()) == 28   # 預設仍是 Session 2
    assert regen_of(Build("乙"), S3) == 2 and regen_of(Build("丙", attrs={"Con": 9}), S3) == 0


def test_regen_keeps_fight_alive_instead_of_stalling():
    # 上限極大打不死。Con 10 → 回復 0:雙方池空後提前判平手;Con 12 → 回復 1:不會停擺,打滿回合上限
    huge = {"Str": 200, "Con": 10}
    a0, b0 = Build("甲", attrs=huge, weapon_attr="Ski"), Build("乙", attrs=huge, weapon_attr="Ski")
    f = fight(a0, b0, s3_params(act_threshold=3), random.Random(3))
    assert f.stalled and f.rounds < 300
    on = {"Str": 200, "Con": 12}
    a1, b1 = Build("甲", attrs=on, weapon_attr="Ski"), Build("乙", attrs=on, weapon_attr="Ski")
    f = fight(a1, b1, s3_params(act_threshold=3), random.Random(3), max_rounds=40)
    assert not f.stalled and f.rounds == 40


# ---------- 傷勢三位 ----------
def test_three_position_wounds():
    s = new_state(Build("甲"), S3)
    assert (s.hp, s.heavy) == (28, 28)
    s.take(12)
    assert (s.hp, s.heavy) == (16, 22)             # 輕傷位 −12,重傷位 −⌊12/2⌋
    s.take(5)
    assert (s.hp, s.heavy) == (11, 20)             # ⌊5/2⌋ = 2


def test_incap_vs_kill():
    # Str 4 + Con 4 → 上限 8。技能 800(溢出 700 → 乘數 ×8),一擊 ≥ 24 − 吸收(≤ 8)≥ 16 → 重傷位 8 − 8 = 0 擊殺
    killer = Build("殺", attrs={"Str": 14, "Con": 14}, skill_override={"weapon": 800})
    frail = Build("弱", attrs={"Str": 4, "Con": 4})
    f = fight(killer, frail, S3, random.Random(1), first=0)
    assert f.winner == 0 and f.kill and f.states[1].hp <= 0 and f.states[1].heavy <= 0
    # 普通身板:一刀 10 點 → 輕傷位 18、重傷位 23,戰鬥未結束;打到輕傷位 0 時重傷位仍 > 0 → 只失能不擊殺
    s = new_state(Build("普"), S3)
    for _ in range(3):
        s.take(10)
    assert s.hp == -2 and s.heavy == 13


def test_end_on_death_continues_past_incap():
    # 'death' 模式:輕傷位 0 不結束,打到重傷位 0 才分勝負;敗方輕傷位早已 ≤ 0,kill 必為真
    a, b = Build("甲", weapon=3), Build("乙", weapon=3)
    for seed in range(60):
        f = fight(a, b, s3_params(act_threshold=3, end_on="death"), random.Random(seed), first=seed % 2)
        if f.winner is not None:
            loser = f.states[1 - f.winner]
            assert f.kill and loser.heavy <= 0 and loser.hp <= 0
            assert f.states[f.winner].heavy > 0


def test_fight_ends_on_incap_not_kill():
    a, b = Build("甲", weapon=3), Build("乙", weapon=3)
    kills = decided = 0
    for seed in range(300):
        f = fight(a, b, s3_params(act_threshold=3), random.Random(seed), first=seed % 2)
        if f.winner is not None:
            decided += 1
            kills += f.kill
            loser = f.states[1 - f.winner]
            assert loser.hp <= 0 and f.states[f.winner].hp > 0
    assert decided > 200 and kills == 0            # 均值角色:重傷位只吃一半,輕傷位先歸零


# ---------- 傷判:乘數加屬性補正 ----------
def test_damage_s3_example_from_doc():
    # resolution.md 範例:長劍、Str 14(+2)、技能 125 致命 → (8+2) × 1.25 = 12.5 → 13
    assert damage_s3(8, 2, 25, True, 0, Fixed()) == (13, 13)


def test_damage_s3_rounding_and_defense():
    assert damage_s3(8, 2, 0, False, 0, Fixed(5)) == (7, 7)           # 未溢出 ×1
    assert damage_s3(8, 2, 25, False, 0, Fixed(5)) == (9, 9)          # (5+2)×1.25 = 8.75 → 9
    assert damage_s3(8, 2, 25, False, 4, Fixed(5)) == (5, 9)          # 吸收在乘完之後扣
    assert damage_s3(8, 2, 0, False, 99, Fixed(5)) == (0, 7)
    assert damage_s3(8, 2, 25, True, 0, Fixed())[1] == 13             # 致命且溢出 → 滿骰
    assert damage_s3(8, 2, 0, True, 0, Fixed(3)) == (5, 5)            # 致命但沒溢出 → 不滿骰(A3)


def test_attack_uses_attribute_bonus():
    # Str 16(+3):骰 5 → 8;無防禦 → 傷害 8
    a = Build("攻", attrs={"Str": 16, "Con": 14}, weapon_attr="Str")
    d = Build("守")
    sa, sd = new_state(a, S3), new_state(d, S3)
    rec = _attack(0, "weapon", a, sa, d, sd, S3, Fixed(10, 90, 90, 5), None, Ctx(), 0)
    assert rec[12] == 8 and sd.hp == 28 - 8 and sd.heavy == 28 - 4
    a2 = Build("攻", attrs={"Str": 16, "Ski": 10}, weapon_attr="Ski")      # 輕兵取 Ski:+0
    sa, sd = new_state(a2, S3), new_state(d, S3)
    rec = _attack(0, "weapon", a2, sa, d, sd, S3, Fixed(10, 90, 90, 5), None, Ctx(), 0)
    assert rec[12] == 5


# ---------- 疊加佔格付費 ----------
def test_stack_pays_own_slot_and_cheap():
    # 戰鬥反應 5 級(低價 3 次)+ 內功 5 級,同在腦格:兩疊各低價 1 點;武器零級手格 1 點 → 共 3
    b = Build("甲", stacks=(("戰鬥反應", 5), ("內功", 5)))
    s = new_state(b, S3)
    ok, costs = _plan(s, S3, [("stack:戰鬥反應", 0), ("stack:內功", 0), ("weapon", 0)])
    assert ok is None and costs == [1, 1, 1]


def test_stack_progressive_without_cheap():
    # 零級疊加沒有低價:同在腦格 → 1、2;加武器 1 = 4
    b = Build("甲", stacks=(("戰鬥反應", 0), ("內功", 0)))
    s = new_state(b, S3)
    ok, costs = _plan(s, S3, [("stack:戰鬥反應", 0), ("stack:內功", 0), ("weapon", 0)])
    assert ok is None and costs == [1, 2, 1]


def test_stack_value_added_and_paid():
    # 武器 42(零級)擲 80 失敗;疊戰鬥反應 5 級(Agi+Ski+Wis=42,+50=92)→ 武器 134,成功
    b = Build("甲", stacks=(("戰鬥反應", 5),))
    d = Build("守")
    sa, sd = new_state(b, S3), new_state(d, S3)
    rec = _attack(0, "weapon", b, sa, d, sd, S3, Fixed(80, 90, 90, 4), None, Ctx(), 0)
    assert rec[2]                                    # 沒疊就失敗,疊了成功
    assert sa.stamina == 28 - 2 and sa.slots["brain"] == 1 and sa.slots["hand"] == 1
    assert sa.st.stack_acts == 1
    sa0, sd0 = new_state(Build("甲"), S3), new_state(d, S3)
    rec = _attack(0, "weapon", Build("甲"), sa0, d, sd0, S3, Fixed(80), None, Ctx(), 0)
    assert not rec[2]


def test_stack_dropped_when_unaffordable_or_over_threshold():
    b = Build("甲", stacks=(("戰鬥反應", 5),))
    d = Build("守")
    sa, sd = new_state(b, S3, stamina=1), new_state(d, S3)       # 只付得起 1 點:疊加放棄,只出手
    rec = _attack(0, "weapon", b, sa, d, sd, S3, Fixed(80), None, Ctx(), 0)
    assert rec is not None and not rec[2] and sa.st.stack_acts == 0 and sa.stamina == 0
    # 門檻 1:疊(1)可以、攻(1)可以,全串每動都 ≤ 1,總 2 付得起 → 疊
    p1 = s3_params(act_threshold=1)
    sa = new_state(b, p1)
    assert _try_seq(sa, b, p1, [[("stack:戰鬥反應", 0), ("weapon", 0)], [("weapon", 0)]]) == 0


def test_stack_pay_none_is_free():
    p = s3_params(stack_pay="none")
    b = Build("甲", stacks=(("戰鬥反應", 5),))
    d = Build("守")
    sa, sd = new_state(b, p), new_state(d, p)
    rec = _attack(0, "weapon", b, sa, d, sd, p, Fixed(80, 90, 90, 4), None, Ctx(), 0)
    assert rec[2] and sa.stamina == 28 - 1


# ---------- 副技能發動 +1/個 ----------
def test_subskill_fee_on_price_not_on_k():
    # 招架持有 link×2 + 反擊:發動費 +3。零級招架第一動價碼 1 + 3 = 4;手格 k 只前進 1
    d = Build("守", link_w2p=1, link_p2w=1, counter=1)
    sd = new_state(d, S3)
    assert _try_seq(sd, d, S3, [[("parry", 3)], [("parry", 0)]]) == 0
    assert sd.stamina == 28 - 4 and sd.slots["hand"] == 1 and sd.st.sub_fee == 3
    assert _price(sd, "parry", S3) == 2             # 下一動手格第 2 動,費用不累進


def test_subskill_fee_falls_back_when_unaffordable_or_over_threshold():
    d = Build("守", link_w2p=1)
    sd = new_state(d, S3, stamina=1)                # 1 + 1 = 2 付不起 → 不發動
    assert _try_seq(sd, d, S3, [[("parry", 1)], [("parry", 0)]]) == 1 and sd.stamina == 0
    p = s3_params(act_threshold=1)
    sd = new_state(d, p)                            # 價碼 + 費 = 2 > 門檻 1 → 不發動
    assert _try_seq(sd, d, p, [[("parry", 1)], [("parry", 0)]]) == 1


def test_partial_activation_when_not_all_affordable():
    # 門檻 3:招架持有三個副技能,全發動 1 + 3 = 4 > 3 → 退一步只發動前兩個(價碼 3)
    for order, expect in (("link_first", ["link_w2p", "link_p2w"]), ("counter_first", ["counter", "link_w2p"])):
        p = s3_params(act_threshold=3, sub_order=order)
        d = Build("守", link_w2p=1, link_p2w=1, counter=1)
        held = d.held_parry(p)
        sd = new_state(d, p)
        idx = _try_seq(sd, d, p, [[("parry", k)] for k in range(len(held), -1, -1)])
        assert held[:len(held) - idx] == expect and sd.stamina == 28 - 3, order
    # 攻擊:門檻 2,致命 + 劍意都持有 → 只發動前一個(致命,價碼 2)
    p = s3_params(act_threshold=2)
    a, d = Build("攻", crit_sub=1, reroll_any=True), Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    _attack(0, "weapon", a, sa, d, sd, p, Fixed(6, 90, 90, 5), None, Ctx(), 0)   # 致命區 6 → 致命
    assert sa.st.sub_fee == 1 and sa.stamina == 28 - 2


def test_subs_on_filters_activation():
    # subs_on 限定願意發動的副技能:持有三個但只願發動反擊 → 費用 1、沒有 link 加成
    p = s3_params()
    d = Build("守", link_w2p=1, link_p2w=1, counter=1, subs_on=("counter",))
    assert d.held_parry(p) == ["counter"]
    assert d.held_parry(p, react=True) == []                   # 對事件的反應不含反擊
    assert Build("甲", crit_sub=1, reroll_any=True, subs_on=("reroll_any",)).held_atk() == ["reroll_any"]
    assert Build("甲", crit_sub=1).held_atk() == ["crit_sub"] and Build("甲", subs_on=()).held_atk() == []
    a = Build("攻")
    sa, sd = new_state(a, p), new_state(d, p)
    # 招架 42 擲 50 失敗:沒有 link(+0)所以失敗;反擊發動 → 費 1
    _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 90, 50, 5), None, Ctx(), 0)
    assert sd.st.sub_fee == 1 and sd.stamina == 28 - 1 - 1 - 1   # 閃避 1 + 招架 1 + 費 1


def test_activate_never():
    # 持有 link 但策略 never:招架不加 10、不付費
    p = s3_params(activate="never")
    a, d = Build("攻"), Build("守", link_w2p=1)
    sa, sd = new_state(a, p), new_state(d, p)
    # 攻 10;閃避敗 90;招架 52?零級 42 擲 50 → 失敗(沒 +10);傷害骰 5(+2)= 7
    rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 90, 50, 5), None, Ctx(), 0)
    assert rec[9] is None and rec[12] == 7 and sd.st.sub_fee == 0


def test_link_both_directions_add_ten_each():
    # 招架零級 42:擲 60 → 單向 link(52)失敗;雙向 link(62)成功,吸收骰 3;傷害骰 5(+2)= 7 → 7 − 3 = 4
    a = Build("攻")
    for link_w2p, link_p2w, expect_parry in ((1, 0, False), (1, 1, True)):
        d = Build("守", link_w2p=link_w2p, link_p2w=link_p2w)
        sa, sd = new_state(a, S3), new_state(d, S3)
        seq = [10, 90, 60] + ([3] if expect_parry else []) + [5]
        rec = _attack(0, "weapon", a, sa, d, sd, S3, Fixed(*seq), None, Ctx(), 0)
        assert (rec[9] is not None) == expect_parry
        assert rec[12] == (4 if expect_parry else 7)


def test_crit_subskill_widens_zone_when_activated():
    # 技能 42:致命區 ceil(4.2)=5;致命副技能 1 級 → 6。擲 6 發動才算致命
    a = Build("攻", crit_sub=1)
    d = Build("守")
    sa, sd = new_state(a, S3), new_state(d, S3)
    rec = _attack(0, "weapon", a, sa, d, sd, S3, Fixed(6, 90, 90, 5), None, Ctx(), 0)
    assert rec[3] and sa.st.sub_fee == 1
    pn = s3_params(activate="never")
    sa, sd = new_state(a, pn), new_state(d, pn)
    rec = _attack(0, "weapon", a, sa, d, sd, pn, Fixed(6, 90, 90, 5), None, Ctx(), 0)
    assert not rec[3]


def test_reroll_any_takes_the_latter():
    res = check(50, Fixed(90, 10), reroll_any=True)               # 失敗 → 重擲取後者
    assert res.roll == 10 and res.success
    res = check(50, Fixed(20), reroll_any=True)                   # 成功就不重擲(只消耗一個值)
    assert res.roll == 20
    res = check(50, Fixed(100, 70), reroll_any=True)              # 00 特誤 → 重擲;後者仍失敗也取後者
    assert res.roll == 70 and not res.success
    res = check(50, Fixed(90, 95), reroll_any=True)
    assert res.roll == 95


def test_reroll_any_in_attack_costs_one_point():
    a = Build("攻", reroll_any=True)
    d = Build("守")
    sa, sd = new_state(a, S3), new_state(d, S3)
    rec = _attack(0, "weapon", a, sa, d, sd, S3, Fixed(90, 10, 90, 90, 5), None, Ctx(), 0)
    assert rec[2] and sa.stamina == 28 - 2 and sa.st.sub_fee == 1


# ---------- 事件→被動反應(反擊)----------
def _counter_scene(p, attacker_kw=None, atk_seq=None):
    a = Build("攻", **(attacker_kw or {}))
    d = Build("守", counter=1)
    sa, sd = new_state(a, p), new_state(d, p)
    return a, d, sa, sd


def test_counter_event_damage_and_reaction():
    # 攻擲 10 成功(T = 32);守閃避 90 敗;招架(發動反擊 +1)擲 20 成功(幅度 22)、吸收骰 8;
    # 傷害骰 1 → D_atk = 1+2 = 3 → 傷害 0;反擊差值 8 − 3 = 5,T = 22。
    # 攻方反應:閃避 90 敗、招架 90 敗 → 吃 5
    a, d, sa, sd = _counter_scene(S3)
    rec = _attack(0, "weapon", a, sa, d, sd, S3, Fixed(10, 90, 20, 8, 1, 90, 90), None, Ctx(), 0)
    assert rec[12] == 0 and sd.hp == 28
    assert sa.hp == 28 - 5 and sd.st.ev_gen == 1 and sd.st.ev_dmg_dealt == 5
    assert sa.st.ev_reacted == 1 and sa.st.ev_dmg_taken == 5
    # 反應付費:攻方手格已出手 1 次 → 招架在手格第 2 動 = 2 點;閃避腳格第 1 動 = 1 點;出手本身 1 點
    assert sa.stamina == 28 - 1 - 1 - 2 and sa.st.ev_react_cost == 3
    assert sd.st.sub_fee == 1                       # 守方發動反擊 +1


def test_counter_reaction_can_fully_dodge_or_absorb():
    a, d, sa, sd = _counter_scene(S3)
    # 反應閃避擲 1:幅度 41 ≥ T 22 → 全閃,吃 0
    _attack(0, "weapon", a, sa, d, sd, S3, Fixed(10, 90, 20, 8, 1, 1), None, Ctx(), 0)
    assert sa.hp == 28 and sa.st.ev_reacted == 1
    # 反應閃避敗、招架成功骰 6 ≥ 5 → 吸收,吃 0
    a, d, sa, sd = _counter_scene(S3)
    _attack(0, "weapon", a, sa, d, sd, S3, Fixed(10, 90, 20, 8, 1, 90, 20, 6), None, Ctx(), 0)
    assert sa.hp == 28


def test_counter_ignored_policy_and_unaffordable():
    p = s3_params(react="never")
    a, d, sa, sd = _counter_scene(p)
    _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 90, 20, 8, 1), None, Ctx(), 0)
    assert sa.hp == 28 - 5 and sa.st.ev_ignored == 1 and sa.st.ev_reacted == 0
    assert sa.stamina == 28 - 1                      # 沒反應就不多付
    # 付不起(體力只剩 0)→ 想反應也反應不了,吃後果
    a, d, sa, sd = _counter_scene(S3)
    sa.stamina = 1                                   # 出手後 0
    _attack(0, "weapon", a, sa, d, sd, S3, Fixed(10, 90, 20, 8, 1), None, Ctx(), 0)
    assert sa.hp == 28 - 5 and sa.st.ev_ignored == 1


def test_no_counter_when_not_stronger_or_not_held():
    a, d, sa, sd = _counter_scene(S3)
    # 吸收骰 2 < D_atk 3:沒有差值,招架吸收 2 → 傷害 1
    rec = _attack(0, "weapon", a, sa, d, sd, S3, Fixed(10, 90, 20, 2, 1), None, Ctx(), 0)
    assert rec[12] == 1 and sd.st.ev_gen == 0 and sa.hp == 28
    # 沒持有反擊
    d2 = Build("守")
    sa, sd = new_state(a, S3), new_state(d2, S3)
    _attack(0, "weapon", a, sa, d2, sd, S3, Fixed(10, 90, 20, 8, 1), None, Ctx(), 0)
    assert sd.st.ev_gen == 0 and sa.hp == 28
    # activate never:持有但不發動 → 沒有反擊
    p = s3_params(activate="never")
    sa, sd = new_state(a, p), new_state(Build("守", counter=1), p)
    _attack(0, "weapon", a, sa, Build("守", counter=1), sd, p, Fixed(10, 90, 20, 8, 1), None, Ctx(), 0)
    assert sd.st.ev_gen == 0


def test_counter_reaction_does_not_spawn_counter():
    # 攻方也持有反擊:反應用的招架不再生反擊(避免連鎖)
    a = Build("攻", counter=1)
    d = Build("守", counter=1)
    sa, sd = new_state(a, S3), new_state(d, S3)
    # 反應招架成功骰 8 > 事件 5:吸收,沒有第二層事件(Fixed 用完會報錯)
    _attack(0, "weapon", a, sa, d, sd, S3, Fixed(10, 90, 20, 8, 1, 90, 20, 8), None, Ctx(), 0)
    assert sa.hp == 28 and sd.hp == 28 and sa.st.ev_gen == 0


def test_counter_can_incapacitate_attacker():
    a = Build("攻", attrs={"Str": 4, "Con": 3})              # 上限 7
    d = Build("守", counter=1)
    sa, sd = new_state(a, S3), new_state(d, S3)
    sa.hp = 3                                                # 輕傷位只剩 3
    _attack(0, "weapon", a, sa, d, sd, S3, Fixed(10, 90, 20, 8, 1, 90, 90), None, Ctx(), 0)
    assert sa.hp <= 0


def test_fight_winner_when_attacker_falls_to_own_counter():
    # 整場戰鬥中,出手方被反擊打到失能 → 勝者是反擊方(fight() 兩邊都要檢查)
    a = Build("攻", attrs={"Str": 4, "Con": 3}, weapon=2)
    d = Build("守", counter=1, parry=4, weapon=0)
    wins = [0, 0]
    for seed in range(200):
        f = fight(a, d, s3_params(act_threshold=3), random.Random(seed), first=0)
        if f.winner is not None:
            wins[f.winner] += 1
            assert f.states[1 - f.winner].hp <= 0
    assert sum(wins) > 150


# ---------- 舊路徑不變 ----------
def test_defaults_keep_session2_behaviour():
    p = Params()
    a, d = Build("攻"), Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(10, 90, 90, 5), None, Ctx(), 0)
    assert rec[12] == 5 and sd.hp == 25                      # 沒有屬性補正、flat 溢傷
    assert sd.heavy == 30 - 5 // 2                           # 重傷位在舊模式也追蹤,但不影響勝負
    assert hp_max(a, p) == 30 and regen_of(a, Params(regen_stamina=3)) == 3


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            n += 1
    print(f"{n} tests passed")
