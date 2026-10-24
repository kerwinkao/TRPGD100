"""解算器:一條數線上的判定、侵蝕鏈、難度、傷判。

規則來源:design/resolution.md、sim/spec.md。
Session 2:招架、格擋改傷害對抗(parry / block / damage 新簽名),只有閃避(dodge)侵蝕 T,
步法(step)回傳幅度。Session 1 的侵蝕鏈(erode / Layer / damage_s1)保留給難度判定與舊測試。
- 純函數:不讀寫全域狀態。隨機源 `rng` 由呼叫端注入(任何有 randint(a, b) 的物件,
  例如 random.Random(seed))。使用紀錄寫進呼叫端注入的 `log`。
- 規則沒講清楚的地方一律做成 Params 的開關,預設值不是設計決定,
  對應條目見 design/ambiguities.md(以 A# 標註)。
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Protocol, Sequence


class RNG(Protocol):
    def randint(self, a: int, b: int) -> int: ...


# ---------------------------------------------------------------------------
# 參數
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Params:
    # Session 2 spec.md 列出的參數
    act_threshold: float = math.inf      # 一動最多願付幾點(∞ = 付得起就動);實驗自行指定
    shield: Optional[str] = None         # 'small' | 'large' | None
    dual_wield: str = "lv1"              # 雙持技能等級:'none'(0)| 'lv1'(1)| 'full'(抵銷完)
    weight_k: Optional[float] = None     # 負重上限 Con × k;None = 不啟用
    parry_pool: str = "skill"            # A23:招架吃哪個低價池:'skill' 招架自己 | 'weapon' 武器(攻守共用)
    defense_combine: str = "sum"         # A24:招架與格擋同時成功:'sum' 相加 | 'max' 取大

    # Session 1 spec.md 列出的參數
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
    overflow_damage: str = "flat"        # A8 已裁 'flat'(傷害 + 溢出點);'pct' 僅供 Session 1 舊測試
    stack_mode: str = "level"            # A10:'level' 疊 +10×被疊技能等級 | 'full' 疊 + 被疊技能值 | 'none'
    stack_cap: str = "none"              # E3 對策:'none' | 'by_level' 疊加總額 ≤ 10 × 主技能等級
    stack_cost_growth: str = "flat"      # E3 對策:'flat' | 'steep' 同一動作第 k 個疊加付 cost_stack × 3^(k−1)
    hp: int = 30                         # A11:生命值公式待定,先用常數
    weapon_die: int = 8                  # 長劍 1d8

    # Session 3 規則開關。預設值維持 Session 2 行為(舊測試與 Session 2 實驗不變);
    # Session 3 實驗用 s3_params() 一次打開。
    hp_model: str = "const"              # 'const' 用 hp;'str_con' 上限 = Str+Con(attributes.md)
    stamina_model: str = "const"         # 'const' 預設 28;'con' 體力池 = Con+Con
    regen_model: str = "const"           # 'const' 用 regen_stamina;'con' = Con 效益值 ⌊(Con−10)/2⌋ 下限 0
    damage_model: str = "flat"           # 'flat' Session 2(骰 + 溢出點);'s3' (骰 + 屬性補正) × (1 + 溢出/100)
    activate: str = "always"             # 副技能發動策略:'always' 付得起就發動;'never' 不發動(+1/個,不進累進格)
    react: str = "always"                # 事件→被動反應:'always' 付得起就反應;'never' 不反應吃後果
    sub_order: str = "link_first"        # 付不起全部副技能時,依序發動前 k 個(A43):'link_first' link 先、反擊後;'counter_first' 反擊先
    end_on: str = "incap"                # 戰鬥終止:'incap' 輕傷位 0 失能即結束(設計文件);'death' 打到重傷位 0 才結束(A40,擊殺矩陣用)
    # Session 4 規則開關(同樣預設維持舊行為;s4_params() 一次打開)
    sub_fee: float = 1.0                 # 副技能發動費(點/個,體力可小數);Session 4 裁 0.2
    reroll_pool: str = "stamina"         # 任意重骰(劍意)發動費燒哪個池:'stamina' | 'shenshi'(Session 4)
    shenshi_fee: float = 1.0             # 燒神識的副技能每次費用(佔位 1、不佔格、不進價碼與門檻,A53)
    link_bonus: int = 10                 # link 每級加成(E9 掃 {10,15,20})
    neili_model: str = "skill_value"     # 'skill_value' 內力池 = 內功技能值(A12);'s4' 效益值和 × 難度階 × 等級,三種用途
    neili_stack: bool = True             # 內力疊發:同一動作可灌多次輸出;False 每動作只灌一次輸出
    # Session 5:主動權交換
    initiative: str = "turns"            # 'turns' 雙方各一手番(Session 2–4,A28);'swap' 主動權交換(resolution「主動權交換」)
    counter_policy: str = "never"        # 守方被攻擊時是否對攻:'never' 一律防 | 'always' 付得起就對攻 | 'lead' 輕傷位領先攻方時對攻
    counter_initiative: str = "by_hit"   # 對攻之後主動權:'by_hit' 攻方擲骰成功才保有 | 'keep' 攻方一律保有(A67)
    dodge_swap: str = "full"             # 閃避算不算「防禦成功」:'full' 只有全閃 | 'any' 閃避擲骰成功(含部分侵蝕)即算(A68)
    stack_pay: str = "slot"              # 疊加付費:'slot' 佔被疊技能的格、走累進與低價;'none' 免費(Session 1 舊讀法)
    # Session 6:每回合重擲先攻、預判、無預判者盲防(預設維持 Session 5 行為)
    initiative_roll: str = "fixed"       # 'fixed' 每場先手交替、每回合開頭持主動者 = 該場先手(A73);'reroll' 每回合重擲行動值(A80)
    info_model: str = "legacy"           # 'legacy' 守方看得到攻擊結果再選防禦、內力付剛好(Session 4–5,A57);'s6' 預判 / 盲防(A81)
    blind_qi: str = "full"               # s6 無預判者的內力防禦固定策略:'full' 每次灌滿 | 'half' 灌一半(A84)
    foresight_dc: int = 10               # 預判難度:戰鬥反應幅度 ≥ 此值才看得到(散修 10)
    foresight_shen: float = 1.0          # 預判燒神識(佔位 1,不佔格)
    fs_dodge_min: float = 0.25           # 預判成功者:全閃機率(技能值 − T)/100 低於此值就不付閃避(A83)


DEFAULT = Params()


def s3_params(**kw) -> Params:
    """Session 3 規則一次打開:Str+Con 上限、Con+Con 體力池、Con 效益值回復、乘數溢傷加屬性補正。"""
    base = dict(hp_model="str_con", stamina_model="con", regen_model="con", damage_model="s3")
    base.update(kw)
    return Params(**base)


def s4_params(**kw) -> Params:
    """Session 4 規則一次打開:Session 3 全部 + 發動費 0.2、劍意燒神識、內力三用途。
    失能即勝(單挑)是 end_on='incap' 的預設,不需另開。"""
    base = dict(sub_fee=0.2, reroll_pool="shenshi", neili_model="s4")
    base.update(kw)
    return s3_params(**base)


def s5_params(**kw) -> Params:
    """Session 5:Session 4 全部 + 主動權交換(取代雙方各一手番)。counter_policy 預設 'never',實驗另掃。"""
    base = dict(initiative="swap")
    base.update(kw)
    return s4_params(**base)


def s6_params(**kw) -> Params:
    """Session 6:Session 5 全部 + 每回合重擲先攻 + 預判 / 盲防;守方 counter_policy 預設 lead。"""
    base = dict(initiative_roll="reroll", info_model="s6", counter_policy="lead")
    base.update(kw)
    return s5_params(**base)


def effect_value(attr: int) -> int:
    """屬性效益值(attributes.md 通則):⌊(屬性 − 10) / 2⌋,下限 0。14 → 2,20 → 5。"""
    return max(0, (attr - 10) // 2)


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


Dice = "int | tuple"          # 骰型:int = 1dN;(n, s) = nds,例 (2, 4) = 2d4


def dice_spec(d) -> tuple:
    return (1, d) if isinstance(d, int) else (d[0], d[1])


def roll_dice(d, rng: RNG) -> int:
    """擲武器骰。1dN 只呼叫一次 randint,與舊版擲骰順序相同。"""
    n, sides = dice_spec(d)
    return sum(rng.randint(1, sides) for _ in range(n))


def max_dice(d) -> int:
    n, sides = dice_spec(d)
    return n * sides


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
          reroll_any: bool = False,
          params: Params = DEFAULT, log: Optional[list] = None,
          ctx: Optional[Ctx] = None) -> Result:
    """一次判定。rerolls = 重骰類副技能可用次數(只能消 00)。每擲寫一列紀錄(重骰只記最後一擲)。
    reroll_any = 任意重骰(【劍意】類):第一擲不是成功(含特誤)就重擲一次,取後者(策略假設,A39)。"""
    r = roll(rng)
    while r == 100 and rerolls > 0:          # 重骰只消 00(硬禁令)
        rerolls -= 1
        r = roll(rng)
    if reroll_any and (r > skill or r > fumble_floor(skill)):
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

def damage_s1(weapon_die: int, T_left: int, overflow_pts: int, crit: bool, rng: RNG,
              params: Params = DEFAULT) -> int:
    """Session 1 版傷判(T_left > 0 才進傷判,無守方吸收)。保留給舊測試;Session 2 用 damage()。"""
    if T_left <= 0:
        return 0
    die = weapon_die if (crit and overflow_pts > 0) else rng.randint(1, weapon_die)
    if params.overflow_damage == "flat":
        return die + overflow_pts
    return (die * (100 + overflow_pts) + 50) // 100   # 四捨五入的整數 × (1 + o/100)


# ---------------------------------------------------------------------------
# Session 2:防禦鏈新結構(閃避侵蝕 T;招架、格擋做傷害對抗;步法轉加值)
# ---------------------------------------------------------------------------

def step(skill: int, rng: RNG, *, params: Params = DEFAULT, log: Optional[list] = None,
         ctx: Optional[Ctx] = None) -> int:
    """步法:成功回傳幅度 S(=技能值 − 擲值),失敗或特誤回傳 0。
    S 加到哪個動作的技能值由呼叫端決定(不進侵蝕上限)。"""
    res = check(skill, rng, params=params, log=log, ctx=replace(ctx or Ctx(), skill="步法"))
    return res.margin if res.success else 0


def dodge(T: int, skill: int, level: int, rng: RNG, *, params: Params = DEFAULT,
          log: Optional[list] = None, ctx: Optional[Ctx] = None) -> tuple:
    """閃避:回傳 (T_left, full_dodge)。
    特誤、失敗 → T 不變;m ≥ T → 全閃(用原始 m,A4);否則 T −= min(m, 10 × level)。"""
    c = replace(ctx or Ctx(), skill="閃避", challenge_value=T)
    res = check(skill, rng, params=params, log=log, ctx=c)
    if not res.success:
        return T, False
    m = res.margin
    if m >= T:
        return 0, True
    return T - min(m, 10 * level), False


def dodge_ex(T: int, skill: int, level: int, rng: RNG, *, params: Params = DEFAULT,
             log: Optional[list] = None, ctx: Optional[Ctx] = None) -> tuple:
    """同 dodge,另回傳擲骰是否成功:(T_left, 全閃, 擲骰成功)。主動權交換的 dodge_swap='any' 要用
    (零級閃避的侵蝕上限為 0,T 不變,不能用 T_left < T 判斷成功)。"""
    c = replace(ctx or Ctx(), skill="閃避", challenge_value=T)
    res = check(skill, rng, params=params, log=log, ctx=c)
    if not res.success:
        return T, False, False
    m = res.margin
    if m >= T:
        return 0, True, True
    return T - min(m, 10 * level), False, True


def parry(skill: int, weapon_die: int, rng: RNG, *, params: Params = DEFAULT,
          log: Optional[list] = None, ctx: Optional[Ctx] = None) -> Optional[int]:
    """招架:成功 → 守方擲武器傷害骰,回傳 D_def;失敗或特誤 → None。不碰 T。"""
    res = check(skill, rng, params=params, log=log, ctx=replace(ctx or Ctx(), skill="招架"))
    return roll_dice(weapon_die, rng) if res.success else None


def parry_ex(skill: int, weapon_die: int, rng: RNG, *, params: Params = DEFAULT,
             log: Optional[list] = None, ctx: Optional[Ctx] = None) -> tuple:
    """同 parry,另回傳擲骰幅度:(D_def 或 None, 幅度)。反擊事件用幅度當 T(A37)。"""
    res = check(skill, rng, params=params, log=log, ctx=replace(ctx or Ctx(), skill="招架"))
    if not res.success:
        return None, 0
    return roll_dice(weapon_die, rng), res.margin


def block(skill: int, shield_value: int, rng: RNG, *, params: Params = DEFAULT,
          log: Optional[list] = None, ctx: Optional[Ctx] = None) -> Optional[int]:
    """格擋:技能 +10(裝備加成);成功 → D_def = 盾值(固定),失敗或特誤 → None。"""
    res = check(skill + 10, rng, params=params, log=log, ctx=replace(ctx or Ctx(), skill="格擋"))
    return shield_value if res.success else None


def shield_value(kind: Optional[str], strength: int = 14) -> int:
    """盾值:小盾 1、大盾 2,加 ⌊(Str−10)/2⌋,下限 0。無盾回傳 0。"""
    if kind is None:
        return 0
    return max(0, {"small": 1, "large": 2}[kind] + (strength - 10) // 2)


def damage(weapon_die: int, overflow_pts: int, crit: bool, D_def: int, rng: RNG) -> int:
    """傷判:D_atk = 武器骰 + 溢傷(點);溢出狀態下致命 → 武器骰滿骰。
    傷害 = max(0, D_atk − D_def)。T 不進傷判(A9)。"""
    die = weapon_die if (crit and overflow_pts > 0) else rng.randint(1, weapon_die)
    return max(0, die + overflow_pts - D_def)


def damage_s3(weapon_die, bonus: int, overflow_pts: int, crit: bool, D_def: int,
              rng: RNG, extra: int = 0) -> tuple:
    """傷判:D_atk = 四捨五入((骰 + 屬性補正) × (1 + 溢出/100)) + extra;溢出狀態下致命 → 骰滿骰。
    weapon_die 可為 int(1dN)或 (顆數, 面數);extra = 內力攻擊灌注(Session 4,乘數之後才加)。
    傷害 = max(0, D_atk − D_def)。回傳 (傷害, D_atk)。例:長劍、Str 14、技能 125 致命 → (8+2)×1.25 → 13。"""
    die = max_dice(weapon_die) if (crit and overflow_pts > 0) else roll_dice(weapon_die, rng)
    d_atk = ((die + bonus) * (100 + overflow_pts) + 50) // 100 + extra
    return max(0, d_atk - D_def), d_atk
