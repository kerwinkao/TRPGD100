"""Session 2 戰鬥迴圈(重寫)。這裡是「模擬假設」,不是規則:
先攻固定輪流、攻守策略(每動門檻)、步法的宣告時機、副技能的解讀,都寫在這裡並列入 REPORT / ambiguities。
解算一律呼叫 resolve.py。Session 1 版本見 fight_s1.py。

Session 3 加入(皆由 Params 開關 / Build 新欄位啟用,預設值維持 Session 2 行為):
- 傷勢三位:輕傷位 −D、重傷位 −⌊D/2⌋;輕傷位 0 失能(戰鬥結束),重傷位 0 擊殺。上限 Str+Con。
- 體力池 Con+Con、回復 = Con 效益值;傷害 = 四捨五入((骰 + 屬性補正) × (1 + 溢出/100)) − D_def。
- 疊加佔格付費:疊的技能走自己的格(腦格)、累進與低價次數,疊加值 = 被疊技能全值(A10)。
- 副技能發動:每個 +1 點,加在價碼上、不進累進格的 k;付不起就不發動。link(雙向 +10/級)、致命、任意重骰(劍意)。
- 事件→被動反應:動作生成對手必須回應的事件(第一個實例:反擊),對手付體力走防禦鏈,或不回應吃後果。

Session 4 加入(同樣由 Params 開關啟用,s4_params() 一次打開):
- 體力與發動費是浮點數(每個副技能 0.2 點,Params.sub_fee);比較時留 1e-9 容差。
- 每個副技能宣告燒哪個池:預設體力;任意重骰(劍意)燒神識(Params.reroll_pool),不佔格、不進價碼與門檻。
- 武器骰可為多骰((顆數, 面數),如 2d4);招架骰對骰用守方武器的骰型。
- 失能即勝:輕傷位 0 戰鬥結束(end_on='incap' 預設),不再需要打到死。
- 內力三種用途(攻擊灌注 / 防禦吸收 / 療傷):池量與等級上限由 Con/Cha/Wis 效益值決定,單次輸出上限、
  每回合輸出次數、疊發分發、三類各自冷卻一回合、戰鬥中不回。

Session 5 加入(Params.initiative='swap' / s5_params()):
- 主動權交換取代「雙方各一手番」:持主動者出手,守方任一防禦層成功(全閃 / 招架成功 / 格擋成功)就接手主動,
  攻擊落空也交出;全部失敗則攻方保有主動;持主動者付不起或不想攻就停手交出;雙方連續停手 = 回合結束。
- 守方可改為對攻(Params.counter_policy / Build.counter_policy):不防,雙方同時擲骰、同時結算傷害,雙方都不防。
- 量測:主動權交換次數(依成因)、連續出手長度、對攻發生率(FightOut.flow、Stats.ca_*)。

Session 6 加入(Params.initiative_roll='reroll'、info_model='s6' / s6_params()):
- 每回合重擲先攻:行動值 = d100 − 先攻屬性效益值 × 5 − 戰鬥反應等級 × 5,小者起手持主動(A80)。
- 預判:守方在攻方擲骰後、決定防禦前,付腦格一動 + 神識擲戰鬥反應,幅度 ≥ 預判難度才看得到該擊幅度與傷害;失敗照付。
- 無預判者(沒有戰鬥反應或預判失敗)在攻方擲骰前決定:防禦層一次付清(落空也白付)、是否對攻、內力防禦固定投入(灌滿 / 灌一半)。
  預判成功者看過結果才決定防禦層(全閃機率太低的閃避不付)與是否對攻,內力防禦保留舊的「付剛好吃掉」(A82)。

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

from resolve import (Ctx, Params, block, check, damage, damage_s3, dodge, dodge_ex, effect_value,
                     max_dice, overflow, parry, parry_ex, roll_dice, shield_value, step)

BASE = 42  # 均值角色(全 14)模擬技能的底值
EPS = 1e-9   # 浮點比較容差(發動費 0.2 的累加)
SLOT = {"weapon": "hand", "offhand": "hand", "parry": "hand", "block": "hand",
        "step": "foot", "dodge": "foot", "heal": "brain", "reaction": "brain"}
NAME = {"weapon": "長劍", "offhand": "短劍", "step": "步法", "dodge": "閃避",
        "parry": "招架", "block": "格擋", "neigong": "內功", "reaction": "戰鬥反應"}
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
    # ---- Session 4 ----
    weapon_dice: Optional[tuple] = None   # 主手武器骰 (顆數, 面數),如 (2, 4);None = (1, weapon_die)
    neili_tier: int = 1                # 內功持有難度階序號(散修 1;1 散修、2 內門、3 嫡傳、4 以上修真)
    neili_use: tuple = ()              # 內力用途優先序:'attack' | 'defense' | 'heal' 的子集;() = 不用內力
    neili_alloc: Optional[dict] = None # 每次輸出分給各用途的點數(分發),如 {"attack": 2, "defense": 1};None = 全給 neili_use 第一項
    # ---- Session 5 ----
    counter_policy: Optional[str] = None   # 守方對攻策略;None = 用 Params.counter_policy
    # ---- Session 6 ----
    reaction: int = 0                  # 戰鬥反應等級(腦格):先攻行動值 −5/級;≥ 1 級才有預判
    init_eff: Optional[int] = None     # 先攻屬性效益值;None = Agi 的效益值(均值 14 → 2)
    foresight: bool = True             # 有戰鬥反應時是否發動預判(False = 只吃先攻加值,拆解用)

    def init_value(self) -> int:
        """先攻屬性效益值(行動值 = d100 − 此值 × 5 − 戰鬥反應等級 × 5)。"""
        return self.init_eff if self.init_eff is not None else effect_value(self.attr("Agi"))

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

    def dice(self) -> tuple:
        return self.weapon_dice if self.weapon_dice else (1, self.weapon_die)

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


def shenshi_max(b: Build) -> int:
    """神識池 = Wis + Wis(attributes.md:依家,標準取 Wis+Wis;A53)。"""
    return 2 * b.attr("Wis")


def neili_level(b: Build, p: Params) -> int:
    """內力可用的內功等級:上限 = Con、Cha、Wis 效益值中最小者(任一為 0 修不出)。僅 neili_model='s4' 有效。"""
    if p.neili_model != "s4":
        return 0
    cap = min(effect_value(b.attr(a)) for a in ("Con", "Cha", "Wis"))
    return max(0, min(b.neigong, cap))


def neili_pool(b: Build, p: Params) -> int:
    """池量 = Con、Cha、Wis 效益值之和 × 內功難度階序號 × 內功等級。均值散修一級 6。"""
    lv = neili_level(b, p)
    return sum(effect_value(b.attr(a)) for a in ("Con", "Cha", "Wis")) * b.neili_tier * lv


def neili_cap(level: int) -> int:
    """單次輸出上限:內功 1/2/3/4 級 → 3/5/7/9 點。"""
    return 2 * level + 1 if level > 0 else 0


def sub_costs(names: list, p: Params) -> tuple:
    """發動這些副技能的費用:(體力費, 神識費)。每個副技能燒自己宣告的池:
    任意重骰在 reroll_pool='shenshi' 時燒神識,其餘燒體力(Params.sub_fee/個)。"""
    stam = shen = 0.0
    for n in names:
        if n == "reroll_any" and p.reroll_pool == "shenshi":
            shen += p.shenshi_fee
        else:
            stam += p.sub_fee
    return stam, shen


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
                 "ev_reacted", "ev_ignored", "ev_dmg_taken", "ev_react_cost",
                 "sub_offered", "shen_spent", "qi_atk_pts", "qi_atk_dmg", "qi_def_pts", "qi_def_abs",
                 "qi_heal_pts", "qi_heal_amt", "heal_acts",
                 "ca_n", "ca_decisions", "ca_unaffordable",
                 "fs_opp", "fs_try", "fs_ok", "blind_n", "blind_miss")

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
        self.p = p
        self.stamina = stamina
        self.stamina_max = stamina
        # 內力:s4 模型是獨立的三用途池(self.qi),不當體力替代品;舊模型沿用 A12(內功技能值,可代付體力)
        self.neili = (b.value("neigong") if (b.level("neigong") >= 1 and p.neili_model != "s4") else 0)
        self.neili_max = self.neili
        self.qi = neili_pool(b, p)
        self.qi_max = self.qi
        self.shen = shenshi_max(b)
        self.cd_now: set = set()
        self.slots = {"hand": 0, "foot": 0, "brain": 0}
        self.cheap: dict = {}
        self.rnd = 0
        self.st = Stats()
        self.reset_round(b)

    def take(self, d: int, light_abs: int = 0, heavy_abs: int = 0):
        """受傷 D:輕傷位 −D,重傷位 −⌊D/2⌋(各自可先扣吸收:內力防禦輕 +3、重 +1,Session 4)。
        輕傷位 ≤ 0 失能(戰鬥結束,Session 4 起即勝負);重傷位仍追蹤,只供舊統計。"""
        self.hp -= max(0, d - light_abs)
        self.heavy -= max(0, d // 2 - heavy_abs)

    def reset_round(self, b: Build):
        self.slots = {"hand": 0, "foot": 0, "brain": 0}
        self.cheap = {k: cheap_count(b.level(k)) for k in
                      ("weapon", "offhand", "step", "dodge", "parry", "block", "reaction")}
        for name, lv in b.stacks:
            self.cheap["stack:" + name] = cheap_count(lv)
        # 內力:冷卻 = 上回合用過的類別本回合不能用;本回合各用途的額度 = 每回合輸出次數 × 每次輸出分給該用途的點數
        lv = neili_level(b, self.p)
        self.cheap["heal"] = cheap_count(lv)
        self.cd_prev = self.cd_now
        self.cd_now = set()
        self.allow, self.unit = {}, {}
        if lv > 0 and self.qi > 0 and b.neili_use:
            n_out, cap = (lv + 1) // 2, neili_cap(lv)
            for i, cat in enumerate(b.neili_use):
                a = (b.neili_alloc or {}).get(cat, 0) if b.neili_alloc else (cap if i == 0 else 0)
                if a > 0 and cat not in self.cd_prev:
                    self.unit[cat] = min(a, cap)
                    self.allow[cat] = n_out * self.unit[cat]

    def qi_peek(self, cat: str) -> int:
        """這個用途這一動最多能取幾點內力(額度、單次輸出上限、剩餘池)。"""
        a = self.allow.get(cat, 0)
        per = a if self.p.neili_stack else min(a, self.unit.get(cat, 0))
        return int(min(per, self.qi))

    def qi_take(self, cat: str, want: int) -> int:
        """取至多 want 點內力給 cat;用了就記冷卻。回傳實際點數。"""
        pts = min(want, self.qi_peek(cat))
        if pts <= 0:
            return 0
        self.allow[cat] -= pts
        self.qi -= pts
        self.cd_now.add(cat)
        return pts

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
    if c > p.act_threshold + EPS:
        _refuse(s)
        return False
    if c > s.avail + EPS:
        _wind(s)
        return False
    _commit(s, b, key, p, c)
    if extra:
        s.st.sub_fee += extra
    return True


def _plan(s: State, p: Params, actions: list, shen: float = 0.0) -> tuple:
    """不改狀態地模擬一串動作((鍵, 發動費)...)的總價:每動(價碼 + 發動費)都要 ≤ 門檻,體力總和要付得起;
    shen = 這一串另燒的神識(不佔格、不進價碼與門檻),池不夠就付不起。
    回傳 (失敗原因 None|'refuse'|'wind', 各動價碼)。同格的動作累進、技能低價次數依序消耗。"""
    slots, cheap, total, costs = dict(s.slots), dict(s.cheap), 0.0, []
    for key, extra in actions:
        pk, sl = _pool(key, p), slot_of(key)
        if cheap.get(pk, 0) > 0:
            price = 1
            cheap[pk] -= 1
        else:
            price = slots[sl] + 1
        slots[sl] += 1
        c = price + extra
        if c > p.act_threshold + EPS:
            return "refuse", costs
        total += c
        costs.append(c)
    if total > s.avail + EPS or shen > s.shen + EPS:
        return "wind", costs
    return None, costs


def _try_seq(s: State, b: Build, p: Params, variants: list) -> Optional[int]:
    """依偏好順序試幾種「動作串」(如:疊兩個再攻並發動副技能 → 疊兩個再攻不發動 → ...)。
    variant 可為動作 list,或 (動作 list, 神識費) tuple。
    回傳第一個付得起的 variant 索引並付費;全失敗回 None,只按最後一個(最便宜)的失敗原因記一次拒絕 / 喘。"""
    why = None
    for idx, v in enumerate(variants):
        actions, shen = v if isinstance(v, tuple) else (v, 0.0)
        why, costs = _plan(s, p, actions, shen)
        if why is None:
            for (key, extra), c in zip(actions, costs):
                _commit(s, b, key, p, c)
                if extra:
                    s.st.sub_fee += extra
                if key.startswith("stack:"):
                    s.st.stack_acts += 1
            if shen:
                s.shen -= shen
                s.st.shen_spent += shen
            return idx
    (_refuse if why == "refuse" else _wind)(s)
    return None


def _try_pair(s: State, b: Build, k1: str, k2: str, p: Params) -> bool:
    """步法 + 目標動作:兩動都在門檻內、合計付得起,才先付第一動(第二動由呼叫端接著 _try)。
    同格輔助累進(腳 1 + 腳 2),跨格各算各格第一動。"""
    c1 = _price(s, k1, p)
    if c1 > p.act_threshold + EPS:
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
    if c2 > p.act_threshold + EPS or c1 + c2 > s.avail + EPS:
        return False
    _commit(s, b, k1, p, c1)
    return True


def new_state(b: Build, p: Params, stamina: Optional[int] = None) -> State:
    return State(b, p, stamina)


# 一次出手的紀錄(keep_events 時保存):
# (出手者, 武器, 擲骰成功, 致命, 特誤, 步法S, 全閃, T, T_after_dodge, D_招架, D_格擋, D_def, 傷害, 類別)
def _declare_defense(d: Build, sd: State, p: Params) -> dict:
    """盲防(s6 無預判者):攻方擲骰前把整條防禦鏈(步法 → 閃避 → 招架 → 格擋)一次付清,不管之後落空或被全閃。
    付費順序與 _defend 的逐層付費相同,所以價碼一樣;回傳各層是否付到,供 _defend(plan=...) 取用。"""
    plan = dict(step=None, dodge=False, parry=None, block=False)
    if d.step_def is not None and _can_use(d, p, d.step_def):
        plan["step"] = _try_pair(sd, d, "step", d.step_def, p)
    plan["dodge"] = _try(sd, d, "dodge", p)
    held = d.held_parry(p) if p.activate != "never" else []
    variants = []
    for k in range(len(held), -1, -1):
        stam, shen = sub_costs(held[:k], p)
        variants.append(([("parry", stam)], shen))
    idx = _try_seq(sd, d, p, variants)
    plan["parry"] = None if idx is None else held[:len(held) - idx]
    plan["block"] = bool(_can_use(d, p, "block") and _try(sd, d, "block", p))
    return plan


def _defend(d: Build, sd: State, T: int, p: Params, rng, log, d_ctx: Ctx, react: bool = False,
            plan: Optional[dict] = None, no_dodge: bool = False) -> dict:
    """守方防禦鏈:步法(若宣告)→ 閃避 → 招架 → 格擋。只有閃避侵蝕 T;招架、格擋做傷害對抗。
    react=True(對事件的反應):不用步法、不再生反擊(避免連鎖)。招架若發動副技能,每個 +1 點。
    plan(s6 盲防):各層已由 _declare_defense 預先付費,這裡只擲骰;no_dodge(s6 預判者):不付閃避,連帶不用助閃避的步法."""
    dst = sd.st
    boost = {"dodge": 0, "parry": 0, "block": 0}
    out = dict(full=False, T_after=T, d_par=None, d_blk=None, D_def=0, par_margin=0,
               counter_on=False, acted=False, dodge_ok=False)
    step_paid = None
    if not react and d.step_def is not None and _can_use(d, p, d.step_def) \
            and not (no_dodge and d.step_def == "dodge"):
        step_paid = _try_pair(sd, d, "step", d.step_def, p) if plan is None else plan["step"]
    if step_paid:
        Sd = step(d.value("step"), rng, params=p, log=log, ctx=d_ctx)
        dst.step_try += 1
        dst.step_ok += Sd > 0
        dst.step_sum += Sd
        boost[d.step_def] = Sd
    elif step_paid is False:
        dst.step_skipped += 1
    if (not no_dodge) and (_try(sd, d, "dodge", p) if plan is None else plan["dodge"]):
        out["acted"] = True
        dst.def_acts += 1
        T_after, full, ok = dodge_ex(T, d.value("dodge") + boost["dodge"], d.level("dodge"), rng,
                                     params=p, log=log, ctx=replace_ctx(d_ctx, T))
        dst.dodge_try += 1
        out["T_after"], out["full"], out["dodge_ok"] = T_after, full, ok
    if out["full"]:
        return out

    held = d.held_parry(p, react) if p.activate != "never" else []
    if plan is None:
        variants = []
        for k in range(len(held), -1, -1):
            stam, shen = sub_costs(held[:k], p)
            variants.append(([("parry", stam)], shen))
        idx = _try_seq(sd, d, p, variants)
        active = None if idx is None else held[:len(held) - idx]
    else:
        active = plan["parry"]
    if active is not None:
        out["acted"] = True
        dst.def_acts += 1
        dst.parry_try += 1
        dst.sub_active += len(active)
        dst.sub_offered += len(held)
        pen_p = dual_penalty(p)[0] if d.offhand is not None else 0
        link = p.link_bonus * ((d.link_w2p if "link_w2p" in active else 0)
                               + (d.link_p2w if "link_p2w" in active else 0))
        d_par, margin = parry_ex(max(d.value("parry") - pen_p + boost["parry"] + link, 0),
                                 d.dice(), rng, params=p, log=log, ctx=d_ctx)
        dst.parry_ok += d_par is not None
        out["d_par"], out["par_margin"] = d_par, margin
        out["counter_on"] = "counter" in active
    if (_can_use(d, p, "block") and _try(sd, d, "block", p)) if plan is None else plan["block"]:
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


def _pay_attack(i: int, key: str, b: Build, s: State, p: Params, rng, log, ctx: Ctx,
                lianci_bonus: int):
    """出手宣告並付費:(選配)步法 → 疊加與副技能發動(依偏好排序的動作串,付得起的第一串生效)→ 武器那一動。
    回傳 None(被門檻擋或付不起)或宣告資料 dict(尚未擲攻擊骰,供守方選擇防或對攻)。"""
    S = 0
    a_ctx = Ctx(session=ctx.session, turn=ctx.turn, actor=b.name)
    if b.step_atk and _try_pair(s, b, "step", key, p):
        S = step(b.value("step"), rng, params=p, log=log, ctx=a_ctx)
        s.st.step_try += 1
        s.st.step_ok += S > 0
        s.st.step_sum += S

    stacks = list(b.stacks) if key == "weapon" else []
    held = b.held_atk() if (p.activate != "never" and key == "weapon") else []
    variants, meta = [], []
    for ns in range(len(stacks), -1, -1):
        pre = [] if p.stack_pay == "none" else [("stack:" + n, 0) for n, _ in stacks[:ns]]
        for k in range(len(held), -1, -1):
            stam, shen = sub_costs(held[:k], p)
            variants.append((pre + [(key, stam)], shen))
            meta.append((ns, held[:k]))
    idx = _try_seq(s, b, p, variants)
    if idx is None:
        return None
    ns, active = meta[idx]
    st = s.st
    st.atk_acts += 1
    st.att += 1
    st.sub_active += len(active)
    st.sub_offered += len(held)
    return dict(i=i, key=key, S=S, active=active, lianci=lianci_bonus,
                stack_sum=sum(b.stack_value(n, lv) for n, lv in stacks[:ns]))


def _roll_attack(pay: dict, b: Build, s: State, p: Params, rng, log, ctx: Ctx):
    """擲攻擊骰。回傳 (結果, 有效技能值)。"""
    key, active = pay["key"], pay["active"]
    pen_main, pen_off = dual_penalty(p)
    pen = 0
    if b.offhand is not None:
        pen = pen_main if key == "weapon" else pen_off
    eff = max(b.value(key) - pen + pay["S"] + pay["lianci"] + pay["stack_sum"], 0)
    res = check(eff, rng, crit_bonus=b.crit_bonus + (b.crit_sub if "crit_sub" in active else 0),
                reroll_any="reroll_any" in active, params=p, log=log,
                ctx=Ctx(session=ctx.session, turn=ctx.turn, actor=b.name,
                        skill=b.weapon_name if key == "weapon" else NAME[key]))
    st = s.st
    if res.fumble:
        st.fumble += 1
    if res.success:
        st.succ += 1
        st.crit_roll += res.crit
    return res, eff


def _roll_damage(pay: dict, res, eff: int, b: Build, s: State, p: Params, rng) -> dict:
    """先擲出這一擊(未計防禦)的傷害:內力攻擊灌注的骰與武器骰。預判成功者要在防禦前看到它;
    內力這時只窺看額度,真正扣池與冷卻留到 _deal(傷害過了防禦鏈才灌)。"""
    key = pay["key"]
    die = b.dice() if key == "weapon" else DIE[key]
    attr = b.weapon_attr if key == "weapon" else "Ski"
    pts = s.qi_peek("attack") if "attack" in s.allow else 0
    extra = sum(rng.randint(1, 3) for _ in range(pts)) if pts else 0
    _, d_atk = damage_s3(die, effect_value(b.attr(attr)), overflow(eff), res.crit, 0, rng, extra=extra)
    return dict(d_atk=d_atk, pts=pts, extra=extra)


def _deal(pay: dict, res, eff: int, b: Build, s: State, d: Build, sd: State, p: Params, rng,
          D_def: int, parts: list, opposed: bool = True, pre: Optional[dict] = None,
          qi_pts: Optional[int] = None) -> tuple:
    """傷判並套用到守方。opposed=False(對攻):守方不防,也不用內力防禦吸收。
    pre:_roll_damage 先擲好的結果(預判);qi_pts:盲防者攻方擲骰前已投入的防禦內力點數(不為 None 就不付「剛好」的量)。
    回傳 (實際傷害, D_atk 或 None, 類別)。"""
    key, st = pay["key"], s.st
    ov = overflow(eff)
    die = DIE["weapon"] if key == "weapon" else DIE[key]
    d_atk = None
    absorbed = 0
    if p.damage_model == "s3":
        die = b.dice() if key == "weapon" else DIE[key]
        attr = b.weapon_attr if key == "weapon" else "Ski"
        # 內力攻擊灌注:傷害過了防禦鏈才灌,每點 +1d3(乘數之後才加);點數記在攻方
        if pre is None:
            extra = 0
            pts = s.qi_take("attack", s.qi_peek("attack")) if "attack" in s.allow else 0
            if pts:
                extra = sum(rng.randint(1, 3) for _ in range(pts))
                st.qi_atk_pts += pts
                st.qi_atk_dmg += extra
            dmg, d_atk = damage_s3(die, effect_value(b.attr(attr)), ov, res.crit, D_def, rng, extra=extra)
        else:
            d_atk = pre["d_atk"]
            dmg = max(0, d_atk - D_def)
            if pre["pts"]:
                s.qi_take("attack", pre["pts"])
                st.qi_atk_pts += pre["pts"]
                st.qi_atk_dmg += pre["extra"]
        # 內力防禦吸收(每點輕傷吸收 3、重傷吸收 1):
        #   legacy / 預判成功者:被打中才付,點數取到剛好吃掉傷害;盲防者:攻方擲骰前已投入 qi_pts,吃得掉多少算多少
        if opposed and qi_pts is None and dmg > 0 and "defense" in sd.allow:
            need = -(-dmg // 3)
            dpts = sd.qi_take("defense", need)
            if dpts:
                absorbed = dpts
                sd.st.qi_def_pts += dpts
                sd.st.qi_def_abs += min(dmg, 3 * dpts)
        elif opposed and qi_pts and dmg > 0:
            absorbed = qi_pts
            sd.st.qi_def_abs += min(dmg, 3 * qi_pts)
    else:
        dmg = damage(die, ov, res.crit, D_def, rng)
    light_abs = 3 * absorbed
    dealt = max(0, dmg - light_abs)
    cat = None
    if dealt > 0:
        st.dmg_hit += 1
        st.dmg_total += dealt
        cat = ("crit_maxdie" if (res.crit and ov > 0) else
               "through_parry" if parts else "no_guard")
        st.cat_n[cat] += 1
        st.cat_dmg[cat] += dealt
    sd.take(dmg, light_abs=light_abs, heavy_abs=absorbed)
    return dealt, d_atk, cat


def _attack(i: int, key: str, b: Build, s: State, d: Build, sd: State, p: Params, rng,
            log, ctx: Ctx, lianci_bonus: int, info: Optional[dict] = None):
    """出手一次(守方走防禦鏈):付費 → 擲骰 → 成功則守方防禦鏈 → 傷判 → 事件。
    回傳 None(沒出手:被門檻擋或付不起)或紀錄 tuple。info(選填)回填 hit / defended,
    供主動權交換判斷:defended = 守方任一防禦層成功(全閃、招架成功、格擋成功;dodge_swap='any' 時閃避擲骰成功也算)。"""
    pay = _pay_attack(i, key, b, s, p, rng, log, ctx, lianci_bonus)
    if pay is None:
        return None
    return _attack_resolve(pay, b, s, d, sd, p, rng, log, ctx, info)


def _attack_resolve(pay: dict, b: Build, s: State, d: Build, sd: State, p: Params, rng, log,
                    ctx: Ctx, info: Optional[dict] = None, rolled: Optional[tuple] = None,
                    plan: Optional[dict] = None, qi_pts: Optional[int] = None,
                    pre: Optional[dict] = None, no_dodge: bool = False):
    """rolled:攻擊骰已擲好 (res, eff)(預判流程);plan / qi_pts:盲防者預先付好的防禦鏈與內力;
    pre / no_dodge:預判成功者先看到的傷害與「不付閃避」的決定。"""
    i, key, S = pay["i"], pay["key"], pay["S"]
    st = s.st
    res, eff = rolled if rolled is not None else _roll_attack(pay, b, s, p, rng, log, ctx)
    if info is not None:
        info.update(hit=res.success, defended=False)
    if plan is not None and not res.success:
        sd.st.blind_miss += 1                # 盲防者白付的一條防禦鏈
    if not res.success:                      # 失手(含 A21 攻方特誤)
        return (i, key, False, False, res.fumble, S, False, 0, 0, None, None, 0, 0, None)
    T = res.margin

    # ---- 守方防禦鏈 ----
    sd.st.faced += 1
    r = _defend(d, sd, T, p, rng, log, Ctx(session=ctx.session, turn=ctx.turn, actor=d.name),
                plan=plan, no_dodge=no_dodge)
    d_par, d_blk, D_def = r["d_par"], r["d_blk"], r["D_def"]
    if info is not None:
        info["defended"] = bool(r["full"] or d_par is not None or d_blk is not None
                                or (p.dodge_swap == "any" and r["dodge_ok"]))
    if r["full"]:
        st.full_dodged += 1
        return (i, key, True, res.crit, False, S, True, T, 0, None, None, 0, 0, None)
    st.thru += 1
    parts = [x for x in (d_par, d_blk) if x is not None]
    dmg, d_atk, cat = _deal(pay, res, eff, b, s, d, sd, p, rng, D_def, parts, pre=pre, qi_pts=qi_pts)

    # ---- 事件:反擊(招架成功且 D_def > D_atk,差值彈向攻方)----
    if (r["counter_on"] and d_par is not None and d_atk is not None and sd.hp > 0
            and d_par > d_atk):
        _react_event("counter", d_par - d_atk, r["par_margin"], b, s, d, sd, p, rng, log, ctx)
    return (i, key, True, res.crit, False, S, False, T, r["T_after"], d_par, d_blk, D_def, dmg, cat)


def _counter_attack_wanted(d: Build, sd: State, s: State, p: Params) -> bool:
    """守方被攻擊時,依 counter_policy 決定要不要對攻(只有主動權交換模式才有這個選項)。"""
    if p.initiative != "swap":
        return False
    pol = d.counter_policy or p.counter_policy
    if pol == "always":
        return True
    if pol == "lead":
        return sd.hp > s.hp                   # 輕傷位絕對值領先攻方(A69)
    return False


def _counter_resolve(pay: dict, pay_d: dict, b: Build, s: State, d: Build, sd: State, p: Params, rng, log,
                     ctx: Ctx, info: dict, rolled: Optional[tuple] = None, pre: Optional[dict] = None):
    """對攻結算:雙方各擲骰、各自傷判(誰都不防),同時套用傷害。rolled / pre = 攻方骰與傷害已先擲(預判流程)。"""
    sd.st.ca_n += 1
    info["counter"] = True
    res_a, eff_a = rolled if rolled is not None else _roll_attack(pay, b, s, p, rng, log, ctx)
    res_d, eff_d = _roll_attack(pay_d, d, sd, p, rng, log, ctx)
    recs = []
    for (pay_x, res_x, eff_x, bx, sx, dx, sdx, pre_x) in ((pay, res_a, eff_a, b, s, d, sd, pre),
                                                          (pay_d, res_d, eff_d, d, sd, b, s, None)):
        dmg_x, cat_x = 0, None
        if res_x.success:
            dmg_x, _da, cat_x = _deal(pay_x, res_x, eff_x, bx, sx, dx, sdx, p, rng, 0, [], opposed=False, pre=pre_x)
        recs.append((pay_x["i"], pay_x["key"], res_x.success, res_x.crit, res_x.fumble, pay_x["S"],
                     False, res_x.margin if res_x.success else 0, 0, None, None, 0, dmg_x, cat_x))
    info.update(hit=res_a.success, defended=False, rec_d=recs[1])
    return recs[0]


def _foresight(d: Build, sd: State, p: Params, rng, log, ctx: Ctx) -> Optional[bool]:
    """預判:腦格一動(累進、低價次數、受門檻約束)+ 神識,擲戰鬥反應,幅度 ≥ 預判難度才看得到。
    回傳 None(沒有戰鬥反應 / 付不起,沒發動)、True(看到)、False(發動了但沒看到,照付)。"""
    if p.info_model != "s6" or not d.foresight or d.level("reaction") < 1:
        return None
    sd.st.fs_opp += 1
    if _try_seq(sd, d, p, [([("reaction", 0)], p.foresight_shen)]) is None:
        return None
    sd.st.fs_try += 1
    res = check(d.value("reaction"), rng, params=p, log=log,
                ctx=Ctx(session=ctx.session, turn=ctx.turn, actor=d.name, skill=NAME["reaction"]))
    ok = res.success and res.margin >= p.foresight_dc
    sd.st.fs_ok += ok
    return ok


def _blind_qi(sd: State, p: Params) -> int:
    """盲防者在攻方擲骰前投入的防禦內力:每次灌滿(本次可取的額度)或灌一半(單次輸出上限的一半)。落空也白投。"""
    if "defense" not in sd.allow:
        return 0
    peek = sd.qi_peek("defense")
    want = peek if p.blind_qi == "full" else min(peek, -(-sd.unit.get("defense", 0) // 2))
    pts = sd.qi_take("defense", want)
    sd.st.qi_def_pts += pts
    return pts


def _exchange_s6(i: int, key: str, b: Build, s: State, d: Build, sd: State, p: Params, rng, log,
                 ctx: Ctx, bonus: int, info: dict):
    """s6 的一次出手:攻方宣告付費 →(有戰鬥反應者:攻方擲骰、守方預判)→ 守方選對攻或防禦。
    預判成功:看到命中幅度與傷害,據此決定對攻(致命的一擊不對攻)、閃避付不付、內力付剛好。
    其餘(沒有預判、失敗、付不起):在不知道結果下決定——對攻照 counter_policy,防禦鏈一次付清,內力固定投入。"""
    pay = _pay_attack(i, key, b, s, p, rng, log, ctx, bonus)
    if pay is None:
        return None
    sd.st.ca_decisions += 1
    info.update(counter=False, rec_d=None)
    rolled = pre = None
    fs = None
    if d.foresight and d.level("reaction") >= 1:
        rolled = _roll_attack(pay, b, s, p, rng, log, ctx)
        fs = _foresight(d, sd, p, rng, log, ctx)
        if fs and rolled[0].success:
            pre = _roll_damage(pay, rolled[0], rolled[1], b, s, p, rng)
    want = _counter_attack_wanted(d, sd, s, p)
    if want and pre is not None and pre["d_atk"] >= sd.hp:
        want = False                                   # 預判看到這一擊會致命 → 不賭對攻
    if want:
        pay_d = _pay_attack(1 - i, "weapon", d, sd, p, rng, log, ctx, 0)
        if pay_d is not None:
            return _counter_resolve(pay, pay_d, b, s, d, sd, p, rng, log, ctx, info, rolled, pre)
        sd.st.ca_unaffordable += 1
    if fs:                                             # 預判成功:逐層看情況付
        no_dodge = False
        if rolled[0].success:
            full_chance = (d.value("dodge") - rolled[0].margin) / 100
            no_dodge = full_chance < p.fs_dodge_min
        return _attack_resolve(pay, b, s, d, sd, p, rng, log, ctx, info, rolled=rolled, pre=pre, no_dodge=no_dodge)
    plan = _declare_defense(d, sd, p)                  # 盲防:先付防禦鏈與內力,再看攻方的骰
    qi_pts = _blind_qi(sd, p)
    sd.st.blind_n += 1
    return _attack_resolve(pay, b, s, d, sd, p, rng, log, ctx, info, rolled=rolled, plan=plan, qi_pts=qi_pts)


def _exchange(i: int, key: str, b: Build, s: State, d: Build, sd: State, p: Params, rng, log,
              ctx: Ctx, bonus: int, info: dict):
    """主動權交換下的一次出手:攻方宣告付費 → 守方選防或對攻 → 擲骰與結算。
    對攻:守方也付一動(累進)出手,雙方各擲骰、各自傷判(誰都不防),同時套用傷害。
    守方想對攻但付不起 → 退回走防禦鏈。回傳攻方紀錄 tuple(或 None),info 回填 hit / defended / counter / rec_d。
    info_model='s6' 轉給 _exchange_s6(預判 / 盲防)。"""
    if p.info_model == "s6":
        return _exchange_s6(i, key, b, s, d, sd, p, rng, log, ctx, bonus, info)
    pay = _pay_attack(i, key, b, s, p, rng, log, ctx, bonus)
    if pay is None:
        return None
    sd.st.ca_decisions += 1
    info.update(counter=False, rec_d=None)
    if _counter_attack_wanted(d, sd, s, p):
        pay_d = _pay_attack(1 - i, "weapon", d, sd, p, rng, log, ctx, 0)
        if pay_d is not None:
            return _counter_resolve(pay, pay_d, b, s, d, sd, p, rng, log, ctx, info)
        sd.st.ca_unaffordable += 1
    return _attack_resolve(pay, b, s, d, sd, p, rng, log, ctx, info)


def _heal(b: Build, s: State, p: Params, rng):
    """內力療傷:手番開頭,輕傷位有缺且療傷額度還在,就花腦格一動(走價碼、門檻、低價次數)灌內力,
    每點回 1d4 輕傷位(上限內)。點數取到「期望回復剛好補滿缺口」(⌈缺口/2.5⌉)為止,不超過單次輸出上限。
    一動只回一次輸出;額度夠就再來一動。"""
    if p.neili_model != "s4" or "heal" not in s.allow:
        return
    while s.hp < s.hp_cap and s.qi > 0:
        want = min(-(-(s.hp_cap - s.hp) * 2 // 5), s.qi_peek("heal"))
        if want <= 0 or not _try(s, b, "heal", p):
            return
        pts = s.qi_take("heal", want)
        amt = sum(rng.randint(1, 4) for _ in range(pts))
        s.hp = min(s.hp_cap, s.hp + amt)
        s.st.heal_acts += 1
        s.st.qi_heal_pts += pts
        s.st.qi_heal_amt += amt


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
    flow: Optional[dict] = None   # Session 5 主動權交換量測(只在 initiative='swap' 填):見 _new_flow
    double_ko: bool = False    # 對攻同時結算,雙方同時失能(判平手,A70)


def _new_flow() -> dict:
    """主動權交換量測。swap_* = 主動權易手次數(依成因);hold_* = 「持主動期間」(從接手到交出)的連續出手長度;
    exchanges = 出手總次數(含被守方改成對攻的);counter_attacks = 守方選對攻的次數。"""
    return dict(swap_defend=0, swap_miss=0, swap_stop=0, hold_n=0, hold_len_sum=0, hold_empty=0,
                exchanges=0, counter_attacks=0, counter_unaffordable=0,
                starters=[], round_dmg=[])   # Session 6:每回合起手者、每回合雙方輕傷位損失


def action_value(b: Build, rng) -> int:
    """先攻行動值 = d100 − 先攻屬性效益值 × 5 − 戰鬥反應等級 × 5,越小越快(resolution「先攻」)。負值不處理。"""
    return rng.randint(1, 100) - 5 * b.init_value() - 5 * b.level("reaction")


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

    flow = _new_flow() if p.initiative == "swap" else None
    hp0 = [s.hp for s in states]

    def finish(w, rnd, double=False, stalled=False) -> FightOut:
        out = (done(w, rnd) if w is not None else
               FightOut(None, rnd, states, events, first_crit, first_crit_hit, stalled))
        out.flow = flow
        out.double_ko = double
        if flow is not None:
            if len(flow["round_dmg"]) < len(flow["starters"]):      # 戰鬥在回合中途結束
                flow["round_dmg"].append(tuple(h - s.hp for h, s in zip(hp0, states)))
            flow["counter_attacks"] = sum(s.st.ca_n for s in states)
            flow["counter_unaffordable"] = sum(s.st.ca_unaffordable for s in states)
        return out

    for rnd in range(1, max_rounds + 1):
        for s in states:
            s.rnd = rnd
        if p.initiative == "swap":
            ctx = Ctx(session=session, turn=rnd)
            starter = first                           # fixed:每場先手每回合起手(A73)
            if p.initiative_roll == "reroll":         # 每回合重擲行動值,小者起手;同值由本場先手(逐場交替)(A80)
                v0, v1 = action_value(b0, rng), action_value(b1, rng)
                starter = first if v0 == v1 else (0 if v0 < v1 else 1)
            flow["starters"].append(starter)
            hp0[:] = [s.hp for s in states]
            for i in (starter, 1 - starter):          # 內力療傷:回合開頭,兩邊各一次機會(A71)
                _heal(builds[i], states[i], p, rng)
            holder, stops, k, prev_ok, hold_len = starter, 0, 0, False, 0
            for _ in range(400):                      # 一回合內的出手次數上限(實際由消耗截止)
                i, j = holder, 1 - holder
                b, s, d, sd = builds[i], states[i], builds[j], states[j]
                key = "offhand" if (b.offhand is not None and k % 2 == 1) else "weapon"
                bonus = 10 if (b.lianci and prev_ok and k > 0) else 0
                info: dict = {}
                rec = _exchange(i, key, b, s, d, sd, p, rng, log, ctx, bonus, info)
                if rec is None:                       # 持主動者停手 → 交出主動;雙方連續停手 = 回合結束
                    flow["hold_n" if hold_len else "hold_empty"] += 1
                    flow["hold_len_sum"] += hold_len
                    stops += 1
                    if stops >= 2:
                        break
                    flow["swap_stop"] += 1
                    holder, k, prev_ok, hold_len = j, 0, False, 0
                    continue
                stops = 0
                k += 1
                hold_len += 1
                prev_ok = rec[2]
                flow["exchanges"] += 1
                for r_ in (rec, info.get("rec_d")):
                    if r_ is None:
                        continue
                    if first_crit is None and r_[3]:
                        first_crit = r_[0]
                    if first_crit_hit is None and r_[3] and r_[12] > 0:
                        first_crit_hit = r_[0]
                    if keep_events:
                        events.append(r_)
                a_down, d_down = _down(s, p), _down(sd, p)
                if a_down and d_down:
                    return finish(None, rnd, double=True)
                if d_down:
                    return finish(i, rnd)
                if a_down:
                    return finish(j, rnd)
                # 主動權:攻擊落空 → 交出;守方任一防禦層成功 → 交出;否則攻方保有
                why = None
                if info.get("counter"):
                    if not info["hit"] and p.counter_initiative == "by_hit":
                        why = "swap_miss"
                elif not info["hit"]:
                    why = "swap_miss"
                elif info["defended"]:
                    why = "swap_defend"
                if why:
                    flow[why] += 1
                    flow["hold_n"] += 1
                    flow["hold_len_sum"] += hold_len
                    holder, k, prev_ok, hold_len = j, 0, False, 0
        else:
            for i in order:
                j = 1 - i
                b, s, d, sd = builds[i], states[i], builds[j], states[j]
                ctx = Ctx(session=session, turn=rnd)
                _heal(b, s, p, rng)                       # 內力療傷(手番開頭)
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
        if flow is not None:
            flow["round_dmg"].append(tuple(h - s.hp for h, s in zip(hp0, states)))
        for s, b in zip(states, builds):
            s.reset_round(b)
            s.stamina = min(s.stamina_max, s.stamina + regen_of(b, p))
            s.neili = min(s.neili_max, s.neili + p.regen_neili)
        if (all(regen_of(b, p) <= 0 for b in builds) and p.regen_neili <= 0
                and states[0].avail <= 0 and states[1].avail <= 0):
            return finish(None, rnd, stalled=True)
    return finish(None, max_rounds)
