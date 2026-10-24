"""Session 5 新規則測試:主動權交換(取代雙方各一手番)、守方 counter_policy 對攻、
同時結算與雙敗、主動權交換量測。可用 pytest,也可直接 `python3 sim/tests/test_fight_s5.py`。"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fight import Build, _attack, _counter_attack_wanted, _exchange, fight, new_state
from resolve import Ctx, Params, s4_params, s5_params


class Fixed:
    def __init__(self, *vals):
        self.vals = list(vals)

    def randint(self, a, b):
        v = self.vals.pop(0)
        assert a <= v <= b, (a, b, v)
        return v


S5 = s5_params()


def attack(a, d, p, seq, **kw):
    sa, sd = new_state(a, p), new_state(d, p)
    info = {}
    rec = _attack(0, "weapon", a, sa, d, sd, p, Fixed(*seq), None, Ctx(), 0, info)
    return rec, info, sa, sd


# ---------- 開關 ----------
def test_switches_default_to_old_behaviour():
    assert Params().initiative == "turns" and Params().counter_policy == "never"
    assert S5.initiative == "swap" and S5.counter_policy == "never" and S5.sub_fee == 0.2
    assert s5_params(counter_policy="always").counter_policy == "always"
    f = fight(Build("甲"), Build("乙"), s4_params(act_threshold=3), random.Random(1))
    assert f.flow is None                                          # 舊手番模式不填量測


# ---------- 防禦成功 = 換手 ----------
def test_defended_flags():
    a, d = Build("攻"), Build("守")
    # 全部失敗:攻方保有主動
    rec, info, *_ = attack(a, d, S5, [10, 90, 90, 5])
    assert info == {"hit": True, "defended": False}
    # 招架成功(擲 20,骰 3)→ 即使仍有傷害穿透也換手
    rec, info, sa, sd = attack(a, d, S5, [10, 90, 20, 3, 5])
    assert info == {"hit": True, "defended": True} and rec[12] == 4 and sd.hp == 24
    # 全閃 → 換手
    rec, info, *_ = attack(a, d, S5, [10, 5])
    assert rec[6] and info["defended"]
    # 攻擊落空 → 沒有防禦,hit False(由呼叫端換手)
    rec, info, *_ = attack(a, d, S5, [90])
    assert info == {"hit": False, "defended": False}


def test_block_success_counts_as_defended():
    a, d = Build("攻"), Build("守", block=0, shield="small")
    # 閃避敗、招架敗、格擋成功(擲 20 ≤ 52)→ 換手
    rec, info, *_ = attack(a, d, s5_params(), [10, 90, 90, 20, 5])
    assert info["defended"] and rec[10] is not None


def test_partial_dodge_counts_only_when_dodge_swap_any():
    a, d = Build("攻"), Build("守")
    seq = [1, 30, 90, 5]            # 攻擊幅度 41;閃避擲 30 成功但只侵蝕 m = 12(未全閃);招架敗
    _, info, *_ = attack(a, d, S5, seq)
    assert info["defended"] is False                      # 預設:只有全閃算
    _, info, *_ = attack(a, d, s5_params(dodge_swap="any"), seq)
    assert info["defended"] is True


# ---------- 一整回合的主動權流向 ----------
def test_round_flow_scripted():
    # 門檻 1:每動只付得起各格第一動。甲攻(擲 10)→ 乙閃避敗、招架成功 → 主動權換乙;
    # 乙手格第 2 動價碼 2 > 1 停手 → 換甲;甲也付不起第 2 動 → 回合結束
    p = s5_params(act_threshold=1)
    f = fight(Build("甲"), Build("乙"), p, Fixed(10, 90, 20, 3, 5), first=0, max_rounds=1)
    fl = f.flow
    assert f.states[1].hp == 24 and f.states[0].hp == 28
    assert (fl["exchanges"], fl["swap_defend"], fl["swap_miss"], fl["swap_stop"]) == (1, 1, 0, 1)
    assert (fl["hold_n"], fl["hold_len_sum"], fl["hold_empty"]) == (1, 1, 2)


def test_miss_hands_over_initiative():
    # 甲落空 → 主動權換乙;乙(手格還沒用過)出手也落空 → 換回甲;甲、乙第 2 動價碼 2 > 門檻 1,雙雙停手
    p = s5_params(act_threshold=1)
    f = fight(Build("甲"), Build("乙"), p, Fixed(90, 90), first=0, max_rounds=1)
    fl = f.flow
    assert (fl["exchanges"], fl["swap_miss"], fl["swap_defend"], fl["swap_stop"]) == (2, 2, 0, 1)
    assert f.states[0].hp == 28 and f.states[1].hp == 28


def test_attacker_keeps_initiative_when_all_defenses_fail():
    # 甲連出兩擊(第二擊價碼 2 ≤ 門檻 2),乙全防失敗 → 甲一直持有主動
    p = s5_params(act_threshold=2)
    seq = [10, 90, 90, 5, 10, 90, 90, 5]
    f = fight(Build("甲"), Build("乙"), p, Fixed(*seq), first=0, max_rounds=1)
    fl = f.flow
    assert f.states[1].hp == 28 - 7 - 7
    assert fl["exchanges"] == 2 and fl["swap_defend"] == 0 and fl["swap_miss"] == 0
    # 連續出手長度:甲這一段持主動連出 2 擊(之後付不起停手)
    assert fl["hold_len_sum"] >= 2


def test_rounds_end_when_both_stop_and_fight_stalls():
    huge = {"Str": 200, "Con": 10}                                  # 打不死,Con 10 回復 0
    a = Build("甲", attrs=huge, weapon_attr="Ski")
    b = Build("乙", attrs=huge, weapon_attr="Ski")
    f = fight(a, b, s5_params(act_threshold=3), random.Random(3))
    assert f.stalled and f.rounds < 300
    fl = f.flow
    assert fl["hold_len_sum"] == fl["exchanges"]                    # 回合都是雙方停手收尾,每段持主動都有結算


def test_flow_invariants_over_random_fights():
    for pol in ("never", "always", "lead"):
        p = s5_params(act_threshold=3, counter_policy=pol)
        for seed in range(80):
            f = fight(Build("甲", weapon=2, parry=1), Build("乙", weapon=2, parry=1), p, random.Random(seed),
                      first=seed % 2)
            fl = f.flow
            assert fl["hold_len_sum"] <= fl["exchanges"]
            assert sum(s.st.ca_decisions for s in f.states) == fl["exchanges"]
            assert fl["counter_attacks"] + fl["counter_unaffordable"] <= fl["exchanges"]
            if pol == "never":
                assert fl["counter_attacks"] == 0 and fl["swap_defend"] + fl["swap_miss"] >= 0
            if f.winner is not None:
                assert f.states[1 - f.winner].hp <= 0 and f.states[f.winner].hp > 0
            elif f.double_ko:
                assert f.states[0].hp <= 0 and f.states[1].hp <= 0


# ---------- 對攻 ----------
def test_counter_policy_decision():
    a, d = Build("攻"), Build("守")
    sa, sd = new_state(a, S5), new_state(d, S5)
    assert not _counter_attack_wanted(d, sd, sa, s5_params(counter_policy="never"))
    assert _counter_attack_wanted(d, sd, sa, s5_params(counter_policy="always"))
    lead = s5_params(counter_policy="lead")
    assert not _counter_attack_wanted(d, sd, sa, lead)                 # 血量相等:不算領先
    sa.hp -= 1
    assert _counter_attack_wanted(d, sd, sa, lead)                     # 守方輕傷位領先攻方
    sd.hp -= 5
    assert not _counter_attack_wanted(d, sd, sa, lead)
    # 舊手番模式沒有對攻;Build 可覆寫策略
    assert not _counter_attack_wanted(d, sd, sa, s4_params(counter_policy="always"))
    d2 = Build("守", counter_policy="always")
    assert _counter_attack_wanted(d2, sd, sa, s5_params(counter_policy="never"))


def test_counter_attack_simultaneous_no_defense():
    # 對攻:雙方各付一動、各擲骰(甲 10 成、乙 20 成)、誰都不防;傷害骰甲 5(+2 = 7)、乙 3(+2 = 5),同時結算
    p = s5_params(act_threshold=1, counter_policy="always")
    f = fight(Build("甲"), Build("乙"), p, Fixed(10, 20, 5, 3), first=0, max_rounds=1)
    sa, sd = f.states
    assert sd.hp == 28 - 7 and sa.hp == 28 - 5
    assert sd.st.ca_n == 1 and sa.st.ca_decisions == 0 and sd.st.ca_decisions == 1
    assert sd.st.def_acts == 0 and sd.st.parry_try == 0 and sd.st.dodge_try == 0   # 守方沒有走防禦鏈
    assert f.flow["counter_attacks"] == 1 and f.flow["swap_defend"] == 0


def test_counter_attack_pays_progressive_cost():
    p = s5_params(act_threshold=3, counter_policy="always")
    a, d = Build("攻"), Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    info = {}
    _exchange(0, "weapon", a, sa, d, sd, p, Fixed(10, 20, 5, 3), None, Ctx(), 0, info)
    assert info["counter"] and sd.slots["hand"] == 1 and sd.stamina == 28 - 1
    # 守方手格已用一動:下一次對攻價碼 2
    _exchange(0, "weapon", a, sa, d, sd, p, Fixed(10, 20, 5, 3), None, Ctx(), 0, {})
    assert sd.slots["hand"] == 2 and sd.stamina == 28 - 1 - 2


def test_counter_unaffordable_falls_back_to_defense():
    p = s5_params(act_threshold=3, counter_policy="always")
    a, d = Build("攻"), Build("守")
    sa, sd = new_state(a, p), new_state(d, p)
    sd.stamina = 0
    info = {}
    rec = _exchange(0, "weapon", a, sa, d, sd, p, Fixed(10, 5), None, Ctx(), 0, info)
    assert not info["counter"] and sd.st.ca_unaffordable == 1 and sd.st.ca_n == 0
    assert rec[12] == 7                                              # 守方什麼都做不了,吃全額


def test_counter_initiative_by_hit_vs_keep():
    # 甲落空(擲 90)、乙對攻命中(擲 20):by_hit → 甲交出主動;keep → 甲仍持有
    for rule, swaps in (("by_hit", 1), ("keep", 0)):
        p = s5_params(act_threshold=1, counter_policy="always", counter_initiative=rule)
        f = fight(Build("甲"), Build("乙"), p, Fixed(90, 20, 3), first=0, max_rounds=1)
        assert f.flow["swap_miss"] == swaps
        assert f.states[0].hp == 28 - 5 and f.states[1].hp == 28


def test_double_ko_is_a_draw():
    frail = {"Str": 2, "Con": 2}                                     # 輕傷位 4,無補正
    p = s5_params(act_threshold=1, counter_policy="always")
    f = fight(Build("甲", attrs=frail), Build("乙", attrs=frail), p, Fixed(10, 20, 4, 4), first=0)
    assert f.winner is None and f.double_ko
    assert f.states[0].hp <= 0 and f.states[1].hp <= 0


def test_counter_hit_can_finish_attacker():
    frail = {"Str": 2, "Con": 2}
    p = s5_params(act_threshold=1, counter_policy="always")
    # 甲落空、乙命中打到甲失能 → 乙勝
    f = fight(Build("甲", attrs=frail), Build("乙"), p, Fixed(90, 20, 6), first=0)
    assert f.winner == 1 and f.states[0].hp <= 0


# ---------- 與其他規則的相容 ----------
def test_neili_heal_works_in_swap_mode():
    for seed in range(20):
        b = Build("甲", weapon=2, neigong=2, neili_use=("heal",))
        f = fight(b, Build("乙", weapon=2), s5_params(act_threshold=3), random.Random(seed), first=seed % 2)
        assert f.states[0].qi <= f.states[0].qi_max and f.flow is not None


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            n += 1
    print(f"{n} tests passed")
