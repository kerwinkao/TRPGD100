"""經驗成本:累計表 × 難度倍率(design/progression.md)。純函數。

- 累計經驗表(普通難度階):1–7 級 100 / 300 / 600 / 1000 / 1500 / 2500 / 4000。單技能上限 6 級(spec Session 3 §1)。
- 難度階倍率:1.00 / 1.20 / 1.35 / 1.50 / 1.75。
- 升階(鑽研)補差額:已投入經驗 × (新倍率 − 舊倍率);幅度累計門檻升 2 → 100、升 3 → 200(4、5 待確認)。
- 副技能經驗 = 主技能同級累計成本的 1/10(spec Session 3 §7);副技能自身難度倍率待定,本檔不乘。
"""
from __future__ import annotations

CUM = (0, 100, 300, 600, 1000, 1500, 2500, 4000)
TIER_MULT = {1: 1.00, 2: 1.20, 3: 1.35, 4: 1.50, 5: 1.75}
LEVEL_CAP = 6


def cum_cost(level: int, tier: int = 1) -> float:
    """某技能(持有難度階 tier)練到 level 級的累計經驗。"""
    if not 0 <= level <= LEVEL_CAP:
        raise ValueError(f"等級 {level} 超出 0–{LEVEL_CAP}")
    return CUM[level] * TIER_MULT[tier]


def level_increment(level: int, tier: int = 1) -> float:
    """從 level−1 級升到 level 級要付的增量。"""
    return cum_cost(level, tier) - cum_cost(level - 1, tier)


def tier_upgrade_diff(level: int, old_tier: int, new_tier: int) -> float:
    """鑽研升階的經驗補差額:已投入經驗 × 新舊倍率差。"""
    return CUM[level] * (TIER_MULT[new_tier] - TIER_MULT[old_tier])


def subskill_cost(level: int, main_tier: int = 1) -> float:
    """副技能練到 level 級的經驗:主技能同級累計成本的 1/10。"""
    return cum_cost(level, main_tier) / 10


def max_level_within(budget: float, tier: int = 1, cap: int = LEVEL_CAP) -> int:
    """單技能獨吞 budget 最多幾級(受 cap 限制)。"""
    lv = 0
    while lv < cap and cum_cost(lv + 1, tier) <= budget:
        lv += 1
    return lv
