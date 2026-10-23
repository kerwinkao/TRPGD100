"""【Session 1 版,保留以便重現 experiments.py;Session 2 見 fight.py】
模擬用戰鬥迴圈。這裡是「模擬假設」,不是規則:
先攻固定輪流、防禦與疊加的貪婪策略、副技能的觸發解讀,都寫在這裡並列入 REPORT。
解算一律呼叫 resolve.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from resolve import Ctx, Layer, Params, check, damage_s1 as damage, erode_step, overflow

BASE = 42  # 均值角色(全 14)六條模擬技能的底值


def skill_value(level: int, base: int = BASE) -> int:
    return base + 10 * level


@dataclass(frozen=True)
class Build:
    name: str
    weapon_level: int = 0
    weapon_crit_bonus: int = 0
    dodge: int = 0                 # 各防禦技能等級
    parry: int = 0
    block: Optional[int] = None    # None = 沒帶盾
    reaction: int = 0              # 戰鬥反應(疊加用)
    neigong: int = 0               # 內功(疊加用;等級 ≥ 1 才有內力池,A12)
    stacks: tuple = ()             # 攻擊時嘗試疊加的技能名,依序
    chain_max: int = 3             # 守方最多用幾層
    neili_first: bool = False      # 內力流:超量先燒內力
    # 技術流副技能(skill-schema example 三條,解讀見 A14–A16)
    shunshi: bool = False          # 順勢格擋:招架侵蝕上限 +5
    lianci: bool = False           # 連刺:上一擊命中 → 本擊 +10
    xieren: bool = False           # 卸刃:招架幅度 ≥ 20 且完全防禦 → 對手下一擊作廢
    skill_override: Optional[dict] = None   # E7 高手:直接指定技能值 {'weapon':150,...}
    level_override: Optional[dict] = None   # 對應侵蝕上限用的等級

    def value(self, key: str) -> int:
        if self.skill_override and key in self.skill_override:
            return self.skill_override[key]
        return skill_value(self.level(key))

    def level(self, key: str) -> int:
        if self.level_override and key in self.level_override:
            return self.level_override[key]
        return {"weapon": self.weapon_level, "dodge": self.dodge, "parry": self.parry,
                "block": self.block or 0, "reaction": self.reaction,
                "neigong": self.neigong}[key]

    def layers(self) -> list:
        out = [Layer("閃避", self.value("dodge"), self.level("dodge")),
               Layer("招架", self.value("parry"), self.level("parry"),
                     cap_bonus=5 if self.shunshi else 0)]
        if self.block is not None:
            out.append(Layer("格擋", self.value("block"), self.level("block")))
        return out[: self.chain_max]


STACK_KEY = {"戰鬥反應": "reaction", "內功": "neigong"}


@dataclass
class State:
    hp: int
    stamina: int
    neili: int
    attacked_last: bool = False
    hit_last: bool = False
    disarmed: bool = False
    winded_at: Optional[int] = None     # 第幾次防禦時首次付不起一層
    defenses: int = 0
    layers_rolled: int = 0
    layers_paid: int = 0
    stamina_max: int = 0
    neili_max: int = 0

    def pay(self, cost: int, neili_first: bool) -> bool:
        if cost <= 0:
            return True
        if neili_first and self.neili >= cost:
            self.neili -= cost
            return True
        if self.stamina >= cost:
            self.stamina -= cost
            return True
        if not neili_first and self.neili >= cost:
            self.neili -= cost
            return True
        return False


def new_state(b: Build, p: Params, stamina: int = 28) -> State:
    neili = b.value("neigong") if b.neigong >= 1 else 0
    return State(hp=p.hp, stamina=stamina, neili=neili, stamina_max=stamina, neili_max=neili)


@dataclass
class AttackOut:
    attempted: bool
    success: bool
    crit: bool
    T: int
    T_left: int
    dmg: int
    eff_skill: int
    stack_bonus: int


def attack(a: Build, sa: State, d: Build, sd: State, p: Params, rng,
           log=None, ctx: Ctx = Ctx()) -> AttackOut:
    if sa.disarmed:                      # 卸刃:這一擊作廢(A16)
        sa.disarmed = False
        sa.attacked_last = False
        return AttackOut(False, False, False, 0, 0, 0, 0, 0)

    # 疊加
    bonus = 0
    if p.stack_mode != "none":
        for k, name in enumerate(a.stacks):
            cost = p.cost_stack * (3 ** k if p.stack_cost_growth == "steep" else 1)
            if not sa.pay(cost, a.neili_first):
                break
            key = STACK_KEY[name]
            bonus += a.value(key) if p.stack_mode == "full" else 10 * a.level(key)
        if p.stack_cap == "by_level":
            bonus = min(bonus, 10 * a.level("weapon"))
    if a.lianci and sa.hit_last:
        bonus += 10
    eff = a.value("weapon") + bonus

    res = check(eff, rng, crit_bonus=a.weapon_crit_bonus, params=p, log=log,
                ctx=replace(ctx, actor=a.name, skill="長劍"))
    sa.attacked_last = True
    if not res.success:
        sa.hit_last = False
        return AttackOut(True, False, False, 0, 0, 0, eff, bonus)
    T = res.margin

    # 守方逐層
    sd.defenses += 1
    layers = d.layers()
    if p.declare == "upfront":               # 先宣告並付清
        paid = []
        for i, L in enumerate(layers):
            cost = 0 if (i == 0 and p.free_first_layer) else p.cost_layer
            if not sd.pay(cost, d.neili_first):
                break
            paid.append(L)
        if len(paid) < len(layers) and sd.winded_at is None:
            sd.winded_at = sd.defenses
        sd.layers_paid += len(paid)
        layers = paid
    for i, L in enumerate(layers):
        if T <= 0:
            break
        if p.declare == "adaptive":
            cost = 0 if (i == 0 and p.free_first_layer) else p.cost_layer
            if not sd.pay(cost, d.neili_first):
                if sd.winded_at is None:
                    sd.winded_at = sd.defenses
                break
            sd.layers_paid += 1
        sd.layers_rolled += 1
        T_new, step, r = erode_step(T, L, rng, p, log,
                                    replace(ctx, actor=d.name))
        if d.xieren and L.name == "招架" and step.outcome == "full" and step.margin >= 20:
            sa.disarmed = True
        T = T_new
        if step.outcome == "fumble" and p.defense_fumble == "chain_break":
            break

    dmg = damage(p.weapon_die, T, overflow(eff), res.crit, rng, p)
    sd.hp -= dmg
    sa.hit_last = T > 0
    return AttackOut(True, True, res.crit, res.margin, max(T, 0), dmg, eff, bonus)


@dataclass
class FightOut:
    winner: Optional[int]     # 0 / 1 / None(平手或回合上限)
    rounds: int
    states: tuple
    events: list              # 每擊 (attacker_idx, AttackOut)


def fight(b0: Build, b1: Build, p: Params, rng, first: int = 0, max_rounds: int = 300,
          stamina: int = 28, log=None, session: int = 0, keep_events: bool = True) -> FightOut:
    builds = (b0, b1)
    states = (new_state(b0, p, stamina), new_state(b1, p, stamina))
    events = []
    order = (first, 1 - first)
    for rnd in range(1, max_rounds + 1):
        for i in order:
            j = 1 - i
            out = attack(builds[i], states[i], builds[j], states[j], p, rng, log,
                         Ctx(session=session, turn=rnd))
            if keep_events:
                events.append((i, out))
            if states[j].hp <= 0:
                return FightOut(i, rnd, states, events)
        for s in states:
            s.stamina = min(s.stamina_max, s.stamina + p.regen_stamina)
            s.neili = min(s.neili_max, s.neili + p.regen_neili)
    return FightOut(None, max_rounds, states, events)
