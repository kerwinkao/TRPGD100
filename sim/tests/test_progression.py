"""progression.py 測試(Session 3):累計表、難度倍率、補差額、副技能 1/10。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from progression import (cum_cost, level_increment, max_level_within, subskill_cost,
                         tier_upgrade_diff)


def test_cumulative_table():
    assert [cum_cost(l) for l in range(8) if l <= 6] == [0, 100, 300, 600, 1000, 1500, 2500]
    assert [level_increment(l) for l in range(1, 7)] == [100, 200, 300, 400, 500, 1000]


def test_level_cap_six():
    try:
        cum_cost(7)
    except ValueError:
        return
    raise AssertionError("7 級應被拒絕(單技能上限 6 級)")


def test_tier_multiplier():
    assert cum_cost(4, 1) == 1000
    assert cum_cost(4, 2) == 1200            # 難度 2:× 1.20
    assert cum_cost(4, 5) == 1750            # 難度 5:× 1.75
    assert abs(cum_cost(2, 3) - 405) < 1e-9


def test_tier_upgrade_diff_matches_doc():
    # progression.md:4 級普通 1000,升難度 2 補 200
    assert abs(tier_upgrade_diff(4, 1, 2) - 200) < 1e-9


def test_subskill_cost_tenth():
    assert subskill_cost(1) == 10
    assert subskill_cost(3) == 60
    assert abs(subskill_cost(2, 2) - 36) < 1e-9


def test_max_level_within():
    assert max_level_within(300) == 2
    assert max_level_within(1000) == 4
    assert max_level_within(2500) == 6
    assert max_level_within(2500, cap=4) == 4
    assert max_level_within(1000, tier=2) == 3     # 1200 > 1000


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            n += 1
    print(f"{n} tests passed")
