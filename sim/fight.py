"""Session 2 戰鬥迴圈(重寫)。這裡是「模擬假設」,不是規則:
先攻固定輪流、攻守策略(每動門檻)、步法的宣告時機、副技能的解讀,都寫在這裡並列入 REPORT / ambiguities。
解算一律呼叫 resolve.py。Session 1 版本見 fight_s1.py。

Session 3 加入(皆由 Params 開關 / Build 新欄位啟用,預設值維持 Session 2 行為):
- 傷勢三位:輕傷位 −D、重傷位 −⌊D/2⌋;輕傷位 0 失能(戰鬥結束),重傷位 0 擊殺。上限 Str+Con。
- 體力池 Con+Con、回復 = Con 效益值;傷害 = 四捨五入((骰 + 屬性補正) × (1 + 溢出/100)) − D_def。
- 疊加佔格付費:疊的技能走自己的格(腦格)、累進與低價次數,疊加值 = 被疊技能全值(A10)。
- 副技能發動:每個 +1 點,加在價碼上、不進累進格的 k;付不起就不發動。link(雙向 +10/級)、致命、任意重骰(劍意)。
- 事件→被動反應:動作生成對手必須回應的事件(第一個實例:反擊),對手付體力走防禦鏈,或不回應吃後果。

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

from resolve import (Ctx, Params, block, check, damage, damage_s3, dodge, effect_value,
                     overflow, parry, parry_ex, shield_value, step)

BASE = 42  # 均值角色(全 14)模擬技能的底值
SLOT = {"weapon": "hand", "offhand": "hand", "parry": "hand", "block": "hand",
        "step": "foot", "dodge": "foot"}
NAME = {"weapon": "長劍", "offhand": "短劍", "step": "步法", "dodge": "閃避",
        "parry": "招架", "block": "格擋", "neigong": "內功"}
DIE = {"weapon": 8, "offhand": 4}
# 疊加技能:疊的是被疊技能的全值(A10),每疊一個是一次動作,佔該技能所在的格(A38:腦格為假設)
STACK_ATTR = {"戰鬥反應": ("Agi", "Ski", "Wis"), "內功": ("Con", "Cha", "Wis")}
STACK_SLOT = {"戰鬥反應": "brain", "內功": "brain"}


def slot_of(key: str) -> str:
    return SLOT[key] if key in SLOT else STACK_SLOT[key.split(":", 1)[1]]


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
    # ---- Session 3 ----
    attrs: Optional[dict] = None       # 七屬性 {"Str":14,...};None = 全 14
    base: Optional[dict] = None        # 各技能鍵 → 三屬性底值;None / 缺項 = BASE(42)
    shield: Optional[str] = None       # 'small' | 'large' | None;None 時退回 Params.shield(Session 2 行為)
    weapon_name: str = "長劍"
    weapon_die: int = 8                # 主手武器骰;副手固定 DIE['offhand']
    weapon_attr: str = "Str"           # 傷害補正取哪個屬性的效益值('Str' 重兵 / 'Ski' 輕兵)
    link_w2p: int = 0                  # 武器技能掛【招架】link 的等級(+10/級);散修 ≤ 1
    link_p2w: int = 0                  # 招架掛【武器】link 的等級
    counter: int = 0                   # 招架掛【反擊】的等級(> 0 即持有)
    crit_sub: int = 0                  # 武器掛【致命】的等級(致命區 +1/級,發動才算)
    reroll_any: bool = False           # 武器掛任意重骰(【劍意】類)
    stacks: tuple = ()                 # 疊加:((技能名, 等級), ...),如 (("戰鬥反應", 5), ("內功", 5))
    subs_on: Optional[tuple] = None    # 願意發動的副技能名單('link_w2p','link_p2w','counter','crit_sub','reroll_any');None = 持有的都願意

    def level(self, key: str) -> int:
        if self.level_override and key in self.level_override:
            return self.level_override[key]
        v = getattr(self, key)
        return v or 0

    def value(self, key: str) -> int:
        if self.skill_override and key in self.skill_override:
            return self.skill_override[key]
        base = self.base.get(key, BASE) if self.base else BASE
        return skill_value(self.level(key), base)

    def attr(self, code: str) -> int:
        return self.attrs.get(code, 14) if self.attrs else 14

    def stack_value(self, name: str, level: int) -> int:
        return sum(self.attr(a) for a in STACK_ATTR[name]) + 10 * level

    def held_atk(self) -> list:
        """攻擊動作可發動的副技能,依發動優先序(付不起全部時取前 k 個)。"""
        return [n for n, on in (("crit_sub", self.crit_sub > 0), ("reroll_any", self.reroll_any))
                if on and (self.subs_on is None or n in self.subs_on)]

    def held_parry(self, p: Params, react: bool = False) -> list:
        """招架動作可發動的副技能,依 Params.sub_order 排序;對事件的反應不含反擊(避免連鎖)。"""
        order = (("link_w2p", "link_p2w", "counter") if p.sub_order == "link_first"
                 else ("counter", "link_w2p", "link_p2w"))
        on = {"link_w2p": self.link_w2p > 0, "link_p2w": self.link_p2w > 0,
              "counter": self.counter > 0 and not react}
        return [n for n in order if on[n] and (self.subs_on is None or n in self.subs_on)]


def dual_penalty(p: Params) -> tuple:
    """雙持減值 (主手, 副手)。無技能 −10/−30;一級 −10/−10;等級高先抵銷副手減值(A27)。"""
    return {"none": (10, 30), "lv1": (10, 10), "full": (0, 0)}[p.dual_wield]


def hp_max(b: Build, p: Params) -> int:
    return b.attr("Str") + b.attr("Con") if p.hp_model == "str_con" else p.hp


def stamina_max(b: Build, p: Params) -> int:
    return 2 * b.attr("Con") if p.stamina_model == "con" else 28


def regen_of(b: Build, p: Params) -> int:
    return effect_value(b.attr("Con")) if p.regen_model == "con" else p.regen_stamina


def shield_of(b: Build, p: Params) -> Optional[str]:
    return b.shield if b.shield is not None else p.shield


class Stats:
    """每名戰鬥者的統計。攻方欄位記在出手者身上,防方欄位記在被攻擊者身上。"""
    __slots__ = ("att", "succ", "crit_roll", "fumble", "thru", "dmg_hit", "dmg_total",
                 "full_dodged", "parry_try", "parry_ok", "block_try", "block_ok",
                 "step_try", "step_ok", "step_sum", "step_skipped",
                 "acts", "atk_acts", "def_acts", "refuse_n", "winded_n",
                 "first_refuse", "first_winded", "faced",
                 "cat_n", "cat_dmg", "dodge_try",
                 "sub_fee", "stack_acts", "sub_active", "ev_gen", "ev_dmg_dealt",
                 "ev_reacted", "ev_ignored", "ev_dmg_taken", "ev_react_cost")

    def __init__(self):
        for k in self.__slots__:
            setattr(self, k, 0)
        self.first_refuse = None
        self.first_winded = None
        self.cat_n = {"crit_maxdie": 0, "through_parry": 0, "no_guard": 0}
        self.cat_dmg = {"crit_maxdie": 0, "through_parry": 0, "no_guard": 0}


class State:
    def __init__(self, b: Build, p: Params, stamina: Optional[int] = None):
        if stamina is None:
            stamina = stamina_max(b, p)
        self.hp_cap = hp_max(b, p)
        self.hp = self.hp_cap              # 輕傷位(休息即回,戰鬥中不回)
        self.heavy = self.hp_cap           # 重傷位(吃一半)
        self.stamina = stamina
        self.stamina_max = stamina
        self.neili = b.value("neigong") if b.level("neigong") >= 1 else 0   # A12
        self.neili_max = self.neili
        self.slots = {"hand": 0, "foot": 0, "brain": 0}
        self.cheap: dict = {}
        self.rnd = 0
        self.st = Stats()
        self.reset_round(b)

    def take(self, d: int):
        """受傷 D:輕傷位 −D,重傷位 −⌊D/2⌋。輕傷位 ≤ 0 失能(戰鬥結束),重傷位 ≤ 0 擊殺。"""
        self.hp -= d
        self.heavy -= d // 2

    def reset_round(self, b: Build):
        self.slots = {"hand": 0, "foot": 0, "brain": 0}
        self.cheap = {k: cheap_count(b.level(k)) for k in
                      ("weapon", "offhand", "step", "dodge", "parry", "block")}
        for name, lv in b.stacks:
            self.cheap["stack:" + name] = cheap_count(lv)

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
    return 1 if s.cheap.get(_pool(key, p), 0) > 0 else s.slots[slot_of(key)] + 1


def _commit(s: State, b: Build, key: str, p: Params, cost: int):
    pk = _pool(key, p)
    if s.cheap.get(pk, 0) > 0:
        s.cheap[pk] -= 1
    s.slots[slot_of(key)] += 1
    s.pay(cost, b.neili_first)
    s.st.acts += 1


def _refuse(s: State):
    s.st.refuse_n += 1
    if s.st.first_refuse is None:
        s.st.first_refuse = s.rnd


def _wind(s: State):
    s.st.winded_n += 1
    if s.st.first_winded is None:
        s.st.first_winded = s.rnd


def _try(s: State, b: Build, key: str, p: Params, extra: int = 0) -> bool:
    """想做一動:價碼超過門檻 → 拒絕(refuse);付不起 → 喘(winded);否則付費並記一動。
    extra = 發動副技能的費用(每個 +1,加在價碼上、不進累進格的 k)。"""
    c = _price(s, key, p) + extra
    if c > p.act_threshold:
        _refuse(s)
        return False
    if c > s.avail:
        _wind(s)
        return False
    _commit(s, b, key, p, c)
    if extra:
        s.st.sub_fee += extra
    return True


def _plan(s: State, p: Params, actions: list) -> tuple:
    """不改狀態地模擬一串動作((鍵, 副技能費)...)的總價:每動都要 ≤ 門檻,總和要付得起。
    回傳 (失敗原因 None|'refuse'|'wind', 各動價碼)。同格的動作累進、技能低價次數依序消耗。"""
    slots, cheap, total, costs = dict(s.slots), dict(s.cheap), 0, []
    for key, extra in actions:
        pk, sl = _pool(key, p), slot_of(key)
        if cheap.get(pk, 0) > 0:
            price = 1
            cheap[pk] -= 1
        else:
            price = slots[sl] + 1
        slots[sl] += 1
        c = price + extra
        if c > p.act_threshold:
            return "refuse", costs
        total += c
        costs.append(c)
    if total > s.avail:
        return "wind", costs
    return None, costs


def _try_seq(s: State, b: Build, p: Params, variants: list) -> Optional[int]:
    """依偏好順序試幾種「動作串」(如:疊兩個再攻並發動副技能 → 疊兩個再攻不發動 → ...)。
    回傳第一個付得起的 variant 索引並付費;全失敗回 None,只按最後一個(最便宜)的失敗原因記一次拒絕 / 喘。"""
    why = None
    for idx, actions in enumerate(variants):
        why, costs = _plan(s, p, actions)
        if why is None:
            for (key, extra), c in zip(actions, costs):
                _commit(s, b, key, p, c)
                if extra:
                    s.st.sub_fee += extra
                if key.startswith("stack:"):
                    s.st.stack_acts += 1
            return idx
    (_refuse if why == "refuse" else _wind)(s)
    return None


def _try_pair(s: State, b: Build, k1: str, k2: str, p: Params) -> bool:
    """步法 + 目標動作:兩動都在門檻內、合計付得起,才先付第一動(第二動由呼叫端接著 _try)。
    同格輔助累進(腳 1 + 腳 2),跨格各算各格第一動。"""
    c1 = _price(s, k1, p)
    if c1 > p.act_threshold:
        return False
    sl, pk = slot_of(k1), _pool(k1, p)
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


def new_state(b: Build, p: Params, stamina: Optional[int] = None) -> State:
    return State(b, p, stamina)


# 一次出手的紀錄(keep_events 時保存):
# (出手者, 武器, 擲骰成功, 致命, 特誤, 步法S, 全閃, T, T_after_dodge, D_招架, D_格擋, D_def, 傷害, 類別)
def _defend(d: Build, sd: State, T: int, p: Params, rng, log, d_ctx: Ctx, react: bool = False) -> dict:
    """守方防禦鏈:步法(若宣告)→ 閃避 → 招架 → 格擋。只有閃避侵蝕 T;招架、格擋做傷害對抗。
    react=True(對事件的反應):不用步法、不再生反擊(避免連鎖)。招架若發動副技能,每個 +1 點。"""
    dst = sd.st
    boost = {"dodge": 0, "parry": 0, "block": 0}
    out = dict(full=False, T_after=T, d_par=None, d_blk=None, D_def=0, par_margin=0,
               counter_on=False, acted=False)
    if not react and d.step_def is not None and _can_use(d, p, d.step_def):
        if _try_pair(sd, d, "step", d.step_def, p):
            Sd = step(d.value("step"), rng, params=p, log=log, ctx=d_ctx)
            dst.step_try += 1
            dst.step_ok += Sd > 0
            dst.step_sum += Sd
            boost[d.step_def] = Sd
        else:
            dst.step_skipped += 1
    if _try(sd, d, "dodge", p):
        out["acted"] = True
        dst.def_acts += 1
        T_after, full = dodge(T, d.value("dodge") + boost["dodge"], d.level("dodge"), rng,
                              params=p, log=log, ctx=replace_ctx(d_ctx, T))
        dst.dodge_try += 1
        out["T_after"], out["full"] = T_after, full
    if out["full"]:
        return out

    held = d.held_parry(p, react) if p.activate != "never" else []
    variants = [[("parry", k)] for k in range(len(held), -1, -1)] or [[("parry", 0)]]
    idx = _try_seq(sd, d, p, variants)
    if idx is not None:
        out["acted"] = True
        active = held[:len(held) - idx]
        dst.def_acts += 1
        dst.parry_try += 1
        dst.sub_active += len(active)
        pen_p = dual_penalty(p)[0] if d.offhand is not None else 0
        link = 10 * ((d.link_w2p if "link_w2p" in active else 0)
                     + (d.link_p2w if "link_p2w" in active else 0))
        d_par, margin = parry_ex(max(d.value("parry") - pen_p + boost["parry"] + link, 0),
                                 d.weapon_die, rng, params=p, log=log, ctx=d_ctx)
        dst.parry_ok += d_par is not None
        out["d_par"], out["par_margin"] = d_par, margin
        out["counter_on"] = "counter" in active
    if _can_use(d, p, "block") and _try(sd, d, "block", p):
        out["acted"] = True
        dst.def_acts += 1
        dst.block_try += 1
        out["d_blk"] = block(d.value("block") + boost["block"],
                             shield_value(shield_of(d, p), d.strength), rng, params=p, log=log, ctx=d_ctx)
        dst.block_ok += out["d_blk"] is not None
    parts = [x for x in (out["d_par"], out["d_blk"]) if x is not None]
    out["D_def"] = 0 if not parts else (sum(parts) if p.defense_combine == "sum" else max(parts))
    return out


def _react_event(kind: str, dmg: int, T: int, b: Build, s: State, d: Build, sd: State, p: Params,
                 rng, log, ctx: Ctx) -> int:
    """事件→被動反應。事件由對手(d)的動作生成,b 必須回應:付體力走防禦鏈,或不回應吃後果。
    目前只有 'counter'(反擊)一種,日後的阻斷 / 強彈劍 / interrupt 加 kind 即可。回傳 b 實際受傷。"""
    assert kind == "counter"
    st, dst = s.st, sd.st
    dst.ev_gen += 1
    taken = dmg
    reacted = False
    if p.react != "never":
        before = s.stamina + s.neili
        r = _defend(b, s, T, p, rng, log, Ctx(session=ctx.session, turn=ctx.turn, actor=b.name),
                    react=True)
        st.ev_react_cost += before - (s.stamina + s.neili)
        reacted = r["acted"]
        taken = 0 if r["full"] else max(0, dmg - r["D_def"])
    if reacted:
        st.ev_reacted += 1
    else:
        st.ev_ignored += 1
    s.take(taken)
    st.ev_dmg_taken += taken
    dst.ev_dmg_dealt += taken
    return taken


def _attack(i: int, key: str, b: Build, s: State, d: Build, sd: State, p: Params, rng,
            log, ctx: Ctx, lianci_bonus: int):
    """出手一次:先(選配)步法,再(選配)疊加與副技能,再武器擲骰;成功 → 守方走防禦鏈 → 傷判 → 事件。
    回傳 None(沒出手:被門檻擋或付不起)或紀錄 tuple。"""
    S = 0
    a_ctx = Ctx(session=ctx.session, turn=ctx.turn, actor=b.name)
    if b.step_atk and _try_pair(s, b, "step", key, p):
        S = step(b.value("step"), rng, params=p, log=log, ctx=a_ctx)
        s.st.step_try += 1
        s.st.step_ok += S > 0
        s.st.step_sum += S

    # 疊加(只疊主手武器)與副技能發動:依偏好排序的動作串,付得起的第一串生效
    stacks = list(b.stacks) if key == "weapon" else []
    held = b.held_atk() if (p.activate != "never" and key == "weapon") else []
    variants, meta = [], []
    for ns in range(len(stacks), -1, -1):
        pre = [] if p.stack_pay == "none" else [("stack:" + n, 0) for n, _ in stacks[:ns]]
        for k in range(len(held), -1, -1):
            variants.append(pre + [(key, k)])
            meta.append((ns, held[:k]))
    idx = _try_seq(s, b, p, variants)
    if idx is None:
        return None
    ns, active = meta[idx]
    act_crit, act_reroll = "crit_sub" in active, "reroll_any" in active
    stack_sum = sum(b.stack_value(n, lv) for n, lv in stacks[:ns])
    st = s.st
    st.atk_acts += 1
    st.att += 1
    st.sub_active += len(active)

    pen_main, pen_off = dual_penalty(p)
    pen = 0
    if b.offhand is not None:
        pen = pen_main if key == "weapon" else pen_off
    eff = max(b.value(key) - pen + S + lianci_bonus + stack_sum, 0)
    res = check(eff, rng, crit_bonus=b.crit_bonus + (b.crit_sub if act_crit else 0),
                reroll_any=act_reroll, params=p, log=log,
                ctx=Ctx(session=ctx.session, turn=ctx.turn, actor=b.name,
                        skill=b.weapon_name if key == "weapon" else NAME[key]))
    if res.fumble:
        st.fumble += 1
    if not res.success:                      # 失手(含 A21 攻方特誤)
        return (i, key, False, False, res.fumble, S, False, 0, 0, None, None, 0, 0, None)
    st.succ += 1
    st.crit_roll += res.crit
    T = res.margin

    # ---- 守方防禦鏈 ----
    sd.st.faced += 1
    r = _defend(d, sd, T, p, rng, log, Ctx(session=ctx.session, turn=ctx.turn, actor=d.name))
    if r["full"]:
        st.full_dodged += 1
        return (i, key, True, res.crit, False, S, True, T, 0, None, None, 0, 0, None)
    st.thru += 1
    d_par, d_blk, D_def = r["d_par"], r["d_blk"], r["D_def"]
    parts = [x for x in (d_par, d_blk) if x is not None]

    ov = overflow(eff)
    die = DIE["weapon"] if key == "weapon" else DIE[key]
    d_atk = None
    if p.damage_model == "s3":
        die = b.weapon_die if key == "weapon" else DIE[key]
        attr = b.weapon_attr if key == "weapon" else "Ski"
        dmg, d_atk = damage_s3(die, effect_value(b.attr(attr)), ov, res.crit, D_def, rng)
    else:
        dmg = damage(die, ov, res.crit, D_def, rng)
    cat = None
    if dmg > 0:
        st.dmg_hit += 1
        st.dmg_total += dmg
        cat = ("crit_maxdie" if (res.crit and ov > 0) else
               "through_parry" if parts else "no_guard")
        st.cat_n[cat] += 1
        st.cat_dmg[cat] += dmg
    sd.take(dmg)

    # ---- 事件:反擊(招架成功且 D_def > D_atk,差值彈向攻方)----
    if (r["counter_on"] and d_par is not None and d_atk is not None and sd.hp > 0
            and d_par > d_atk):
        _react_event("counter", d_par - d_atk, r["par_margin"], b, s, d, sd, p, rng, log, ctx)
    return (i, key, True, res.crit, False, S, False, T, r["T_after"], d_par, d_blk, D_def, dmg, cat)


def replace_ctx(c: Ctx, T: int) -> Ctx:
    return Ctx(session=c.session, turn=c.turn, actor=c.actor, challenge_value=T)


def _can_use(b: Build, p: Params, key: str) -> bool:
    if key == "block":
        return b.block is not None and shield_of(b, p) is not None
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
    kill: bool = False         # 敗方重傷位也 ≤ 0(擊殺);否則只是失能(輕傷位 0,戰鬥結束)


def _down(s: State, p: Params) -> bool:
    return s.hp <= 0 if p.end_on == "incap" else s.heavy <= 0


def fight(b0: Build, b1: Build, p: Params, rng, first: int = 0, max_rounds: int = 300,
          stamina: Optional[int] = None, log=None, session: int = 0,
          keep_events: bool = False) -> FightOut:
    builds = (b0, b1)
    states = (new_state(b0, p, stamina), new_state(b1, p, stamina))
    events = []
    order = (first, 1 - first)
    first_crit = first_crit_hit = None

    def done(w: int, rnd: int) -> FightOut:
        return FightOut(w, rnd, states, events, first_crit, first_crit_hit,
                        kill=states[1 - w].heavy <= 0)

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
                if _down(sd, p):                      # 輕傷位 0:失能,戰鬥結束('death' 模式:重傷位 0)
                    return done(i, rnd)
                if _down(s, p):                       # 被自己觸發的反擊事件打倒
                    return done(j, rnd)
        for s, b in zip(states, builds):
            s.reset_round(b)
            s.stamina = min(s.stamina_max, s.stamina + regen_of(b, p))
            s.neili = min(s.neili_max, s.neili + p.regen_neili)
        if (all(regen_of(b, p) <= 0 for b in builds) and p.regen_neili <= 0
                and states[0].avail <= 0 and states[1].avail <= 0):
            return FightOut(None, rnd, states, events, first_crit, first_crit_hit, True)
    return FightOut(None, max_rounds, states, events, first_crit, first_crit_hit)
