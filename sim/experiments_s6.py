"""Session 6 實驗:每回合重擲先攻、預判(戰鬥反應)、無預判者盲防;守方 counter_policy 預設 lead。
跑:E9 高手情境(門檻 ∞,本輪主題:先手優勢是否消失)、E3、E10(分有 / 無戰鬥反應,內力防禦掃「灌滿 / 灌一半」)、
E11(長劍 + 戰鬥反應 1 級 vs 長劍多一級)、先攻屬性 +5 vs +2。
量測:回合起手者勝率、整場先手勝率、每場交換次數、預判使用率與成功率。
用法:python3 sim/experiments_s6.py [e3 e9 e10 e11 init]
輸出到 sim/out/,檔名一律 s6_ 前綴(不覆蓋 Session 1–5)。
"""
from __future__ import annotations

import os
import random
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

sys.path.insert(0, os.path.dirname(__file__))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from experiments_s3 import INF, SEED, THR_LABEL, div, r4, write_csv, write_json
from experiments_s4 import (BASE_DICE, E9_EFFECTS, E9_FEES, E9_SCENARIOS, E10_PAIRS, E10_USES, STAT_FIELDS,
                            e3_builds, e9_build, e9_xp, weapon, win_rate)
from fight import fight
from progression import cum_cost
from resolve import s5_params, s6_params

OUT = os.path.join(os.path.dirname(__file__), "out")
EXTRA_STATS = ("ca_n", "ca_decisions", "ca_unaffordable", "fs_opp", "fs_try", "fs_ok", "blind_n", "blind_miss")
FLOW_FIELDS = ("swap_defend", "swap_miss", "swap_stop", "hold_n", "hold_len_sum", "hold_empty",
               "exchanges", "counter_attacks", "counter_unaffordable")


def vparams(vn: str, **kw):
    """規則變體:s5-lead = Session 5 規則(每場先手每回合起手、守方看得到結果)+ lead;
    s6 = 重擲先攻 + 預判 / 盲防(內力防禦盲防灌滿);-half = 盲防灌一半;+rN = 該變體所有 build 戰鬥反應 N 級。"""
    if vn == "s5-lead":
        return s5_params(counter_policy="lead", **kw)
    return s6_params(blind_qi="half" if vn.endswith("-half") else "full", **kw)


def vreact(vn: str) -> int:
    m = re.search(r"\+r(\d)", vn)
    return int(m.group(1)) if m else 0


def with_react(b, lv: int):
    return replace(b, reaction=lv) if lv else b


# ---------------------------------------------------------------------------
# 共用:一組對戰的彙總(含先攻 / 預判量測)
# ---------------------------------------------------------------------------

def run_pair(args):
    b0, b1, p, n, seed = args
    rng = random.Random(seed)
    acc = [dict.fromkeys(STAT_FIELDS + EXTRA_STATS, 0.0) for _ in (0, 1)]
    for a in acc:
        a.update(rounds=0, qi_left=0.0)
    flow = dict.fromkeys(FLOW_FIELDS, 0)
    g = dict(n=n, wins=[0, 0], draws=0, double_ko=0, stalled=0, rounds=0, decided=0,
             first_holder_wins=0,                           # 整場先手(第 1 回合起手者)獲勝場數
             fh_n=[0, 0], fh_side0_wins=[0, 0],             # 以第 1 回合起手者分組:[side0 起手, side1 起手] 的場數與 side0 勝場
             start_rounds=0, start_rounds_won=0,            # 分出勝負的場次中,回合起手者最後贏的回合數 / 回合數
             start_side0=0, all_rounds=0,                   # side0 起手的回合數 / 全部回合數
             dmg_better=0, dmg_worse=0)                     # 回合起手者該回合讓對手輕傷位掉得比自己多 / 少
    for i in range(n):
        f = fight(b0, b1, p, rng, first=i % 2)
        g["rounds"] += f.rounds
        st = f.flow["starters"]
        g["all_rounds"] += len(st)
        g["start_side0"] += st.count(0)
        for s_, (l0, l1) in zip(st, f.flow["round_dmg"]):
            mine, theirs = (l0, l1) if s_ == 0 else (l1, l0)
            g["dmg_better"] += theirs > mine
            g["dmg_worse"] += theirs < mine
        if f.winner is None:
            g["draws"] += 1
            g["double_ko"] += f.double_ko
            g["stalled"] += f.stalled
        else:
            w = f.winner
            g["wins"][w] += 1
            g["decided"] += 1
            g["first_holder_wins"] += st[0] == w
            g["fh_n"][st[0]] += 1
            g["fh_side0_wins"][st[0]] += w == 0
            g["start_rounds"] += len(st)
            g["start_rounds_won"] += sum(s_ == w for s_ in st)
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
    """先攻 / 主動權 / 預判量測。
    round_starter_win_rate:回合起手者最後贏下整場的比例(以回合計,分出勝負的場次)。
    first_holder_win_rate:整場先手(第 1 回合起手者)勝率;_balanced = 兩種起手各占一半時的勝率(扣掉雙方強弱不同的混淆)。
    round_starter_dmg_edge:回合起手者該回合打掉對手輕傷位比自己多的比例(排除平手)。"""
    n = g["n"]
    sw = flow["swap_defend"] + flow["swap_miss"] + flow["swap_stop"]
    fs_opp = acc[0]["fs_opp"] + acc[1]["fs_opp"]
    fs_try = acc[0]["fs_try"] + acc[1]["fs_try"]
    fs_ok = acc[0]["fs_ok"] + acc[1]["fs_ok"]
    w_a = div(g["fh_side0_wins"][0], g["fh_n"][0])
    w_b = div(g["fh_side0_wins"][1], g["fh_n"][1])
    blind = acc[0]["blind_n"] + acc[1]["blind_n"]
    return {
        "round_starter_win_rate": r4(div(g["start_rounds_won"], g["start_rounds"])),
        "first_holder_win_rate": r4(div(g["first_holder_wins"], g["decided"])),
        "first_holder_win_rate_balanced": r4(0.5 + (w_a - w_b) / 2),
        "round_starter_dmg_edge": r4(div(g["dmg_better"], g["dmg_better"] + g["dmg_worse"])),
        "starter_share_side0": r4(div(g["start_side0"], g["all_rounds"])),
        "swaps_per_fight": r4(sw / n), "swaps_defend_per_fight": r4(flow["swap_defend"] / n),
        "swaps_miss_per_fight": r4(flow["swap_miss"] / n), "swaps_stop_per_fight": r4(flow["swap_stop"] / n),
        "exchanges_per_fight": r4(flow["exchanges"] / n),
        "counter_attack_rate": r4(div(acc[0]["ca_n"] + acc[1]["ca_n"], acc[0]["ca_decisions"] + acc[1]["ca_decisions"])),
        "foresight_use_rate": r4(div(fs_try, fs_opp)),            # 預判使用率 = 實際發動 / 被攻擊時握有戰鬥反應的次數
        "foresight_success_rate": r4(div(fs_ok, fs_try)),         # 成功率 = 看得到 / 發動
        "foresight_per_fight": r4(fs_try / n),
        "blind_miss_rate": r4(div(acc[0]["blind_miss"] + acc[1]["blind_miss"], blind)),   # 盲防的防禦鏈白付(攻方落空)的比例
        "mean_rounds": round(g["rounds"] / n, 2), "draw_rate": r4(g["draws"] / n),
    }


def pool_run(jobs):
    with ProcessPoolExecutor() as pool:
        return list(pool.map(run_pair, jobs))


# ---------------------------------------------------------------------------
# E9 高手情境(門檻 ∞):先手優勢是否消失
# ---------------------------------------------------------------------------

E9_VARIANTS = ("s5-lead", "s6", "s6+r1", "s6+r3")


def e9(n=6000):
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
            r = vreact(vn)
            for eff in E9_EFFECTS:
                for fee in E9_FEES:
                    p = vparams(vn, act_threshold=INF, link_bonus=eff, sub_fee=fee)
                    for tag, a, b in (("X_vs_X0", B["X"], B["X0"]), ("F_vs_M", B["F"], B["M"]),
                                      ("X_mirror", B["X"], B["X"])):
                        jobs.append((with_react(a, r), with_react(b, r), p, n, SEED + 11000 + len(jobs)))
                        meta.append((sc, vn, eff, fee, tag))
    res = pool_run(jobs)
    cells = {}
    for (sc, vn, eff, fee, tag), r in zip(meta, res):
        cells.setdefault((sc, vn, eff, fee), {})[tag] = r
    rows = []
    for (sc, vn, eff, fee), d in cells.items():
        (ax, gx, fx), (af, gf, ff), (am, gm, fm) = d["X_vs_X0"], d["F_vs_M"], d["X_mirror"]
        xp = builds[sc]["xp"]
        cx, cf, cm = flow_cols(gx, fx, ax), flow_cols(gf, ff, af), flow_cols(gm, fm, am)
        row = {"scenario": sc, "variant": vn, "link_effect_per_level": eff, "sub_fee": fee,
               "activation_rate": r4(div(ax[0]["sub_active"], ax[0]["sub_offered"])),
               "win_with_vs_without_subs": r4(win_rate(gx, 0)),
               "win_sub_flow_vs_main_flow": r4(win_rate(gf, 0))}
        for tag, c in (("mirror", cm), ("X_vs_X0", cx), ("F_vs_M", cf)):
            for k in ("round_starter_win_rate", "first_holder_win_rate", "first_holder_win_rate_balanced",
                      "round_starter_dmg_edge", "swaps_per_fight", "exchanges_per_fight", "mean_rounds",
                      "foresight_use_rate", "foresight_success_rate", "blind_miss_rate", "draw_rate"):
                row[f"{k}_{tag}"] = c[k]
        row.update(xp_sub_flow=round(xp["F"], 1), xp_main_flow=round(xp["M"], 1),
                   xp_with_subs=round(xp["X"], 1), xp_without_subs=round(xp["X0"], 1))
        rows.append(row)
    write_csv("s6_e9_sub_sweep.csv", rows)
    write_json("s6_summary_e9.json", {"rows": rows})
    fig, axes = plt.subplots(1, len(scen), figsize=(7 * len(scen), 4.5), sharey=True)
    for ax, (sc, *_x) in zip(axes, scen):
        xs = range(len(E9_VARIANTS))
        for col, c, lb in (("first_holder_win_rate_mirror", "C3", "整場先手勝率(鏡像)"),
                           ("round_starter_win_rate_mirror", "C0", "回合起手者勝率(鏡像)"),
                           ("round_starter_dmg_edge_mirror", "C2", "回合起手者該回合傷害優勢(鏡像)")):
            ys = [sum(r[col] for r in rows if r["scenario"] == sc and r["variant"] == vn) /
                  sum(1 for r in rows if r["scenario"] == sc and r["variant"] == vn) for vn in E9_VARIANTS]
            ax.plot(xs, ys, "o-", color=c, label=lb)
        ax.axhline(0.5, color="gray", ls=":")
        ax.set_xticks(list(xs))
        ax.set_xticklabels(E9_VARIANTS)
        ax.set_title(f"{sc}(門檻 ∞,九格平均)")
        ax.legend(fontsize=7)
    axes[0].set_ylabel("機率")
    fig.suptitle("E9 高手情境:先手優勢(鏡像對戰,同 build 互打)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s6_e9.png"), dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
# E3 攻守貨幣
# ---------------------------------------------------------------------------

E3_VARIANTS = ("s5-lead", "s6", "s6+r1")


def e3(n=4_000):
    thrs, Ns = (3, INF), (3, 4, 6)
    jobs, meta = [], []
    for vn in E3_VARIANTS:
        r = vreact(vn)
        for N in Ns:
            builds = [with_react(b, r) for b in e3_builds(N, BASE_DICE)]
            for thr in thrs:
                p = vparams(vn, act_threshold=thr)
                for i in range(len(builds)):
                    for j in range(i + 1, len(builds)):
                        jobs.append((builds[i], builds[j], p, n, SEED + 13000 + len(jobs)))
                        meta.append((vn, N, thr, builds[i].name, builds[j].name))
    res = pool_run(jobs)
    rows = []
    for (vn, N, thr, a, b), (acc, g, flow) in zip(meta, res):
        rows.append({"variant": vn, "total_levels": N, "act_threshold": THR_LABEL[thr], "row": a, "col": b,
                     "row_win_rate": r4(win_rate(g, 0)), **flow_cols(g, flow, acc)})
    write_csv("s6_e3_attack_vs_defense.csv", rows)
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
                agg = {k: r4(sum(x[k] for x in sub) / len(sub))
                       for k in ("round_starter_win_rate", "first_holder_win_rate", "first_holder_win_rate_balanced",
                                 "round_starter_dmg_edge", "swaps_per_fight", "exchanges_per_fight",
                                 "foresight_use_rate", "foresight_success_rate", "blind_miss_rate",
                                 "mean_rounds", "draw_rate")}
                rank.append({"variant": vn, "total_levels": N, "act_threshold": THR_LABEL[thr],
                             **{k: r4(sum(v) / len(v)) for k, v in score.items()},
                             "order": " > ".join(sorted(names, key=lambda k: -sum(score[k]) / len(score[k]))), **agg})
    write_csv("s6_e3_ranking.csv", rank)
    fig, axes = plt.subplots(len(thrs), len(Ns), figsize=(15, 8), sharey=True)
    for ti, thr in enumerate(thrs):
        for ni, N in enumerate(Ns):
            ax = axes[ti][ni]
            for bi, (k, c) in enumerate(zip(names, ("C3", "C0", "C2", "C4"))):
                ys = [next(r[k] for r in rank if r["variant"] == vn and r["total_levels"] == N
                           and r["act_threshold"] == THR_LABEL[thr]) for vn in E3_VARIANTS]
                ax.bar([x + (bi - 1.5) * 0.2 for x in range(len(E3_VARIANTS))], ys, 0.2, color=c, label=k)
            ax.axhline(0.5, color="gray", ls=":")
            ax.set_xticks(range(len(E3_VARIANTS)))
            ax.set_xticklabels(E3_VARIANTS)
            ax.set_ylim(0, 1)
            ax.set_title(f"總等級 {N},門檻 {THR_LABEL[thr]}", fontsize=9)
            if ti == 0 and ni == 0:
                ax.legend(fontsize=7)
    axes[0][0].set_ylabel("對其他三種的平均勝率")
    fig.suptitle("E3 攻守貨幣:Session 5 規則 vs 重擲先攻 + 盲防(+r1 = 全員戰鬥反應 1 級)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s6_e3.png"), dpi=110)
    plt.close(fig)
    write_json("s6_summary_e3.json", {"ranking": rank})


# ---------------------------------------------------------------------------
# E10 內功 CP 值:分有 / 無戰鬥反應;內力防禦的盲防策略掃「灌滿 / 灌一半」
# ---------------------------------------------------------------------------

E10_VARIANTS = ("s5-lead", "s6", "s6-half", "s6+r1", "s6+r1-half")


def e10(n=6_000):
    thrs = (3, INF)
    jobs, meta = [], []
    for pname, ng, wl, ctrl in E10_PAIRS:
        for uname, use in E10_USES:
            qi = weapon(wl, name="內功流", neigong=ng, neili_use=use)
            ref_same, ref_up = weapon(wl, name="同級無內功"), weapon(ctrl, name="多一級武器")
            for vn in E10_VARIANTS:
                r = vreact(vn)
                for thr in thrs:
                    p = vparams(vn, act_threshold=thr)
                    for tag, b in (("vs_多一級武器", ref_up), ("vs_同級無內功", ref_same)):
                        jobs.append((with_react(qi, r), with_react(b, r), p, n, SEED + 15000 + len(jobs)))
                        meta.append((pname, ng, wl, ctrl, uname, vn, thr, tag))
    res = pool_run(jobs)
    rows = []
    for (pname, ng, wl, ctrl, uname, vn, thr, tag), (acc, g, flow) in zip(meta, res):
        a = acc[0]
        rows.append({"pair": pname, "neili_use": uname, "variant": vn, "act_threshold": THR_LABEL[thr],
                     "opponent": tag, "win_rate": r4(win_rate(g, 0)),
                     "qi_spent_per_fight": r4((a["qi_atk_pts"] + a["qi_def_pts"] + a["qi_heal_pts"]) / n),
                     "attack_bonus_dmg_per_fight": r4(a["qi_atk_dmg"] / n),
                     "defense_pts_per_fight": r4(a["qi_def_pts"] / n),
                     "defense_absorbed_per_fight": r4(a["qi_def_abs"] / n),
                     "heal_amount_per_fight": r4(a["qi_heal_amt"] / n),
                     "xp_internal_side": cum_cost(wl) + cum_cost(ng) + (cum_cost(vreact(vn)) if vreact(vn) else 0),
                     "xp_opponent": (cum_cost(ctrl) if tag == "vs_多一級武器" else cum_cost(wl))
                     + (cum_cost(vreact(vn)) if vreact(vn) else 0),
                     **flow_cols(g, flow, acc)})
    write_csv("s6_e10_neigong_cp.csv", rows)
    write_json("s6_summary_e10.json", {"rows": rows})
    fig, axes = plt.subplots(2, 2, figsize=(16, 9), sharey=True)
    colors = ("C7", "C0", "C9", "C3", "C1")
    for ri, tag in enumerate(("vs_多一級武器", "vs_同級無內功")):
        for ci, thr in enumerate(thrs):
            ax = axes[ri][ci]
            xs, ticks, labels = 0, [], []
            for pname, *_x in E10_PAIRS:
                for ui, (uname, _u) in enumerate(E10_USES):
                    for vi, vn in enumerate(E10_VARIANTS):
                        r = next(r for r in rows if r["pair"] == pname and r["neili_use"] == uname
                                 and r["variant"] == vn and r["act_threshold"] == THR_LABEL[thr] and r["opponent"] == tag)
                        ax.bar(xs + vi * 0.16, r["win_rate"], 0.16, color=colors[vi],
                               label=vn if (pname == E10_PAIRS[0][0] and ui == 0) else None)
                    ticks.append(xs + 0.32)
                    labels.append(f"{pname[:5]}\n{uname}")
                    xs += 1.1
            ax.axhline(0.5, color="gray", ls=":")
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels, fontsize=6)
            ax.set_title(f"{tag},門檻 {THR_LABEL[thr]}", fontsize=9)
            if ri == 0 and ci == 0:
                ax.legend(fontsize=7)
    axes[0][0].set_ylabel("內功流勝率")
    fig.suptitle("E10 內功 CP 值:Session 5 規則 / 無戰鬥反應 / 雙方戰鬥反應 1 級 × 盲防內力灌滿 / 灌一半")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s6_e10.png"), dpi=105)
    plt.close(fig)


# ---------------------------------------------------------------------------
# E11 戰鬥反應的 CP 值:長劍 L + 戰鬥反應 1 級 vs 長劍 L+1(同級無戰鬥反應當對照;只吃先攻不預判另列)
# ---------------------------------------------------------------------------

def e11(n=8_000):
    thrs, Ls = (3, INF), (1, 2, 3, 4)
    jobs, meta = [], []
    for L in Ls:
        full = weapon(L, name="長劍+戰鬥反應1", reaction=1)
        init_only = weapon(L, name="長劍+戰鬥反應1(只先攻)", reaction=1, foresight=False)
        for thr in thrs:
            p = vparams("s6", act_threshold=thr)
            for aname, a in (("先攻+預判", full), ("只先攻", init_only)):
                for tag, b in (("vs_長劍多一級", weapon(L + 1, name="長劍多一級")),
                               ("vs_同級無戰鬥反應", weapon(L, name="同級無戰鬥反應"))):
                    jobs.append((a, b, p, n, SEED + 17000 + len(jobs)))
                    meta.append((L, thr, aname, tag))
    res = pool_run(jobs)
    rows = []
    for (L, thr, aname, tag), (acc, g, flow) in zip(meta, res):
        rows.append({"weapon_level": L, "act_threshold": THR_LABEL[thr], "reaction_side": aname, "opponent": tag,
                     "win_rate": r4(win_rate(g, 0)),
                     "xp_reaction_side": cum_cost(L) + cum_cost(1),
                     "xp_opponent": cum_cost(L + 1) if tag == "vs_長劍多一級" else cum_cost(L),
                     "reaction_side_starter_share": r4(div(g["start_side0"], g["all_rounds"])),
                     **flow_cols(g, flow, acc)})
    write_csv("s6_e11_reaction_cp.csv", rows)
    write_json("s6_summary_e11.json", {"rows": rows})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, tag in zip(axes, ("vs_長劍多一級", "vs_同級無戰鬥反應")):
        for ai, (aname, c) in enumerate((("先攻+預判", "C3"), ("只先攻", "C0"))):
            for ti, thr in enumerate(thrs):
                ys = [next(r["win_rate"] for r in rows if r["weapon_level"] == L and r["act_threshold"] == THR_LABEL[thr]
                           and r["reaction_side"] == aname and r["opponent"] == tag) for L in Ls]
                ax.plot(Ls, ys, "o" + ("-" if ti == 0 else "--"), color=c, label=f"{aname} 門檻{THR_LABEL[thr]}")
        ax.axhline(0.5, color="gray", ls=":")
        ax.set_xlabel("長劍等級 L(戰鬥反應側 L + 反應 1 級)")
        ax.set_title(tag)
        ax.legend(fontsize=7)
    axes[0].set_ylabel("戰鬥反應側勝率")
    fig.suptitle("E11 戰鬥反應 1 級 vs 長劍多一級(經驗不相等,見 CSV xp 欄)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s6_e11.png"), dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 先攻屬性 +5 vs +2
# ---------------------------------------------------------------------------

INIT_VARIANTS = ("s6", "s6+r1")


def init(n=8_000):
    thrs, Ls = (3, INF), (1, 2, 3)
    jobs, meta = [], []
    for L in Ls:
        for vn in INIT_VARIANTS:
            r = vreact(vn)
            for thr in thrs:
                p = vparams(vn, act_threshold=thr)
                for tag, ea, eb in (("先攻+5 vs +2", 5, 2), ("對照 +2 vs +2", 2, 2)):
                    jobs.append((with_react(weapon(L, name="甲", init_eff=ea), r), with_react(weapon(L, name="乙", init_eff=eb), r),
                                 p, n, SEED + 19000 + len(jobs)))
                    meta.append((L, vn, thr, tag))
    res = pool_run(jobs)
    rows = []
    for (L, vn, thr, tag), (acc, g, flow) in zip(meta, res):
        rows.append({"weapon_level": L, "variant": vn, "act_threshold": THR_LABEL[thr], "matchup": tag,
                     "win_rate_side_a": r4(win_rate(g, 0)), **flow_cols(g, flow, acc)})
    write_csv("s6_init_attr.csv", rows)
    write_json("s6_summary_init.json", {"rows": rows})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for ax, (col, ttl) in zip(axes, (("win_rate_side_a", "甲(先攻 +5)勝率"), ("starter_share_side0", "甲起手的回合比例"))):
        for vi, vn in enumerate(INIT_VARIANTS):
            for ti, thr in enumerate(thrs):
                ys = [next(r[col] for r in rows if r["weapon_level"] == L and r["variant"] == vn
                           and r["act_threshold"] == THR_LABEL[thr] and r["matchup"] == "先攻+5 vs +2") for L in Ls]
                ax.plot(Ls, ys, "o" + ("-" if ti == 0 else "--"), color=("C3", "C0")[vi], label=f"{vn} 門檻{THR_LABEL[thr]}")
        ax.axhline(0.5, color="gray", ls=":")
        ax.set_xlabel("長劍等級")
        ax.set_title(ttl)
        ax.legend(fontsize=7)
    fig.suptitle("先攻屬性效益值 +5 vs +2")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s6_init.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    which = sys.argv[1:] or ["e9", "e3", "e10", "e11", "init"]
    for w in which:
        print("running", w, flush=True)
        globals()[w]()
