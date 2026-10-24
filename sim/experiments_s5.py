"""Session 5 實驗:主動權交換(取代雙方各一手番)× 守方 counter_policy(never / always / lead)。
重跑 E3(全押攻排序是否改變——本輪主題)、E9 高手情境(門檻 ∞:先手決勝是否消失)、E10(內功 CP 值)。
每個實驗都並列 Session 4 的舊手番規則('turns')作對照,另加量測:每場主動權交換次數、平均連續出手長度、對攻發生率。
用法:python3 sim/experiments_s5.py [e3 e9 e10]
輸出到 sim/out/,檔名一律 s5_ 前綴(不覆蓋 Session 1–4)。
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from experiments_s3 import INF, SEED, THR_LABEL, div, r4, write_csv, write_json
from experiments_s4 import (BASE_DICE, E9_EFFECTS, E9_FEES, E9_SCENARIOS, E10_PAIRS, E10_USES, STAT_FIELDS,
                            e3_builds, e9_build, e9_xp, weapon, win_rate)
from fight import fight, neili_cap, neili_level, neili_pool
from progression import cum_cost
from resolve import s4_params, s5_params

OUT = os.path.join(os.path.dirname(__file__), "out")
POLICIES = ("never", "always", "lead")
FLOW_FIELDS = ("swap_defend", "swap_miss", "swap_stop", "hold_n", "hold_len_sum", "hold_empty",
               "exchanges", "counter_attacks", "counter_unaffordable")


def variant_params(vname: str, **kw):
    """規則變體:turns = Session 4 舊手番規則;swap-<policy> = 主動權交換 + 守方對攻策略;
    敏感度:swap-never+dodge_any(閃避擲骰成功也算防禦成功)、swap-always+keep(對攻後攻方一律保有主動)。"""
    if vname == "turns":
        return s4_params(**kw)
    pol = vname.split("-")[1].split("+")[0]
    extra = {}
    if vname.endswith("+dodge_any"):
        extra["dodge_swap"] = "any"
    if vname.endswith("+keep"):
        extra["counter_initiative"] = "keep"
    return s5_params(counter_policy=pol, **extra, **kw)


# ---------------------------------------------------------------------------
# 共用:一組對戰的彙總(附主動權量測)
# ---------------------------------------------------------------------------

def run_pair(args):
    b0, b1, p, n, seed = args
    rng = random.Random(seed)
    acc = [dict.fromkeys(STAT_FIELDS + ("ca_n", "ca_decisions", "ca_unaffordable"), 0.0) for _ in (0, 1)]
    for a in acc:
        a.update(rounds=0, qi_left=0.0)
    flow = dict.fromkeys(FLOW_FIELDS, 0)
    g = dict(n=n, wins=[0, 0], draws=0, double_ko=0, stalled=0, rounds=0, first_mover_wins=0, decided=0)
    for i in range(n):
        first = i % 2
        f = fight(b0, b1, p, rng, first=first)
        g["rounds"] += f.rounds
        if f.winner is None:
            g["draws"] += 1
            g["double_ko"] += f.double_ko
            g["stalled"] += f.stalled
        else:
            g["wins"][f.winner] += 1
            g["decided"] += 1
            g["first_mover_wins"] += f.winner == first
        if f.flow:
            for k in FLOW_FIELDS:
                flow[k] += f.flow[k]
        for k, s in enumerate(f.states):
            a = acc[k]
            for fld in a:
                if hasattr(s.st, fld):
                    a[fld] += getattr(s.st, fld)
            a["rounds"] += f.rounds
            a["qi_left"] += s.qi
    return acc, g, flow


def flow_cols(g, flow, acc):
    """主動權量測:每場交換次數(依成因)、持主動期間平均連續出手長度、對攻發生率、先手勝率。"""
    n = g["n"]
    sw = flow["swap_defend"] + flow["swap_miss"] + flow["swap_stop"]
    dec = acc[0]["ca_decisions"] + acc[1]["ca_decisions"]
    return {
        "swaps_per_fight": r4(sw / n), "swaps_defend_per_fight": r4(flow["swap_defend"] / n),
        "swaps_miss_per_fight": r4(flow["swap_miss"] / n), "swaps_stop_per_fight": r4(flow["swap_stop"] / n),
        "swaps_per_round": r4(div(sw, g["rounds"])),
        "exchanges_per_fight": r4(flow["exchanges"] / n),
        "mean_hold_length": r4(div(flow["hold_len_sum"], flow["hold_n"])),
        "empty_holds_per_fight": r4(flow["hold_empty"] / n),
        "counter_attack_rate": r4(div(acc[0]["ca_n"] + acc[1]["ca_n"], dec)),
        "counter_unaffordable_rate": r4(div(acc[0]["ca_unaffordable"] + acc[1]["ca_unaffordable"], dec)),
        "first_mover_win_rate": r4(div(g["first_mover_wins"], g["decided"])),
        "mean_rounds": round(g["rounds"] / n, 2), "draw_rate": r4(g["draws"] / n),
        "double_ko_rate": r4(g["double_ko"] / n),
    }


# ---------------------------------------------------------------------------
# E3 攻守貨幣
# ---------------------------------------------------------------------------

E3_VARIANTS = ("turns", "swap-never", "swap-always", "swap-lead", "swap-never+dodge_any", "swap-always+keep")


def e3(n=4_000):
    thrs, Ns = (3, INF), (3, 4, 6)
    jobs, meta = [], []
    for vn in E3_VARIANTS:
        for N in Ns:
            builds = e3_builds(N, BASE_DICE)
            for thr in thrs:
                p = variant_params(vn, act_threshold=thr)
                for i in range(len(builds)):
                    for j in range(i + 1, len(builds)):
                        jobs.append((builds[i], builds[j], p, n, SEED + 5000 + len(jobs)))
                        meta.append((vn, N, thr, builds[i].name, builds[j].name))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (vn, N, thr, a, b), (acc, g, flow) in zip(meta, res):
        rows.append({"variant": vn, "total_levels": N, "act_threshold": THR_LABEL[thr], "row": a, "col": b,
                     "row_win_rate": r4(win_rate(g, 0)), **flow_cols(g, flow, acc)})
    write_csv("s5_e3_attack_vs_defense.csv", rows)
    names = [b.name for b in e3_builds(4, BASE_DICE)]
    rank = []
    for vn in E3_VARIANTS:
        for N in Ns:
            for thr in thrs:
                sub = [r for r in rows if r["variant"] == vn and r["total_levels"] == N
                       and r["act_threshold"] == THR_LABEL[thr]]
                score = {k: [] for k in names}
                for r in sub:
                    score[r["row"]].append(r["row_win_rate"])
                    score[r["col"]].append(1 - r["row_win_rate"])
                agg = {k: (r4(sum(sub_r[k] for sub_r in sub) / len(sub)) if all(k in sub_r for sub_r in sub) else None)
                       for k in ("swaps_per_fight", "mean_hold_length", "counter_attack_rate", "first_mover_win_rate",
                                 "mean_rounds", "draw_rate")}
                rank.append({"variant": vn, "total_levels": N, "act_threshold": THR_LABEL[thr],
                             **{k: r4(sum(v) / len(v)) for k, v in score.items()},
                             "order": " > ".join(sorted(names, key=lambda k: -sum(score[k]) / len(score[k]))), **agg})
    write_csv("s5_e3_ranking.csv", rank)
    fig, axes = plt.subplots(len(thrs), len(Ns), figsize=(17, 8), sharey=True)
    for ti, thr in enumerate(thrs):
        for ni, N in enumerate(Ns):
            ax = axes[ti][ni]
            for bi, (k, c) in enumerate(zip(names, ("C3", "C0", "C2", "C4"))):
                ys = [next(r[k] for r in rank if r["variant"] == vn and r["total_levels"] == N
                           and r["act_threshold"] == THR_LABEL[thr]) for vn in E3_VARIANTS]
                ax.bar([x + (bi - 1.5) * 0.2 for x in range(len(E3_VARIANTS))], ys, 0.2, color=c, label=k)
            ax.axhline(0.5, color="gray", ls=":")
            ax.set_xticks(range(len(E3_VARIANTS)))
            ax.set_xticklabels(E3_VARIANTS, rotation=40, ha="right", fontsize=7)
            ax.set_ylim(0, 1)
            ax.set_title(f"總等級 {N},門檻 {THR_LABEL[thr]}", fontsize=9)
            if ti == 0 and ni == 0:
                ax.legend(fontsize=7)
    axes[0][0].set_ylabel("對其他三種的平均勝率")
    fig.suptitle("E3 攻守貨幣:主動權交換 × 對攻策略(turns = Session 4 舊手番)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s5_e3.png"), dpi=110)
    plt.close(fig)
    write_json("s5_summary_e3.json", {"ranking": rank})


# ---------------------------------------------------------------------------
# E9 高手情境(門檻 ∞)
# ---------------------------------------------------------------------------

E9_VARIANTS = ("turns", "swap-never", "swap-always", "swap-lead")


def e9(n=8000):
    scen = [s for s in E9_SCENARIOS if s[0] != "散修互毆"]
    builds = {}
    for sc, tier, sl, m in scen:
        W, P = m["weapon"], m["parry"]
        builds[sc] = dict(F=e9_build("副技能流", tier, W - 1, P, sl), M=e9_build("主技能流", tier, W, P, 0),
                          X=e9_build("有副技能", tier, W, P, sl), X0=e9_build("無副技能", tier, W, P, 0),
                          xp=dict(F=e9_xp(tier, W - 1, P, sl), M=e9_xp(tier, W, P, 0),
                                  X=e9_xp(tier, W, P, sl), X0=e9_xp(tier, W, P, 0)))
    jobs, meta = [], []
    for sc, tier, sl, m in scen:
        B = builds[sc]
        for vn in E9_VARIANTS:
            for eff in E9_EFFECTS:
                for fee in E9_FEES:
                    p = variant_params(vn, act_threshold=INF, link_bonus=eff, sub_fee=fee)
                    for tag, a, b in (("X_vs_X0", B["X"], B["X0"]), ("F_vs_M", B["F"], B["M"])):
                        jobs.append((a, b, p, n, SEED + 9500 + len(jobs)))
                        meta.append((sc, vn, eff, fee, tag))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    cells = {}
    for (sc, vn, eff, fee, tag), r in zip(meta, res):
        cells.setdefault((sc, vn, eff, fee), {})[tag] = r
    rows = []
    for (sc, vn, eff, fee), d in cells.items():
        (ax, gx, fx), (af, gf, ff) = d["X_vs_X0"], d["F_vs_M"]
        a0 = ax[0]
        xp = builds[sc]["xp"]
        fc = flow_cols(gx, fx, ax)
        rows.append({
            "scenario": sc, "variant": vn, "link_effect_per_level": eff, "sub_fee": fee,
            "activation_rate": r4(div(a0["sub_active"], a0["sub_offered"])),
            "win_with_vs_without_subs": r4(win_rate(gx, 0)),
            "win_sub_flow_vs_main_flow": r4(win_rate(gf, 0)),
            "first_mover_win_rate_X_vs_X0": fc["first_mover_win_rate"],
            "first_mover_win_rate_F_vs_M": flow_cols(gf, ff, af)["first_mover_win_rate"],
            "mean_rounds_X_vs_X0": fc["mean_rounds"], "mean_rounds_F_vs_M": flow_cols(gf, ff, af)["mean_rounds"],
            "swaps_per_fight": fc["swaps_per_fight"], "swaps_defend_per_fight": fc["swaps_defend_per_fight"],
            "swaps_miss_per_fight": fc["swaps_miss_per_fight"], "swaps_stop_per_fight": fc["swaps_stop_per_fight"],
            "exchanges_per_fight": fc["exchanges_per_fight"], "mean_hold_length": fc["mean_hold_length"],
            "counter_attack_rate": fc["counter_attack_rate"], "draw_rate": fc["draw_rate"],
            "double_ko_rate": fc["double_ko_rate"],
            "xp_sub_flow": round(xp["F"], 1), "xp_main_flow": round(xp["M"], 1),
            "xp_with_subs": round(xp["X"], 1), "xp_without_subs": round(xp["X0"], 1),
        })
    write_csv("s5_e9_sub_sweep.csv", rows)
    write_json("s5_summary_e9.json", {"rows": rows})
    fig, axes = plt.subplots(2, len(scen) * len(E9_VARIANTS), figsize=(3.4 * len(scen) * len(E9_VARIANTS), 7))
    ci = 0
    for sc, *_x in scen:
        for vn in E9_VARIANTS:
            for ri, col, ttl in ((0, "win_with_vs_without_subs", "有 vs 無"), (1, "win_sub_flow_vs_main_flow", "副技能流 vs 主技能流")):
                M = [[next(r[col] for r in rows if r["scenario"] == sc and r["variant"] == vn
                           and r["link_effect_per_level"] == eff and r["sub_fee"] == fee) for fee in E9_FEES]
                     for eff in E9_EFFECTS]
                ax = axes[ri][ci]
                ax.imshow(M, cmap="RdYlGn", vmin=0, vmax=1)
                ax.set_xticks(range(3))
                ax.set_xticklabels([f"費{f}" for f in E9_FEES], fontsize=7)
                ax.set_yticks(range(3))
                ax.set_yticklabels([f"+{e}" for e in E9_EFFECTS], fontsize=7)
                for i in range(3):
                    for j in range(3):
                        ax.text(j, i, f"{M[i][j]:.2f}", ha="center", va="center", fontsize=7)
                ax.set_title(f"{sc}\n{vn}\n{ttl}", fontsize=7)
            ci += 1
    fig.suptitle("E9 高手情境(門檻 ∞)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s5_e9.png"), dpi=105)
    plt.close(fig)


# ---------------------------------------------------------------------------
# E10 內功 CP 值
# ---------------------------------------------------------------------------

E10_VARIANTS = ("turns", "swap-never", "swap-always", "swap-lead")


def e10(n=6_000):
    thrs = (3, INF)
    jobs, meta = [], []
    for pname, ng, wl, ctrl in E10_PAIRS:
        for uname, use in E10_USES:
            qi = weapon(wl, name="內功流", neigong=ng, neili_use=use)
            ref_same, ref_up = weapon(wl, name="同級無內功"), weapon(ctrl, name="多一級武器")
            for vn in E10_VARIANTS:
                for thr in thrs:
                    p = variant_params(vn, act_threshold=thr)
                    for tag, b in (("vs_多一級武器", ref_up), ("vs_同級無內功", ref_same)):
                        jobs.append((qi, b, p, n, SEED + 10500 + len(jobs)))
                        meta.append((pname, ng, wl, ctrl, uname, vn, thr, tag))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (pname, ng, wl, ctrl, uname, vn, thr, tag), (acc, g, flow) in zip(meta, res):
        a = acc[0]
        rows.append({"pair": pname, "neili_use": uname, "variant": vn, "act_threshold": THR_LABEL[thr],
                     "opponent": tag, "win_rate": r4(win_rate(g, 0)),
                     "qi_spent_per_fight": r4((a["qi_atk_pts"] + a["qi_def_pts"] + a["qi_heal_pts"]) / n),
                     "attack_bonus_dmg_per_fight": r4(a["qi_atk_dmg"] / n),
                     "defense_absorbed_per_fight": r4(a["qi_def_abs"] / n),
                     "heal_amount_per_fight": r4(a["qi_heal_amt"] / n),
                     "xp_internal_side": cum_cost(wl) + cum_cost(ng),
                     "xp_opponent": cum_cost(ctrl) if tag == "vs_多一級武器" else cum_cost(wl),
                     **flow_cols(g, flow, acc)})
    write_csv("s5_e10_neigong_cp.csv", rows)
    write_json("s5_summary_e10.json", {"rows": rows})
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), sharey=True)
    for ri, tag in enumerate(("vs_多一級武器", "vs_同級無內功")):
        for ci, thr in enumerate(thrs):
            ax = axes[ri][ci]
            xs = 0
            ticks, labels = [], []
            for pname, *_x in E10_PAIRS:
                for ui, (uname, _u) in enumerate(E10_USES):
                    for vi, vn in enumerate(E10_VARIANTS):
                        r = next(r for r in rows if r["pair"] == pname and r["neili_use"] == uname
                                 and r["variant"] == vn and r["act_threshold"] == THR_LABEL[thr] and r["opponent"] == tag)
                        ax.bar(xs + vi * 0.2, r["win_rate"], 0.2, color=("C7", "C0", "C3", "C2")[vi],
                               label=vn if (pname == E10_PAIRS[0][0] and ui == 0) else None)
                    ticks.append(xs + 0.3)
                    labels.append(f"{pname[:5]}\n{uname}")
                    xs += 1.2
            ax.axhline(0.5, color="gray", ls=":")
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels, fontsize=6)
            ax.set_title(f"{tag},門檻 {THR_LABEL[thr]}", fontsize=9)
            if ri == 0 and ci == 0:
                ax.legend(fontsize=7)
    axes[0][0].set_ylabel("內功流勝率")
    fig.suptitle("E10 內功 CP 值:規則變體 × 內力用法")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s5_e10.png"), dpi=105)
    plt.close(fig)


if __name__ == "__main__":
    which = sys.argv[1:] or ["e3", "e9", "e10"]
    for w in which:
        print("running", w, flush=True)
        globals()[w]()
