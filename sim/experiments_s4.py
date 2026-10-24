"""Session 4 實驗:E9 副技能效果 × 發動費九格、E10 內功 CP 值、E3 加武器軸、E8 劍意燒神識、E2 同經驗矩陣。
用法:python3 sim/experiments_s4.py [e9 e10 e3 e8 e2]
輸出到 sim/out/,檔名一律 s4_ 前綴(不覆蓋 Session 1–3)。

Session 4 共同設定(都不是規則):s4_params()(副技能發動費預設 0.2、劍意燒神識、內力三用途、失能即勝),
散修基準武器單手 1d6,屬性與池由 Str/Con/Wis 算,均值角色全 14(HP 28、體力 28、回復 2、神識 28)。
失能即勝(單挑):只有一張勝率矩陣,不再跑 end_on='death'。
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

sys.path.insert(0, os.path.dirname(__file__))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import npc
from experiments_s3 import (CORE, INF, SEED, THR_LABEL, TPL, TPL_BY, alloc_levels, div, heat, monopoly,
                            r4, write_csv, write_json)
from fight import Build, fight, neili_cap, neili_level, neili_pool
from progression import cum_cost, level_increment, subskill_cost, tier_upgrade_diff
from resolve import s4_params

OUT = os.path.join(os.path.dirname(__file__), "out")
BASE_DICE = npc.BASELINE_DICE        # 散修基準武器:單手 1d6

STAT_FIELDS = ("att", "succ", "crit_roll", "fumble", "thru", "dmg_hit", "dmg_total", "full_dodged",
               "parry_try", "parry_ok", "block_try", "block_ok", "acts", "atk_acts", "def_acts", "faced",
               "refuse_n", "winded_n", "sub_fee", "sub_active", "sub_offered", "shen_spent", "stack_acts",
               "ev_gen", "ev_dmg_dealt", "qi_atk_pts", "qi_atk_dmg", "qi_def_pts", "qi_def_abs",
               "qi_heal_pts", "qi_heal_amt", "heal_acts")


def weapon(level, dice=BASE_DICE, **kw):
    """散修基準的武器配置(骰型可換,內力等欄位走 kw)。"""
    return Build(kw.pop("name", "甲"), weapon=level, weapon_die=dice[1], weapon_dice=dice, **kw)


# ---------------------------------------------------------------------------
# 共用:對戰彙總(失能即勝,只有一張勝率)
# ---------------------------------------------------------------------------

def run_pair(args):
    b0, b1, p, n, seed = args
    rng = random.Random(seed)
    acc = [dict.fromkeys(STAT_FIELDS, 0.0), dict.fromkeys(STAT_FIELDS, 0.0)]
    for a in acc:
        a.update(rounds=0, qi_left=0.0, shen_left=0.0, stamina_left=0.0)
    g = dict(n=n, wins=[0, 0], draws=0, stalled=0, rounds=0)
    for i in range(n):
        f = fight(b0, b1, p, rng, first=i % 2)
        g["rounds"] += f.rounds
        if f.winner is None:
            g["draws"] += 1
            g["stalled"] += f.stalled
        else:
            g["wins"][f.winner] += 1
        for k, s in enumerate(f.states):
            a = acc[k]
            for fld in STAT_FIELDS:
                a[fld] += getattr(s.st, fld)
            a["rounds"] += f.rounds
            a["qi_left"] += s.qi
            a["shen_left"] += s.shen
            a["stamina_left"] += s.stamina
    return acc, g


def win_rate(g, side=0):
    return (g["wins"][side] + g["draws"] / 2) / g["n"]


def matrix(builds, p, n, seed, pool):
    jobs, keys = [], []
    for i, a in enumerate(builds):
        for j, b in enumerate(builds):
            if i < j:
                jobs.append((a, b, p, n, seed + 1000 * i + j))
                keys.append((i, j))
    res = dict(zip(keys, pool.map(run_pair, jobs)))
    N = len(builds)
    M = [[0.5] * N for _ in builds]
    R = [[0.0] * N for _ in builds]
    D = [[0.0] * N for _ in builds]
    for (i, j), (acc, g) in res.items():
        M[i][j], M[j][i] = win_rate(g, 0), win_rate(g, 1)
        R[i][j] = R[j][i] = g["rounds"] / n
        D[i][j] = D[j][i] = g["draws"] / n
    return dict(M=M, R=R, D=D)


# ---------------------------------------------------------------------------
# E9 副技能:效果 {10,15,20}/級 × 發動費 {1,0.5,0.2}/個,九格 × 兩個情境
# ---------------------------------------------------------------------------

# 情境:(名稱, 主技能難度階, 副技能等級, 主流派武器/招架等級, 副技能流武器等級)
#   副技能流 = 武器少一級 + link 雙向 + 反擊(各副技能 = sub_level 級)
#   主技能流 = 武器多一級,不持有副技能
#   有 / 無副技能 = 同樣的主技能等級,有掛副技能 vs 沒掛
E9_SCENARIOS = (
    ("散修互毆", 1, 1, dict(weapon=2, parry=1)),
    ("高手難度3", 3, 3, dict(weapon=5, parry=3)),
    ("高手難度4", 4, 4, dict(weapon=6, parry=4)),
)
E9_EFFECTS = (10, 15, 20)
E9_FEES = (1.0, 0.5, 0.2)


def e9_build(name, tier, weapon_lv, parry_lv, sub_lv):
    kw = dict(link_w2p=sub_lv, link_p2w=sub_lv, counter=sub_lv) if sub_lv else {}
    return weapon(weapon_lv, name=name, parry=parry_lv, **kw)


def e9_xp(tier, weapon_lv, parry_lv, sub_lv):
    """經驗:兩個主技能(持有難度階 tier)+ 三個難度 1 副技能各 sub_lv 級(副技能自身等級成本 × 難度倍率 × 1/10)。"""
    return cum_cost(weapon_lv, tier) + cum_cost(parry_lv, tier) + 3 * subskill_cost(sub_lv, 1) * (1 if sub_lv else 0)


def e9(n=8000):
    thrs = (3, INF)
    jobs, meta = [], []
    builds = {}
    for sc, tier, sl, m in E9_SCENARIOS:
        W, P = m["weapon"], m["parry"]
        builds[sc] = dict(
            F=e9_build("副技能流", tier, W - 1, P, sl), M=e9_build("主技能流", tier, W, P, 0),
            X=e9_build("有副技能", tier, W, P, sl), X0=e9_build("無副技能", tier, W, P, 0),
            xp=dict(F=e9_xp(tier, W - 1, P, sl), M=e9_xp(tier, W, P, 0),
                    X=e9_xp(tier, W, P, sl), X0=e9_xp(tier, W, P, 0)))
    for sc, tier, sl, m in E9_SCENARIOS:
        for thr in thrs:
            for eff in E9_EFFECTS:
                for fee in E9_FEES:
                    p = s4_params(act_threshold=thr, link_bonus=eff, sub_fee=fee)
                    B = builds[sc]
                    for tag, a, b in (("X_vs_X0", B["X"], B["X0"]), ("F_vs_M", B["F"], B["M"])):
                        jobs.append((a, b, p, n, SEED + 9000 + len(jobs)))
                        meta.append((sc, thr, eff, fee, tag))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    cells = {}
    for (sc, thr, eff, fee, tag), (acc, g) in zip(meta, res):
        cells.setdefault((sc, thr, eff, fee), {})[tag] = (acc, g)
    rows = []
    for (sc, thr, eff, fee), d in cells.items():
        (ax, gx), (af, gf) = d["X_vs_X0"], d["F_vs_M"]
        a0, a1 = ax[0], af[0]
        xp = builds[sc]["xp"]
        rows.append({
            "scenario": sc, "act_threshold": THR_LABEL[thr], "link_effect_per_level": eff, "sub_fee": fee,
            "activation_rate": r4(div(a0["sub_active"], a0["sub_offered"])),
            "sub_fee_paid_per_fight": r4(a0["sub_fee"] / gx["n"]),
            "parry_ok_rate_with_subs": r4(div(a0["parry_ok"], a0["parry_try"])),
            "parry_ok_rate_without_subs": r4(div(ax[1]["parry_ok"], ax[1]["parry_try"])),
            "counter_events_per_fight": r4(a0["ev_gen"] / gx["n"]),
            "win_with_vs_without_subs": r4(win_rate(gx, 0)),
            "win_sub_flow_vs_main_flow": r4(win_rate(gf, 0)),
            "mean_rounds_X_vs_X0": round(gx["rounds"] / gx["n"], 2),
            "mean_rounds_F_vs_M": round(gf["rounds"] / gf["n"], 2),
            "xp_sub_flow": round(xp["F"], 1), "xp_main_flow": round(xp["M"], 1),
            "xp_with_subs": round(xp["X"], 1), "xp_without_subs": round(xp["X0"], 1),
            "draw_rate_X_vs_X0": r4(gx["draws"] / gx["n"]), "draw_rate_F_vs_M": r4(gf["draws"] / gf["n"]),
        })
    write_csv("s4_e9_sub_sweep.csv", rows)
    write_json("s4_summary_e9.json", {"rows": rows})
    fig, axes = plt.subplots(2, len(E9_SCENARIOS), figsize=(5 * len(E9_SCENARIOS), 8))
    for ci, (sc, *_rest) in enumerate(E9_SCENARIOS):
        for ri, col, ttl in ((0, "win_with_vs_without_subs", "有 vs 無副技能勝率"),
                             (1, "win_sub_flow_vs_main_flow", "副技能流 vs 主技能流勝率")):
            M = [[next(r[col] for r in rows if r["scenario"] == sc and r["act_threshold"] == "3"
                       and r["link_effect_per_level"] == eff and r["sub_fee"] == fee) for fee in E9_FEES]
                 for eff in E9_EFFECTS]
            ax = axes[ri][ci]
            ax.imshow(M, cmap="RdYlGn", vmin=0, vmax=1)
            ax.set_xticks(range(3))
            ax.set_xticklabels([f"費 {f}" for f in E9_FEES])
            ax.set_yticks(range(3))
            ax.set_yticklabels([f"+{e}/級" for e in E9_EFFECTS])
            for i in range(3):
                for j in range(3):
                    ax.text(j, i, f"{M[i][j]:.2f}", ha="center", va="center", fontsize=9)
            ax.set_title(f"{sc}:{ttl}(門檻 3)", fontsize=9)
    fig.suptitle("E9 副技能效果 × 發動費(link 雙向 + 反擊)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s4_e9.png"), dpi=115)
    plt.close(fig)


# ---------------------------------------------------------------------------
# E10 內功 CP 值
# ---------------------------------------------------------------------------

E10_PAIRS = (
    # (名稱, 內功等級, 長劍等級, 對照長劍等級)
    ("內功1+長劍1 vs 長劍2", 1, 1, 2),
    ("內功2+長劍2 vs 長劍3", 2, 2, 3),
)
E10_USES = (("全押攻", ("attack",)), ("全押防", ("defense",)), ("留療傷", ("heal",)))


def e10(n=10_000):
    thrs = (3, INF)
    jobs, meta = [], []
    for pname, ng, wl, ctrl in E10_PAIRS:
        for uname, use in E10_USES:
            qi = weapon(wl, name="內功流", neigong=ng, neili_use=use)
            ref_same = weapon(wl, name="同級無內功")
            ref_up = weapon(ctrl, name="多一級武器")
            for thr in thrs:
                p = s4_params(act_threshold=thr)
                for tag, b in (("vs_多一級武器", ref_up), ("vs_同級無內功", ref_same)):
                    jobs.append((qi, b, p, n, SEED + 10000 + len(jobs)))
                    meta.append((pname, ng, wl, ctrl, uname, thr, tag))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (pname, ng, wl, ctrl, uname, thr, tag), (acc, g) in zip(meta, res):
        a = acc[0]
        b0 = weapon(wl, neigong=ng)
        rows.append({
            "pair": pname, "neili_use": uname, "act_threshold": THR_LABEL[thr], "opponent": tag,
            "neigong_level": ng, "qi_pool": neili_pool(b0, s4_params()), "qi_level_cap": neili_level(replace(b0, neigong=9), s4_params()),
            "single_output_cap": neili_cap(neili_level(b0, s4_params())),
            "win_rate": r4(win_rate(g, 0)), "draw_rate": r4(g["draws"] / n),
            "mean_rounds": round(g["rounds"] / n, 2),
            "qi_spent_per_fight": r4((a["qi_atk_pts"] + a["qi_def_pts"] + a["qi_heal_pts"]) / n),
            "qi_left_per_fight": r4(a["qi_left"] / n),
            "attack_bonus_dmg_per_fight": r4(a["qi_atk_dmg"] / n),
            "defense_absorbed_per_fight": r4(a["qi_def_abs"] / n),
            "heal_amount_per_fight": r4(a["qi_heal_amt"] / n),
            "heal_actions_per_fight": r4(a["heal_acts"] / n),
            "xp_internal_side": cum_cost(wl) + cum_cost(ng),
            "xp_opponent": cum_cost(ctrl) if tag == "vs_多一級武器" else cum_cost(wl),
        })
    write_csv("s4_e10_neigong_cp.csv", rows)
    write_json("s4_summary_e10.json", {"rows": rows})
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), sharey=True)
    for ax, tag in zip(axes, ("vs_多一級武器", "vs_同級無內功")):
        w = 0.25
        for ui, (uname, _u) in enumerate(E10_USES):
            for pi, (pname, *_x) in enumerate(E10_PAIRS):
                for ti, thr in enumerate((3, INF)):
                    r = next(r for r in rows if r["pair"] == pname and r["neili_use"] == uname
                             and r["act_threshold"] == THR_LABEL[thr] and r["opponent"] == tag)
                    ax.bar(pi * 4 + ui + (ti - 0.5) * 0.4 / 1.0 * 0.5, r["win_rate"], 0.2,
                           color=("C3", "C0", "C2")[ui], alpha=1.0 if ti == 0 else 0.5,
                           label=f"{uname} 門檻{THR_LABEL[thr]}" if pi == 0 else None)
        ax.axhline(0.5, color="gray", ls=":")
        ax.set_xticks([0 + 1, 4 + 1])
        ax.set_xticklabels([p[0] for p in E10_PAIRS], fontsize=8)
        ax.set_title(tag, fontsize=10)
    axes[0].set_ylabel("內功流勝率")
    axes[0].legend(fontsize=6, ncol=2)
    fig.suptitle("E10 內功 CP 值(均值角色效益值全 +2,散修難度 1,武器 1d6;淺色 = 門檻 ∞)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s4_e10.png"), dpi=115)
    plt.close(fig)


# ---------------------------------------------------------------------------
# E3 攻守貨幣(加武器軸)
# ---------------------------------------------------------------------------

E3_DICE = (("1d6", (1, 6)), ("1d8", (1, 8)), ("2d4", (2, 4)))


def e3_builds(N, dice):
    """總等級 N 平分;單技能 ≤ 4 級,全押攻超出的部分放閃避(A42)。同一場雙方武器骰相同(招架吸收也用它)。"""
    w = min(N, 4)
    mk = lambda **kw: weapon(kw.pop("weapon", 0), dice, **kw)
    return [
        mk(name="全押攻", weapon=w, dodge=N - w),
        mk(name="均衡", weapon=(N + 1) // 2, dodge=(N - (N + 1) // 2 + 1) // 2, parry=(N - (N + 1) // 2) // 2),
        mk(name="全押守", dodge=(N + 1) // 2, parry=N // 2),
        mk(name="全押步法閃避", step=(N + 1) // 2, dodge=N // 2, step_def="dodge"),
    ]


def e3(n=4_000):
    thrs = (3, INF)
    Ns = (3, 4, 6)
    jobs, meta = [], []
    for dn, dice in E3_DICE:
        for N in Ns:
            builds = e3_builds(N, dice)
            for thr in thrs:
                p = s4_params(act_threshold=thr)
                for i in range(len(builds)):
                    for j in range(i + 1, len(builds)):
                        jobs.append((builds[i], builds[j], p, n, SEED + 3000 + len(jobs)))
                        meta.append((dn, N, thr, builds[i].name, builds[j].name))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (dn, N, thr, a, b), (acc, g) in zip(meta, res):
        rows.append({"weapon_dice": dn, "total_levels": N, "act_threshold": THR_LABEL[thr], "row": a, "col": b,
                     "row_win_rate": r4(win_rate(g, 0)), "draw_rate": r4(g["draws"] / n),
                     "mean_rounds": round(g["rounds"] / n, 2)})
    write_csv("s4_e3_attack_vs_defense.csv", rows)
    names = [b.name for b in e3_builds(4, (1, 6))]
    rank = []
    for dn, _d in E3_DICE:
        for N in Ns:
            for thr in thrs:
                sub = [r for r in rows if r["weapon_dice"] == dn and r["total_levels"] == N
                       and r["act_threshold"] == THR_LABEL[thr]]
                score = {k: [] for k in names}
                for r in sub:
                    score[r["row"]].append(r["row_win_rate"])
                    score[r["col"]].append(1 - r["row_win_rate"])
                rank.append({"weapon_dice": dn, "total_levels": N, "act_threshold": THR_LABEL[thr],
                             **{k: r4(sum(v) / len(v)) for k, v in score.items()},
                             "order": " > ".join(sorted(names, key=lambda k: -sum(score[k]) / len(score[k])))})
    write_csv("s4_e3_ranking.csv", rank)
    write_csv("s4_e3_builds.csv", [{"total_levels": N, "build": b.name, "weapon": b.weapon, "step": b.step,
                                    "dodge": b.dodge, "parry": b.parry}
                                   for N in Ns for b in e3_builds(N, (1, 6))])
    fig, axes = plt.subplots(len(thrs), len(E3_DICE), figsize=(15, 8), sharey=True)
    for ti, thr in enumerate(thrs):
        for di, (dn, _d) in enumerate(E3_DICE):
            ax = axes[ti][di]
            for k, c in zip(names, ("C3", "C0", "C2", "C4")):
                ys = [next(r[k] for r in rank if r["weapon_dice"] == dn and r["total_levels"] == N
                           and r["act_threshold"] == THR_LABEL[thr]) for N in Ns]
                ax.plot(Ns, ys, "o-", color=c, label=k)
            ax.axhline(0.5, color="gray", ls=":")
            ax.set_xticks(Ns)
            ax.set_ylim(0, 1)
            ax.set_xlabel("總等級")
            ax.set_title(f"武器 {dn},門檻 {THR_LABEL[thr]}", fontsize=9)
            if ti == 0 and di == 0:
                ax.legend(fontsize=7)
    axes[0][0].set_ylabel("對其他三種的平均勝率")
    fig.suptitle("E3 攻守貨幣 × 武器軸(散修:單技能 ≤ 4 級,失能即勝)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s4_e3.png"), dpi=115)
    plt.close(fig)
    write_json("s4_summary_e3.json", {"ranking": rank})


# ---------------------------------------------------------------------------
# E8 任意重骰(劍意 / 刀意)燒神識
# ---------------------------------------------------------------------------

def e8(n=10_000):
    cases = (("遊俠", "劍意"), ("鏢師", "刀意"))
    opp = [npc.instantiate(t, "中", CORE, name=t["name"], baseline_dice=BASE_DICE) for t in TPL]
    jobs, meta = [], []
    for tname, yi in cases:
        base = npc.instantiate(TPL_BY[tname], "中", CORE, name=tname, baseline_dice=BASE_DICE)
        variants = {"基準": base, f"+{yi}": replace(base, name=f"{tname}+{yi}", reroll_any=True),
                    "+1級主技能": replace(base, name=f"{tname}+1級", weapon=base.weapon + 1)}
        for thr in (3, INF):
            # 燒神識(Session 4 規則);另列「燒體力、0.2/個」作對照,看池的選擇本身造成多少差別
            for pool_name, kw in (("神識", {}), ("體力0.2(對照)", {"reroll_pool": "stamina"})):
                p = s4_params(act_threshold=thr, **kw)
                for vn, vb in variants.items():
                    if pool_name != "神識" and vn != f"+{yi}":
                        continue
                    for o in opp:
                        jobs.append((vb, replace(o, name=o.name + "(對手)"), p, n, SEED + 8000 + len(jobs)))
                        meta.append((tname, yi, thr, pool_name, vn, o.name))
                if pool_name == "神識":
                    jobs.append((variants[f"+{yi}"], variants["+1級主技能"], p, n, SEED + 8000 + len(jobs)))
                    meta.append((tname, yi, thr, pool_name, f"+{yi} vs +1級主技能", "(直接對決)"))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (tname, yi, thr, pool_name, vn, on), (acc, g) in zip(meta, res):
        a = acc[0]
        rows.append({"base": tname, "reroll_skill": yi, "act_threshold": THR_LABEL[thr],
                     "reroll_pool": pool_name, "variant": vn, "opponent": on,
                     "win_rate": r4(win_rate(g, 0)), "draw_rate": r4(g["draws"] / n),
                     "mean_rounds": round(g["rounds"] / n, 2),
                     "atk_roll_success": r4(div(a["succ"], a["att"])),
                     "crit_rate_per_attack": r4(div(a["crit_roll"], a["att"])),
                     "fumble_rate_per_attack": r4(div(a["fumble"], a["att"])),
                     "attacks_per_fight": r4(a["att"] / n),
                     "sub_fee_stamina_per_fight": r4(a["sub_fee"] / n),
                     "shenshi_spent_per_fight": r4(a["shen_spent"] / n),
                     "shenshi_left_per_fight": r4(a["shen_left"] / n),
                     "stamina_left_per_fight": r4(a["stamina_left"] / n)})
    write_csv("s4_e8_reroll_any.csv", rows)
    cost_rows = []
    for tname, yi in cases:
        w = npc.weapon_entry(TPL_BY[tname])
        L = npc.grade_level(w["level"], "中")
        cost_rows.append({"base": tname, "reroll_skill": yi, "weapon_level": L,
                          "plus_1_level_xp": level_increment(L + 1, 1),
                          "reroll_1級本身_xp(1級成本 × 難度3倍率1.35 × 1/10)": round(subskill_cost(1, 3), 2),
                          "升難度3補差額_xp(已投入 × 0.35)": round(tier_upgrade_diff(L, 1, 3), 2),
                          "升難度3鑽研幅度": 200})
    write_csv("s4_e8_costs.csv", cost_rows)
    write_json("s4_summary_e8.json", {"rows": rows, "costs": cost_rows})
    fig, axes = plt.subplots(1, 3, figsize=(17, 4.8))
    sel = [r for r in rows if r["act_threshold"] == "3" and r["opponent"] != "(直接對決)" and r["reroll_pool"] == "神識"]
    for ax, col, ttl in zip(axes, ("win_rate", "atk_roll_success", "attacks_per_fight"),
                            ("對六種模板中檔的平均勝率", "攻擊擲骰成功率", "每場出手數")):
        w = 0.25
        for ci, (tname, yi) in enumerate(cases):
            for vi, vn in enumerate(("基準", f"+{yi}", "+1級主技能")):
                vals = [r[col] for r in sel if r["base"] == tname and r["variant"] == vn]
                ax.bar(ci + (vi - 1) * w, sum(vals) / len(vals), w, color=("C7", "C1", "C0")[vi])
        ax.set_xticks(range(len(cases)))
        ax.set_xticklabels([f"{c[0]}({c[1]})" for c in cases])
        ax.set_title(ttl, fontsize=10)
    axes[0].legend(["基準", "+重骰(燒神識)", "+1 級"], fontsize=8)
    fig.suptitle("E8 任意重骰燒神識(門檻 3;六種對手 × 1 萬場平均,基準武器 1d6)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s4_e8.png"), dpi=115)
    plt.close(fig)


# ---------------------------------------------------------------------------
# E2 同經驗矩陣(模板;散修基準武器 1d6;副技能發動費 0.2)
# ---------------------------------------------------------------------------

def template_at_budget(t, budget):
    """模板在固定戰鬥經驗下的實例(A48):戰鬥技能依「中」檔累計成本占比分配,上限 4 級,非戰鬥技能不花;
    已持副技能先扣(副技能自身等級成本 × 自身難度倍率 × 1/10,A49)。"""
    ents = npc.combat_skills(t)
    sub_cost = sum(subskill_cost(s["level"], npc.sub_difficulty(e, s, CORE)) for e in ents
                   for s in e.get("subskills", []) if s["name"])
    tot = sum(cum_cost(npc.grade_level(e["level"], "中"), e["tier"]) for e in ents)
    entries = [(e["skill"], e["tier"], cum_cost(npc.grade_level(e["level"], "中"), e["tier"]) / tot)
               for e in ents]
    lv, left = alloc_levels(entries, budget - sub_cost)
    full = {e["skill"]: lv.get(e["skill"], 0) for e in t["skills"]}
    b = npc.instantiate(t, "中", CORE, name=t["name"], levels=full, baseline_dice=BASE_DICE)
    out = [e["skill"] for e in ents if not e["level"][0] <= full[e["skill"]] <= e["level"][1]]
    return b, dict(levels=lv, xp_unspent=round(left, 1), sub_cost=sub_cost, out_of_range=out)


def sub_flow_builds(budget):
    """副技能流(A41):長劍 2 級 + 招架 1 級 + 難度 1 副技能各 1 級(link 兩條、反擊、致命)= 300 + 100 + 40。
    發動策略並列三種:全發動 / 只 link / 只反擊。對照:同樣 2 + 1 級、不持有副技能。"""
    need = cum_cost(2) + cum_cost(1) + 4 * subskill_cost(1)
    if budget < need:
        return None
    held = dict(weapon=2, parry=1, link_w2p=1, link_p2w=1, counter=1, crit_sub=1)
    mk = lambda name, **kw: Build(name, weapon_die=6, weapon_dice=BASE_DICE, **held, **kw)
    flows = [mk("副技能流·全發動"), mk("副技能流·只link", subs_on=("link_w2p", "link_p2w")),
             mk("副技能流·只反擊", subs_on=("counter",))]
    ctrl = Build("主2級·無副技能", weapon=2, parry=1, weapon_die=6, weapon_dice=BASE_DICE)
    return flows, ctrl, dict(xp_unspent=budget - need)


def e2(n=8_000):
    budgets = (300, 1000, 2500)
    variants = (("門檻3", dict(act_threshold=3)), ("門檻∞", dict(act_threshold=INF)))
    rows, builds_rows, summary = [], [], {}
    fig, axes = plt.subplots(len(variants), len(budgets), figsize=(21, 14))
    with ProcessPoolExecutor() as pool:
        for bi, budget in enumerate(budgets):
            builds, names = [], []
            for t in TPL:
                b, info = template_at_budget(t, budget)
                builds.append(b)
                names.append(t["name"])
                builds_rows.append({"budget": budget, "build": t["name"],
                                    "levels": json.dumps(info["levels"], ensure_ascii=False),
                                    "xp_unspent": info["xp_unspent"], "sub_cost": info["sub_cost"],
                                    "out_of_template_range": ",".join(info["out_of_range"]),
                                    "weapon_value": b.value("weapon"), "dodge_value": b.value("dodge"),
                                    "parry_value": b.value("parry"), "weapon_dice": "1d6"})
            sf = sub_flow_builds(budget)
            if sf:
                flows, ctrl, info = sf
                for b in flows + [ctrl]:
                    builds.append(b)
                    names.append(b.name)
                    builds_rows.append({"budget": budget, "build": b.name,
                                        "levels": json.dumps({"長劍": b.weapon, "招架": b.parry}, ensure_ascii=False),
                                        "xp_unspent": info["xp_unspent"] if b is not ctrl else budget - 400,
                                        "weapon_value": b.value("weapon"), "dodge_value": b.value("dodge"),
                                        "parry_value": b.value("parry"), "weapon_dice": "1d6"})
            else:
                builds_rows.append({"budget": budget, "build": "副技能流", "levels": "不可行(長劍 2 級已用盡 300)"})
            for vi, (vn, kw) in enumerate(variants):
                p = s4_params(**kw)
                mt = matrix(builds, p, n, SEED + 700 + 13 * bi + 101 * vi, pool)
                for i in range(len(names)):
                    for j in range(len(names)):
                        if i != j:
                            rows.append({"variant": vn, "budget": budget, "row": names[i], "col": names[j],
                                         "win_rate": r4(mt["M"][i][j]), "draw_rate": r4(mt["D"][i][j]),
                                         "mean_rounds": round(mt["R"][i][j], 2)})
                mono = [names[i] for i in monopoly(mt["M"])]
                avg = {nm: r4(sum(x for j, x in enumerate(r) if j != i) / (len(names) - 1))
                       for i, (nm, r) in enumerate(zip(names, mt["M"]))}
                summary[f"{vn}/{budget}"] = {"names": names, "M": mt["M"], "monopoly_rows": mono, "mean_win": avg}
                heat(axes[vi][bi], mt["M"], names, f"{vn},經驗 {budget}\n全綠列:" + (",".join(mono) or "無"), fs=6)
    fig.suptitle("E2 同經驗矩陣(散修基準武器 1d6,副技能發動費 0.2;列勝率,平手算半)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s4_e2.png"), dpi=100)
    plt.close(fig)
    write_csv("s4_e2_matrix.csv", rows)
    write_csv("s4_e2_builds.csv", builds_rows)
    write_json("s4_summary_e2.json", summary)


if __name__ == "__main__":
    which = sys.argv[1:] or ["e9", "e10", "e3", "e8", "e2"]
    for w in which:
        print("running", w, flush=True)
        globals()[w]()
