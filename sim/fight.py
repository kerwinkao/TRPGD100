"""Session 2 戰鬥迴圈(重寫)。這裡是「模擬假設」,不是規則:
先攻固定輪流、攻守策略(每動門檻)、步法的宣告時機、副技能的解讀,都寫在這裡並列入 REPORT / ambiguities。
解算一律呼叫 resolve.py。Session 1 版本見 fight_s1.py。

結構(spec.md「回合狀態」):
- 三個消耗格:手 / 腳 / 腦。回合內同格第 k 動耗 k 點;技能等級給 ceil(level/2) 次低價(各 1 點)。回合末歸零。
- 一回合 = 雙方各自的「手番」依序一次;手番內可連續出手,直到一動的價碼超過 act_threshold 或付不起。
  守方的防禦動作吃的是同一組消耗格(對手手番時也會推進自己的格)。
- 防禦順序:步法(若宣告)→ 閃避 → 招架 → 格擋。只有閃避侵蝕 T;招架、格擋做傷害對抗。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from resolve import (Ctx, Params, block, check, damage, dodge, overflow, parry,
                     shield_value, step)

BASE = 42  # 均值角色(全 14)模擬技能的底值
SLOT = {"weapon": "hand", "offhand": "hand", "parry": "hand", "block": "hand",
        "step": "foot", "dodge": "foot"}
NAME = {"weapon": "長劍", "offhand": "短劍", "step": "步法", "dodge": "閃避",
        "parry": "招架", "block": "格擋", "neigong": "內功"}
DIE = {"weapon": 8, "offhand": 4}


def skill_value(level: int, base: int = BASE) -> int:
    return base + 10 * level


def cheap_count(level: int) -> int:
    """技能等級給的低價次數:ceil(level/2),等級 1/3/5/7/9 → 1/2/3/4/5。"""
    return (level + 1) // 2 if level > 0 else 0


@dataclass(frozen=True)
class Build:
    name: str
    weapon: int = 0                    # 長劍等級
    offhand: Optional[int] = None      # 短劍等級;None = 沒帶副手
    step: int = 0
    dodge: int = 0
    parry: int = 0
    block: Optional[int] = None        # None = 沒帶盾;有無盾還要 Params.shield 不為 None
    neigong: int = 0                   # 內功;等級 ≥ 1 才有內力池(A12)
    crit_bonus: int = 0                # 長劍致命類副技能加值
    strength: int = 14
    step_def: Optional[str] = None     # 守:步法輔助哪個動作 'dodge' | 'parry' | 'block' | None
    step_atk: bool = False             # 攻:出手前先用步法輔助這一擊
    neili_first: bool = False          # 內力流:先燒內力
    lianci: bool = False               # 連刺:同一手番上一擊(擲骰)成功 → 本擊 +10(A29)
    shunshi: bool = False              # 順勢格擋:招架不再侵蝕,本輪無對應效果(A29)
    xieren: bool = False               # 卸刃:「繳械判定」未定義,本輪無對應效果(A29)
    skill_override: Optional[dict] = None   # E7 高手:直接指定技能值
    level_override: Optional[dict] = None   # 對應的等級(侵蝕上限、低價次數用)

    def level(self, key: str) -> int:
        if self.level_override and key in self.level_override:
            return self.level_override[key]
        v = getattr(self, key)
        return v or 0

    def value(self, key: str) -> int:
        if self.skill_override and key in self.skill_override:
            return self.skill_override[key]
        return skill_value(self.level(key))


def dual_penalty(p: Params) -> tuple:
    """雙持減值 (主手, 副手)。無技能 −10/−30;一級 −10/−10;等級高先抵銷副手減值(A27)。"""
    return {"none": (10, 30), "lv1": (10, 10), "full": (0, 0)}[p.dual_wield]


class Stats:
    """每名戰鬥者的統計。攻方欄位記在出手者身上,防方欄位記在被攻擊者身上。"""
    __slots__ = ("att", "succ", "crit_roll", "fumble", "thru", "dmg_hit", "dmg_total",
                 "full_dodged", "parry_try", "parry_ok", "block_try", "block_ok",
                 "step_try", "step_ok", "step_sum", "step_skipped",
                 "acts", "atk_acts", "def_acts", "refuse_n", "winded_n",
                 "first_refuse", "first_winded", "faced",
                 "cat_n", "cat_dmg", "dodge_try")

    def __init__(self):
        for k in self.__slots__:
            setattr(self, k, 0)
        self.first_refuse = None
        self.first_winded = None
        self.cat_n = {"crit_maxdie": 0, "through_parry": 0, "no_guard": 0}
        self.cat_dmg = {"crit_maxdie": 0, "through_parry": 0, "no_guard": 0}


class State:
    def __init__(self, b: Build, p: Params, stamina: int):
        self.hp = p.hp
        self.stamina = stamina
        self.stamina_max = stamina
        self.neili = b.value("neigong") if b.level("neigong") >= 1 else 0   # A12
        self.neili_max = self.neili
        self.slots = {"hand": 0, "foot": 0, "brain": 0}
        self.cheap: dict = {}
        self.rnd = 0
        self.st = Stats()
        self.reset_round(b)

    def reset_round(self, b: Build):
        self.slots = {"hand": 0, "foot": 0, "brain": 0}
        self.cheap = {k: cheap_count(b.level(k)) for k in
                      ("weapon", "offhand", "step", "dodge", "parry", "block")}

    @property
    def avail(self) -> int:
        return self.stamina + self.neili

    def pay(self, cost: int, neili_first: bool):
        a, b = ("neili", "stamina") if neili_first else ("stamina", "neili")
        take = min(cost, getattr(self, a))
        setattr(self, a, getattr(self, a) - take)
        setattr(self, b, getattr(self, b) - (cost - take))


def _pool(key: str, p: Params) -> str:
    return "weapon" if (key == "parry" and p.parry_pool == "weapon") else key


def _price(s: State, key: str, p: Params) -> int:
    """cost(slot, skill) = 1 if cheap[skill] > 0 else slots[slot] + 1(spec.md)。"""
    return 1 if s.cheap.get(_pool(key, p), 0) > 0 else s.slots[SLOT[key]] + 1


def _commit(s: State, b: Build, key: str, p: Params, cost: int):
    pk = _pool(key, p)
    if s.cheap.get(pk, 0) > 0:
        s.cheap[pk] -= 1
    s.slots[SLOT[key]] += 1
    s.pay(cost, b.neili_first)
    s.st.acts += 1


def _try(s: State, b: Build, key: str, p: Params) -> bool:
    """想做一動:價碼超過門檻 → 拒絕(refuse);付不起 → 喘(winded);否則付費並記一動。"""
    c = _price(s, key, p)
    if c > p.act_threshold:
        s.st.refuse_n += 1
        if s.st.first_refuse is None:
            s.st.first_refuse = s.rnd
        return False
    if c > s.avail:
        s.st.winded_n += 1
        if s.st.first_winded is None:
            s.st.first_winded = s.rnd
        return False
    _commit(s, b, key, p, c)
    return True


def _try_pair(s: State, b: Build, k1: str, k2: str, p: Params) -> bool:
    """步法 + 目標動作:兩動都在門檻內、合計付得起,才先付第一動(第二動由呼叫端接著 _try)。
    同格輔助累進(腳 1 + 腳 2),跨格各算各格第一動。"""
    c1 = _price(s, k1, p)
    if c1 > p.act_threshold:
        return False
    sl, pk = SLOT[k1], _pool(k1, p)
    old_slot, old_cheap = s.slots[sl], s.cheap.get(pk, 0)
    if old_cheap > 0:
        s.cheap[pk] = old_cheap - 1
    s.slots[sl] = old_slot + 1
    c2 = _price(s, k2, p)
    s.slots[sl] = old_slot
    if old_cheap > 0:
        s.cheap[pk] = old_cheap
    if c2 > p.act_threshold or c1 + c2 > s.avail:
        return False
    _commit(s, b, k1, p, c1)
    return True


def new_state(b: Build, p: Params, stamina: int = 28) -> State:
    return State(b, p, stamina)


# 一次出手的紀錄(keep_events 時保存):
# (出手者, 武器, 擲骰成功, 致命, 特誤, 步法S, 全閃, T, T_after_dodge, D_招架, D_格擋, D_def, 傷害, 類別)
def _attack(i: int, key: str, b: Build, s: State, d: Build, sd: State, p: Params, rng,
            log, ctx: Ctx, lianci_bonus: int):
    """出手一次:先(選配)步法,再武器擲骰;成功 → 守方走防禦鏈 → 傷判。
    回傳 None(沒出手:被門檻擋或付不起)或紀錄 tuple。"""
    S = 0
    a_ctx = Ctx(session=ctx.session, turn=ctx.turn, actor=b.name)
    if b.step_atk and _try_pair(s, b, "step", key, p):
        S = step(b.value("step"), rng, params=p, log=log, ctx=a_ctx)
        s.st.step_try += 1
        s.st.step_ok += S > 0
        s.st.step_sum += S
    if not _try(s, b, key, p):
        return None
    st = s.st
    st.atk_acts += 1
    st.att += 1

    pen_main, pen_off = dual_penalty(p)
    pen = 0
    if b.offhand is not None:
        pen = pen_main if key == "weapon" else pen_off
    eff = max(b.value(key) - pen + S + lianci_bonus, 0)
    res = check(eff, rng, crit_bonus=b.crit_bonus, params=p, log=log,
                ctx=Ctx(session=ctx.session, turn=ctx.turn, actor=b.name, skill=NAME[key]))
    if res.fumble:
        st.fumble += 1
    if not res.success:                      # 失手(含 A21 攻方特誤)
        return (i, key, False, False, res.fumble, S, False, 0, 0, None, None, 0, 0, None)
    st.succ += 1
    st.crit_roll += res.crit
    T = res.margin

    # ---- 守方防禦鏈 ----
    dst = sd.st
    dst.faced += 1
    d_ctx = Ctx(session=ctx.session, turn=ctx.turn, actor=d.name)
    boost = {"dodge": 0, "parry": 0, "block": 0}
    if d.step_def is not None and _can_use(d, p, d.step_def):
        if _try_pair(sd, d, "step", d.step_def, p):
            Sd = step(d.value("step"), rng, params=p, log=log, ctx=d_ctx)
            dst.step_try += 1
            dst.step_ok += Sd > 0
            dst.step_sum += Sd
            boost[d.step_def] = Sd
        else:
            dst.step_skipped += 1
    full = False
    T_after = T
    if _try(sd, d, "dodge", p):
        dst.def_acts += 1
        T_after, full = dodge(T, d.value("dodge") + boost["dodge"], d.level("dodge"), rng,
                              params=p, log=log, ctx=replace_ctx(d_ctx, T))
        dst.dodge_try += 1
    if full:
        st.full_dodged += 1
        return (i, key, True, res.crit, False, S, True, T, 0, None, None, 0, 0, None)
    st.thru += 1

    d_par = d_blk = None
    if _try(sd, d, "parry", p):
        dst.def_acts += 1
        dst.parry_try += 1
        pen_p = dual_penalty(p)[0] if d.offhand is not None else 0
        d_par = parry(max(d.value("parry") - pen_p + boost["parry"], 0), DIE["weapon"], rng,
                      params=p, log=log, ctx=d_ctx)
        dst.parry_ok += d_par is not None
    if _can_use(d, p, "block") and _try(sd, d, "block", p):
        dst.def_acts += 1
        dst.block_try += 1
        d_blk = block(d.value("block") + boost["block"],
                      shield_value(p.shield, d.strength), rng, params=p, log=log, ctx=d_ctx)
        dst.block_ok += d_blk is not None
    parts = [x for x in (d_par, d_blk) if x is not None]
    D_def = 0 if not parts else (sum(parts) if p.defense_combine == "sum" else max(parts))

    ov = overflow(eff)
    dmg = damage(DIE.get(key, 8), ov, res.crit, D_def, rng)
    cat = None
    if dmg > 0:
        st.dmg_hit += 1
        st.dmg_total += dmg
        cat = ("crit_maxdie" if (res.crit and ov > 0) else
               "through_parry" if parts else "no_guard")
        st.cat_n[cat] += 1
        st.cat_dmg[cat] += dmg
    sd.hp -= dmg
    return (i, key, True, res.crit, False, S, False, T, T_after, d_par, d_blk, D_def, dmg, cat)


def replace_ctx(c: Ctx, T: int) -> Ctx:
    return Ctx(session=c.session, turn=c.turn, actor=c.actor, challenge_value=T)


def _can_use(b: Build, p: Params, key: str) -> bool:
    if key == "block":
        return b.block is not None and p.shield is not None
    return True


@dataclass
class FightOut:
    winner: Optional[int]      # 0 / 1 / None(回合上限平手)
    rounds: int
    states: tuple
    events: list               # keep_events 時:每擊紀錄 tuple
    first_crit: Optional[int] = None      # 第一個擲出致命(攻擊成功且落入致命區)的一方
    first_crit_hit: Optional[int] = None  # 第一個「致命且造成傷害」的一方
    stalled: bool = False      # 雙方池皆空且無回復 → 無人能再動,提前判平手(A30)


def fight(b0: Build, b1: Build, p: Params, rng, first: int = 0, max_rounds: int = 300,
          stamina: int = 28, log=None, session: int = 0, keep_events: bool = False) -> FightOut:
    builds = (b0, b1)
    states = (new_state(b0, p, stamina), new_state(b1, p, stamina))
    events = []
    order = (first, 1 - first)
    first_crit = first_crit_hit = None
    for rnd in range(1, max_rounds + 1):
        for s in states:
            s.rnd = rnd
        for i in order:
            j = 1 - i
            b, s, d, sd = builds[i], states[i], builds[j], states[j]
            ctx = Ctx(session=session, turn=rnd)
            k = 0
            prev_ok = False
            for _ in range(60):                       # 手番內連續出手(實際由消耗與門檻截止)
                key = "offhand" if (b.offhand is not None and k % 2 == 1) else "weapon"
                bonus = 10 if (b.lianci and prev_ok and k > 0) else 0
                rec = _attack(i, key, b, s, d, sd, p, rng, log, ctx, bonus)
                if rec is None:
                    break
                k += 1
                prev_ok = rec[2]
                if first_crit is None and rec[3]:
                    first_crit = i
                if first_crit_hit is None and rec[3] and rec[12] > 0:
                    first_crit_hit = i
                if keep_events:
                    events.append(rec)
                if sd.hp <= 0:
                    return FightOut(i, rnd, states, events, first_crit, first_crit_hit)
        for s, b in zip(states, builds):
            s.reset_round(b)
            s.stamina = min(s.stamina_max, s.stamina + p.regen_stamina)
            s.neili = min(s.neili_max, s.neili + p.regen_neili)
        if (p.regen_stamina <= 0 and p.regen_neili <= 0
                and states[0].avail <= 0 and states[1].avail <= 0):
            return FightOut(None, rnd, states, events, first_crit, first_crit_hit, True)
    return FightOut(None, max_rounds, states, events, first_crit, first_crit_hit)
