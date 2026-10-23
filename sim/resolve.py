"""解算器:一條數線上的判定、侵蝕鏈、難度、傷判。

規則來源:design/resolution.md、sim/spec.md。
- 純函數:不讀寫全域狀態。隨機源 `rng` 由呼叫端注入(任何有 randint(a, b) 的物件,
  例如 random.Random(seed))。使用紀錄寫進呼叫端注入的 `log`。
- 規則沒講清楚的地方一律做成 Params 的開關,預設值不是設計決定,
  對應條目見 design/ambiguities.md(以 A# 標註)。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Protocol, Sequence


class RNG(Protocol):
    def randint(self, a: int, b: int) -> int: ...


# ---------------------------------------------------------------------------
# 參數
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Params:
    # spec.md 列出的參數
    cost_stack: int = 5                  # 疊一個技能扣池幾點
    cost_layer: int = 5                  # 一層防禦扣池幾點
    cost_interrupt: int = 5              # interrupt 扣神識幾點(本輪未用)
    regen_stamina: int = 0               # 體力每回合回復(A13)
    regen_neili: int = 0                 # 內力每回合回復(A13)
    xp_reward_f: str = "linear"          # 'linear' | 'sqrt' | 'capped'
    free_xp_ratio: float = 0.1
    tier_cost: Optional[dict] = None     # 難度階 → 每級經驗(待定)
    tier_slots: Optional[dict] = None    # 難度階 → 每級副技能槽(待定)
    convert_cap: int = 10                # 換池每級上限

    # 以下為規則未定處的開關(見 design/ambiguities.md)
    crit_rounding: str = "ceil"          # A0:'ceil'(spec 公式)| 'half_up'(符合 52 → 05 範例)
    fumble_beats_overflow: bool = True   # A1:技能 ≥ 100 擲 00 時,特誤是否蓋過「每擲皆成功」
    crit_requires_success: bool = True   # A2:致命區超出技能值時,失敗的低擲算不算致命
    full_defense: str = "raw"            # A4:'raw' 依偽碼 m ≥ T;'capped' 用 min(m, 上限) ≥ T
    defense_fumble: str = "layer_fail"   # A5:防禦層擲 00:'layer_fail' 該層失效 | 'chain_break' 整鏈崩
    declare: str = "adaptive"            # A6:'adaptive' 看剩餘 T 逐層 | 'upfront' 先宣告整鏈並先付費
    free_first_layer: bool = False       # A7:第一層防禦是否免費(只有「疊」的層才付費)
    overflow_damage: str = "pct"         # A8:'pct' 傷害 × (1 + 溢出/100) | 'flat' 傷害 + 溢出
    stack_mode: str = "level"            # A10:'level' 疊 +10×被疊技能等級 | 'full' 疊 + 被疊技能值 | 'none'
    stack_cap: str = "none"              # E3 對策:'none' | 'by_level' 疊加總額 ≤ 10 × 主技能等級
    stack_cost_growth: str = "flat"      # E3 對策:'flat' | 'steep' 同一動作第 k 個疊加付 cost_stack × 3^(k−1)
    hp: int = 30                         # A11:生命值公式待定,先用常數
    weapon_die: int = 8                  # 長劍 1d8


DEFAULT = Params()


# ---------------------------------------------------------------------------
# 使用紀錄
# ---------------------------------------------------------------------------

LOG_FIELDS = ("session", "turn", "actor", "skill", "roll", "skill_pct", "margin",
              "challenge_value", "consequence", "xp_pool")


class UsageLog(list):
    """一列一擲。欄位同 resolution.md:session, turn, actor, skill, roll, skill%, margin,
    challenge_value, consequence(bool), xp_pool。"""

    def to_csv(self, path: str) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            w.writeheader()
            w.writerows(self)

    def xp_by_skill(self) -> dict:
        """場末 groupby(actor, skill),只計 consequence = true。"""
        out: dict = {}
        for r in self:
            if r["consequence"]:
                k = (r["actor"], r["skill"])
                out[k] = out.get(k, 0) + 1
        return out


@dataclass(frozen=True)
class Ctx:
    """寫紀錄用的情境。不影響解算。"""
    session: int = 0
    turn: int = 0
    actor: str = ""
    skill: str = ""
    challenge_value: Optional[int] = None
    consequence: bool = True
    xp_pool: str = "combat"


# ---------------------------------------------------------------------------
# 基本擲骰
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    roll: int
    skill: int
    success: bool
    margin: int        # skill − roll;失敗時為負
    crit: bool
    fumble: bool
    crit_zone: int


def roll(rng: RNG) -> int:
    """1..100,00 記為 100。"""
    return rng.randint(1, 100)


def crit_zone(skill: int, crit_bonus: int = 0, rounding: str = "ceil") -> int:
    """ceil(skill × 10%) + crit_bonus,不封頂。整數運算避免浮點誤差。
    rounding='half_up' 為符合 resolution.md 範例(52 → 05)的讀法(A0)。"""
    s = max(skill, 0)
    base = -(-s // 10) if rounding == "ceil" else (s + 5) // 10
    return base + crit_bonus


def fumble_floor(skill: int) -> int:
    """擲值 > 此值即特誤。skill < 100 時為失敗範圍底端 10%(向上取整);否則只剩 00。"""
    if skill >= 100:
        return 99
    return 100 - (-(-(100 - skill) // 10))


def overflow(skill: int) -> int:
    return max(skill - 100, 0)


def check(skill: int, rng: RNG, *, crit_bonus: int = 0, rerolls: int = 0,
          params: Params = DEFAULT, log: Optional[list] = None,
          ctx: Optional[Ctx] = None) -> Result:
    """一次判定。rerolls = 重骰類副技能可用次數(只能消 00)。每擲寫一列紀錄。"""
    r = roll(rng)
    while r == 100 and rerolls > 0:          # 重骰只消 00(硬禁令)
        rerolls -= 1
        r = roll(rng)
    fumble = r > fumble_floor(skill)
    success = r <= skill
    if fumble and params.fumble_beats_overflow:
        success = False
    zone = crit_zone(skill, crit_bonus, params.crit_rounding)
    crit = r <= zone and (success or not params.crit_requires_success)
    res = Result(roll=r, skill=skill, success=success, margin=skill - r,
                 crit=crit, fumble=fumble, crit_zone=zone)
    if log is not None:
        c = ctx or Ctx()
        log.append({"session": c.session, "turn": c.turn, "actor": c.actor,
                    "skill": c.skill, "roll": r, "skill_pct": skill,
                    "margin": res.margin, "challenge_value": c.challenge_value,
                    "consequence": c.consequence, "xp_pool": c.xp_pool})
    return res


# ---------------------------------------------------------------------------
# 侵蝕鏈 / 難度
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Layer:
    name: str
    skill: int
    level: int
    cap_bonus: int = 0       # 侵蝕強化副技能(例:順勢格擋 +5)
    crit_bonus: int = 0
    rerolls: int = 0

    @property
    def cap(self) -> int:
        return 10 * self.level + self.cap_bonus


@dataclass(frozen=True)
class Step:
    layer: str
    roll: int
    margin: int
    outcome: str             # 'fumble' | 'fail' | 'full' | 'erode'
    eroded: int
    T_after: int


def erode_step(T: int, layer: Layer, rng: RNG, params: Params = DEFAULT,
               log: Optional[list] = None, ctx: Optional[Ctx] = None) -> tuple:
    """單層侵蝕。回傳 (T_after, Step, Result)。供需要逐層付費的呼叫端使用。"""
    c = replace(ctx or Ctx(), skill=layer.name, challenge_value=T)
    res = check(layer.skill, rng, crit_bonus=layer.crit_bonus, rerolls=layer.rerolls,
                params=params, log=log, ctx=c)
    if res.fumble:
        return T, Step(layer.name, res.roll, res.margin, "fumble", 0, T), res
    if not res.success:
        return T, Step(layer.name, res.roll, res.margin, "fail", 0, T), res
    m = res.margin
    eff = min(m, layer.cap)
    test = m if params.full_defense == "raw" else eff
    if test >= T:
        return 0, Step(layer.name, res.roll, m, "full", T, 0), res
    T2 = T - eff
    return T2, Step(layer.name, res.roll, m, "erode", eff, T2), res


def erode(T: int, layers: Sequence[Layer], rng: RNG, params: Params = DEFAULT,
          log: Optional[list] = None, ctx: Optional[Ctx] = None) -> tuple:
    """幅度侵蝕鏈(resolution.md 偽碼)。不管池;付費由呼叫端處理。回傳 (T_left, steps)。"""
    steps = []
    for layer in layers:
        if T <= 0:
            break
        T, step, _ = erode_step(T, layer, rng, params, log, ctx)
        steps.append(step)
        if step.outcome == "fumble" and params.defense_fumble == "chain_break":
            break
    return max(T, 0), steps


def difficulty(D: int, aids: Sequence[Layer], rng: RNG, params: Params = DEFAULT,
               log: Optional[list] = None, ctx: Optional[Ctx] = None) -> int:
    """難度判定:D 即 T,相關技能逐一侵蝕,上限 10 × 等級。"""
    D_left, _ = erode(D, aids, rng, params, log,
                      replace(ctx or Ctx(), xp_pool="noncombat"))
    return D_left


# ---------------------------------------------------------------------------
# 傷判
# ---------------------------------------------------------------------------

def damage(weapon_die: int, T_left: int, overflow_pts: int, crit: bool, rng: RNG,
           params: Params = DEFAULT) -> int:
    """武器骰;溢出狀態下致命 → 滿骰;溢傷依 params.overflow_damage。
    T_left 只決定是否進傷判(T > 0),不加傷害(規則未給,A9)。"""
    if T_left <= 0:
        return 0
    die = weapon_die if (crit and overflow_pts > 0) else rng.randint(1, weapon_die)
    if params.overflow_damage == "flat":
        return die + overflow_pts
    return (die * (100 + overflow_pts) + 50) // 100   # 四捨五入的整數 × (1 + o/100)
