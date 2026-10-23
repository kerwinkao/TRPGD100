"""Session 2 實驗:E1 消耗格與喘點、E2 等經驗配置矩陣、E3 攻守貨幣、E7 高手互打。
用法:python3 sim/experiments_s2.py [e1 e2 e3 e7]
輸出到 sim/out/,檔名一律 s2_ 前綴(不覆蓋 Session 1)。
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
from matplotlib import font_manager

from fight import Build, fight
from resolve import Params, UsageLog

OUT = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT, exist_ok=True)

for _f in ("WenQuanYi Zen Hei", "Noto Sans CJK TC", "Noto Sans CJK JP"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        break
plt.rcParams["axes.unicode_minus"] = False

SEED = 20261023
INF = math.inf
THR_LABEL = {2: "2", 3: "3", 5: "5", INF: "∞"}
CATS = ("crit_maxdie", "through_parry", "no_guard")
STAT_FIELDS = ("att", "succ", "crit_roll", "fumble", "thru", "dmg_hit", "dmg_total", "full_dodged",
               "parry_try", "parry_ok", "block_try", "block_ok", "step_try", "step_ok", "step_sum",
               "step_skipped", "acts", "atk_acts", "def_acts", "faced", "dodge_try",
               "refuse_n", "winded_n")


def write_csv(name, rows):
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def write_json(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def div(a, b):
    return a / b if b else float("nan")


def r4(x):
    return round(x, 4) if x == x else x


# ---------------------------------------------------------------------------
# 共用:一組對戰的彙總
# ---------------------------------------------------------------------------

def run_pair(args):
    """b0 vs b1 打 n 場(先手每場交替)。回傳兩側的統計總和與全局計數。"""
    b0, b1, p, n, seed = args
    rng = random.Random(seed)
    acc = [dict.fromkeys(STAT_FIELDS, 0), dict.fromkeys(STAT_FIELDS, 0)]
    for a in acc:
        a.update(winded_fights=0, winded_sum=0, refuse_fights=0, refuse_sum=0, rounds=0,
                 **{f"n_{c}": 0 for c in CATS}, **{f"d_{c}": 0 for c in CATS})
    g = dict(n=n, wins=[0, 0], draws=0, stalled=0, rounds=0, hp_end=[0, 0],
             first_crit_n=0, first_crit_win=0, first_crit_hit_n=0, first_crit_hit_win=0)
    for i in range(n):
        f = fight(b0, b1, p, rng, first=i % 2)
        g["rounds"] += f.rounds
        if f.winner is None:
            g["draws"] += 1
            g["stalled"] += f.stalled
        else:
            g["wins"][f.winner] += 1
        if f.first_crit is not None and f.winner is not None:
            g["first_crit_n"] += 1
            g["first_crit_win"] += f.first_crit == f.winner
        if f.first_crit_hit is not None and f.winner is not None:
            g["first_crit_hit_n"] += 1
            g["first_crit_hit_win"] += f.first_crit_hit == f.winner
        for k, s in enumerate(f.states):
            st, a = s.st, acc[k]
            for fld in STAT_FIELDS:
                a[fld] += getattr(st, fld)
            a["rounds"] += f.rounds
            g["hp_end"][k] += max(s.hp, 0)
            if st.first_winded is not None:
                a["winded_fights"] += 1
                a["winded_sum"] += st.first_winded
            if st.first_refuse is not None:
                a["refuse_fights"] += 1
                a["refuse_sum"] += st.first_refuse
            for c in CATS:
                a[f"n_{c}"] += st.cat_n[c]
                a[f"d_{c}"] += st.cat_dmg[c]
    return acc, g


def merge_sides(acc):
    """把兩側相加(對稱對戰時求平均用)。"""
    out = {}
    for k in acc[0]:
        out[k] = acc[0][k] + acc[1][k]
    return out


def heat(ax, M, names, title, fs=7):
    ax.imshow(M, cmap="RdYlGn", vmin=0, vmax=1)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=60, ha="right", fontsize=fs)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=fs)
    for i in range(len(names)):
        for j in range(len(names)):
            ax.text(j, i, f"{M[i][j]:.2f}", ha="center", va="center", fontsize=fs - 1)
    ax.set_title(title, fontsize=9)


def monopoly(M):
    n = len(M)
    return [i for i in range(n) if all(M[i][j] > 0.5 for j in range(n) if j != i)]


def matrix(builds, p, n, seed, pool):
    jobs, keys = [], []
    for i, a in enumerate(builds):
        for j, b in enumerate(builds):
            if i < j:
                jobs.append((a, b, p, n, seed + 1000 * i + j))
                keys.append((i, j))
    res = dict(zip(keys, pool.map(run_pair, jobs)))
    N = len(builds)
    M = [[0.5] * N for _ in builds]          # 平手算半
    Dec = [[float("nan")] * N for _ in builds]   # 只計分出勝負的場次
    R = [[0.0] * N for _ in builds]
    D = [[0.0] * N for _ in builds]
    for (i, j), (acc, g) in res.items():
        w0, w1, dr = g["wins"][0] / n, g["wins"][1] / n, g["draws"] / n
        M[i][j], M[j][i] = w0 + dr / 2, w1 + dr / 2
        Dec[i][j] = div(w0, w0 + w1)
        Dec[j][i] = div(w1, w0 + w1)
        R[i][j] = R[j][i] = g["rounds"] / n
        D[i][j] = D[j][i] = dr
    return M, Dec, R, D


# ---------------------------------------------------------------------------
# E1 消耗格與喘點
# ---------------------------------------------------------------------------

def e1_build(level, name="甲"):
    return Build(name, weapon=level, step=level, dodge=level, parry=level, block=level,
                 step_def="dodge")


def e1(n=3000):
    thrs = (2, 3, 5, INF)
    regens = (0, 3, 6)
    levels = (1, 3, 5)
    jobs, meta = [], []
    for pool_mode in ("skill", "weapon"):
        for lv in levels:
            for thr in thrs:
                for rg in regens:
                    p = Params(act_threshold=thr, regen_stamina=rg, shield="large",
                               parry_pool=pool_mode)
                    jobs.append((e1_build(lv, "甲"), e1_build(lv, "乙"), p, n,
                                 SEED + len(jobs)))
                    meta.append((pool_mode, lv, thr, rg))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (pool_mode, lv, thr, rg), (acc, g) in zip(meta, res):
        s = merge_sides(acc)
        rows.append({
            "parry_pool": pool_mode, "level": lv, "act_threshold": THR_LABEL[thr],
            "regen_stamina": rg,
            "actions_per_round": r4(div(s["acts"], s["rounds"])),
            "attacks_per_round": r4(div(s["atk_acts"], s["rounds"])),
            "defense_acts_per_round": r4(div(s["def_acts"], s["rounds"])),
            "first_winded_round_mean": r4(div(s["winded_sum"], s["winded_fights"])),
            "pct_fighters_winded": r4(s["winded_fights"] / (2 * n)),
            "first_refuse_round_mean": r4(div(s["refuse_sum"], s["refuse_fights"])),
            "pct_fighters_refused": r4(s["refuse_fights"] / (2 * n)),
            "atk_roll_success": r4(div(s["succ"], s["att"])),
            "not_fully_dodged": r4(div(s["thru"], s["att"])),
            "net_hit_rate": r4(div(s["dmg_hit"], s["att"])),
            "dmg_per_attack": r4(div(s["dmg_total"], s["att"])),
            "step_ok_rate": r4(div(s["step_ok"], s["step_try"])),
            "mean_step_S": r4(div(s["step_sum"], s["step_try"])),
            "fight_rounds_mean": r4(g["rounds"] / n),
            "draw_rate": r4(g["draws"] / n),
            "stalled_rate": r4(g["stalled"] / n)})
    write_csv("s2_e1_cost_scan.csv", rows)

    # 使用紀錄樣本:一場戰鬥(thr 3、regen 0、一級)
    log = UsageLog()
    fight(e1_build(1, "甲"), e1_build(1, "乙"), Params(act_threshold=3, shield="large"),
          random.Random(SEED), first=0, log=log, session=0)
    log.to_csv(os.path.join(OUT, "s2_e1_usage_log_sample.csv"))

    base = [r for r in rows if r["parry_pool"] == "skill"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.5))
    thr_lab = [THR_LABEL[t] for t in thrs]
    colors = {0: "C0", 3: "C1", 6: "C2"}
    ax = axes[0][0]
    for rg in regens:
        ys = [next(r["first_winded_round_mean"] for r in base if r["level"] == 1 and
                   r["act_threshold"] == t and r["regen_stamina"] == rg) for t in thr_lab]
        ax.plot(thr_lab, ys, "o-", color=colors[rg], label=f"regen {rg}")
    ax.set_title("首次付不起(喘)的回合(一級;只計有喘到的角色)", fontsize=9)
    ax.set_xlabel("act_threshold")
    ax.legend()
    ax = axes[0][1]
    for lv, ls in zip(levels, ("-", "--", ":")):
        ys = [next(r["actions_per_round"] for r in base if r["level"] == lv and
                   r["act_threshold"] == t and r["regen_stamina"] == 0) for t in thr_lab]
        ax.plot(thr_lab, ys, "o" + ls, label=f"等級 {lv}(低價 {(lv + 1) // 2} 次)")
    ax.set_title("每回合平均動作數(regen 0)", fontsize=9)
    ax.set_xlabel("act_threshold")
    ax.legend()
    ax = axes[1][0]
    for rg in regens:
        ys = [next(r["net_hit_rate"] for r in base if r["level"] == 1 and
                   r["act_threshold"] == t and r["regen_stamina"] == rg) for t in thr_lab]
        ax.plot(thr_lab, ys, "o-", color=colors[rg], label=f"regen {rg}")
    ax.set_title("淨命中率(造成傷害的出手 / 出手數,一級)", fontsize=9)
    ax.set_xlabel("act_threshold")
    ax.legend()
    ax = axes[1][1]
    for rg in regens:
        ys = [next(r["draw_rate"] for r in base if r["level"] == 1 and
                   r["act_threshold"] == t and r["regen_stamina"] == rg) for t in thr_lab]
        ax.plot(thr_lab, ys, "o-", color=colors[rg], label=f"regen {rg}")
    ax.set_title("平手率(300 回合上限或雙方池空;一級)", fontsize=9)
    ax.set_xlabel("act_threshold")
    ax.legend()
    fig.suptitle("E1 消耗格與喘點(52 vs 52,體力 28,HP 30)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s2_e1.png"), dpi=120)
    plt.close(fig)
    write_json("s2_summary_e1.json", {"rows": rows})


# ---------------------------------------------------------------------------
# E2 等經驗配置矩陣
# ---------------------------------------------------------------------------

def cum_cost(level, flat=False):
    """佔位成本:第 n 級 100 × n(累計 50 n (n+1))。非設計值。"""
    return 100 * level if flat else 50 * level * (level + 1)


def alloc(budget, fracs, fixed=None):
    """依比例把經驗分到技能,轉成等級;零頭依序給最便宜的下一級。fixed = 先扣掉的固定等級。"""
    fixed = fixed or {}
    budget = budget - sum(cum_cost(v) for v in fixed.values())
    lv, spent = {}, 0
    for k, fr in fracs.items():
        L = 0
        while cum_cost(L + 1) <= budget * fr:
            L += 1
        lv[k] = L
        spent += cum_cost(L)
    left = budget - spent
    while True:
        k = sorted(fracs, key=lambda k: (cum_cost(lv[k] + 1) - cum_cost(lv[k]), -fracs[k]))[0]
        c = cum_cost(lv[k] + 1) - cum_cost(lv[k])
        if c > left:
            break
        lv[k] += 1
        left -= c
    lv.update(fixed)
    return lv, left


def e2_builds(budget):
    out = []

    def add(build, lv, left):
        out.append((build, lv, left))

    lv, left = alloc(budget, {"weapon": 1.0})
    add(Build("純攻", weapon=lv["weapon"]), lv, left)
    lv, left = alloc(budget, {"dodge": .5, "parry": .5})
    add(Build("純守", dodge=lv["dodge"], parry=lv["parry"]), lv, left)
    lv, left = alloc(budget, {"weapon": .25, "dodge": .25, "parry": .25, "block": .25})
    add(Build("均衡帶盾", weapon=lv["weapon"], dodge=lv["dodge"], parry=lv["parry"],
              block=lv["block"]), lv, left)
    lv, left = alloc(budget, {"step": .5, "dodge": .5})
    add(Build("步法賭徒", step=lv["step"], dodge=lv["dodge"], step_def="dodge"), lv, left)
    lv, left = alloc(budget, {"step": 1 / 3, "weapon": 1 / 3, "parry": 1 / 3})
    add(Build("步法戰士·招架", step=lv["step"], weapon=lv["weapon"], parry=lv["parry"],
              step_def="parry"), lv, left)
    add(Build("步法戰士·攻擊", step=lv["step"], weapon=lv["weapon"], parry=lv["parry"],
              step_atk=True), lv, left)
    lv, left = alloc(budget, {"weapon": .5, "dodge": .25, "parry": .25})
    w = lv["weapon"]
    add(Build("技術流", weapon=w, dodge=lv["dodge"], parry=lv["parry"],
              shunshi=w >= 2, lianci=w >= 3, xieren=w >= 4), lv, left)
    lv, left = alloc(budget, {"weapon": .5, "neigong": .5})
    add(Build("內力流", weapon=lv["weapon"], neigong=lv["neigong"], neili_first=True), lv, left)
    lv, left = alloc(budget, {"weapon": .5, "offhand": .5}, fixed={"dual": 1})
    add(Build("雙持", weapon=lv["weapon"], offhand=lv["offhand"]), lv, left)
    return out


E2_VARIANTS = [
    ("thr=3,regen=0", dict(act_threshold=3, regen_stamina=0)),
    ("thr=3,regen=3", dict(act_threshold=3, regen_stamina=3)),
]


def e2(n=10_000):
    budgets = (500, 1500, 5000)
    rows, builds_rows, summary = [], [], {}
    fig, axes = plt.subplots(len(E2_VARIANTS), len(budgets), figsize=(18, 11))
    with ProcessPoolExecutor() as pool:
        for vi, (vname, kw) in enumerate(E2_VARIANTS):
            p = Params(shield="large", dual_wield="lv1", **kw)
            for bi, budget in enumerate(budgets):
                bl = e2_builds(budget)
                builds = [b for b, _, _ in bl]
                if vi == 0:
                    for b, lv, left in bl:
                        builds_rows.append({
                            "budget": budget, "build": b.name, "levels": json.dumps(lv),
                            "xp_unspent": left, "weapon": b.value("weapon"),
                            "step": b.value("step"), "dodge": b.value("dodge"),
                            "parry": b.value("parry"),
                            "block": b.value("block") if b.block is not None else "",
                            "offhand": b.value("offhand") if b.offhand is not None else "",
                            "neili_pool": b.value("neigong") if b.neigong else 0,
                            "subskills": ",".join(k for k in ("shunshi", "lianci", "xieren")
                                                  if getattr(b, k))})
                M, Dec, R, D = matrix(builds, p, n, SEED + 7 * bi + 100 * vi, pool)
                names = [b.name for b in builds]
                for i in range(len(builds)):
                    for j in range(len(builds)):
                        if i != j:
                            rows.append({"variant": vname, "budget": budget, "row": names[i],
                                         "col": names[j], "row_win_rate": r4(M[i][j]),
                                         "row_win_rate_decisive": r4(Dec[i][j]),
                                         "draw_rate": r4(D[i][j]), "mean_rounds": round(R[i][j], 2)})
                mono = [names[i] for i in monopoly(M)]
                mono_dec = [names[i] for i in range(len(names))
                            if all(Dec[i][j] > 0.5 for j in range(len(names))
                                   if j != i and Dec[i][j] == Dec[i][j])]
                summary[f"{vname}/{budget}"] = {"names": names, "M": M, "decisive": Dec,
                                                "draw": D, "monopoly_rows": mono,
                                                "monopoly_rows_decisive": mono_dec}
                heat(axes[vi][bi], M, names,
                     f"{vname},經驗 {budget}" + (f"\n全綠列:{','.join(mono)}" if mono else ""))
    fig.suptitle("E2 列勝率(列 vs 行,平手算半)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s2_e2.png"), dpi=110)
    plt.close(fig)
    write_csv("s2_e2_matrix.csv", rows)
    write_csv("s2_e2_builds.csv", builds_rows)
    write_json("s2_summary_e2.json", summary)


# ---------------------------------------------------------------------------
# E3 攻守貨幣
# ---------------------------------------------------------------------------

def e3_builds(N):
    return [
        Build("全押攻", weapon=N),
        Build("均衡", weapon=N // 2, dodge=N // 4, parry=N // 4),
        Build("全押守", dodge=N // 2, parry=N // 2),
        Build("全押步法閃避", step=N // 2, dodge=N // 2, step_def="dodge"),   # 新增:賭全閃
    ]


def e3(n=4_000):
    hps = (30, 60, 100)
    thrs = (3, INF)
    regens = (0, 3)
    Ns = (4, 8)
    jobs, meta = [], []
    for N in Ns:
        builds = e3_builds(N)
        for hp in hps:
            for thr in thrs:
                for rg in regens:
                    p = Params(hp=hp, act_threshold=thr, regen_stamina=rg)
                    for i in range(len(builds)):
                        for j in range(i + 1, len(builds)):
                            jobs.append((builds[i], builds[j], p, n, SEED + len(jobs)))
                            meta.append((N, hp, thr, rg, builds[i].name, builds[j].name))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (N, hp, thr, rg, a, b), (acc, g) in zip(meta, res):
        w0, w1, dr = g["wins"][0] / n, g["wins"][1] / n, g["draws"] / n
        rows.append({"total_levels": N, "hp": hp, "act_threshold": THR_LABEL[thr],
                     "regen_stamina": rg, "row": a, "col": b,
                     "row_win_rate": r4(w0 + dr / 2), "row_win_rate_decisive": r4(div(w0, w0 + w1)),
                     "draw_rate": r4(dr), "mean_rounds": round(g["rounds"] / n, 2),
                     "row_hp_end": round(g["hp_end"][0] / n, 2),
                     "col_hp_end": round(g["hp_end"][1] / n, 2)})
    write_csv("s2_e3_attack_vs_defense.csv", rows)

    # 每個條件下四種配置的「對其他三種平均勝率」排序
    names = [b.name for b in e3_builds(4)]
    rank_rows = []
    for N in Ns:
        for hp in hps:
            for thr in thrs:
                for rg in regens:
                    sub = [r for r in rows if r["total_levels"] == N and r["hp"] == hp and
                           r["act_threshold"] == THR_LABEL[thr] and r["regen_stamina"] == rg]
                    score = {k: [] for k in names}
                    for r in sub:
                        score[r["row"]].append(r["row_win_rate"])
                        score[r["col"]].append(1 - r["row_win_rate"])
                    rank_rows.append({"total_levels": N, "hp": hp,
                                      "act_threshold": THR_LABEL[thr], "regen_stamina": rg,
                                      **{k: r4(sum(v) / len(v)) for k, v in score.items()},
                                      "order": " > ".join(sorted(
                                          names, key=lambda k: -sum(score[k]) / len(score[k])))})
    write_csv("s2_e3_ranking.csv", rank_rows)

    fig, axes = plt.subplots(len(Ns), len(thrs), figsize=(14, 8), sharey=True)
    for ni, N in enumerate(Ns):
        for ti, thr in enumerate(thrs):
            ax = axes[ni][ti]
            for rg, ls in zip(regens, ("-", "--")):
                for k, c in zip(names, ("C3", "C0", "C2", "C4")):
                    ys = [next(r[k] for r in rank_rows if r["total_levels"] == N and r["hp"] == hp
                               and r["act_threshold"] == THR_LABEL[thr] and r["regen_stamina"] == rg)
                          for hp in hps]
                    ax.plot(hps, ys, "o" + ls, color=c, label=f"{k} regen{rg}" if True else None)
            ax.axhline(0.5, color="gray", ls=":")
            ax.set_xticks(hps)
            ax.set_ylim(0, 1)
            ax.set_xlabel("hp")
            ax.set_title(f"總等級 {N},act_threshold={THR_LABEL[thr]}", fontsize=9)
            if ni == 0 and ti == 0:
                ax.legend(fontsize=6, ncol=2)
    axes[0][0].set_ylabel("對其他三種的平均勝率(平手算半)")
    fig.suptitle("E3 攻守貨幣(實線 regen 0,虛線 regen 3)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s2_e3.png"), dpi=120)
    plt.close(fig)
    write_json("s2_summary_e3.json", {"ranking": rank_rows})


# ---------------------------------------------------------------------------
# E7 高手互打
# ---------------------------------------------------------------------------

def master(name="高手"):
    v = {k: 150 for k in ("weapon", "step", "dodge", "parry", "block")}
    lv = {k: 5 for k in v}
    return Build(name, weapon=5, step=5, dodge=5, parry=5, block=5, crit_bonus=5,
                 step_def="dodge", skill_override=v, level_override=lv)


def e7(n=4_000):
    hps = (30, 60, 100)
    thrs = (3, INF)
    regens = (0, 3)
    jobs, meta = [], []
    for hp in hps:
        for thr in thrs:
            for rg in regens:
                p = Params(hp=hp, act_threshold=thr, regen_stamina=rg, shield="large")
                jobs.append((master("高手甲"), master("高手乙"), p, n, SEED + len(jobs)))
                meta.append((hp, thr, rg))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (hp, thr, rg), (acc, g) in zip(meta, res):
        s = merge_sides(acc)
        n_hits = sum(s[f"n_{c}"] for c in CATS)
        d_tot = sum(s[f"d_{c}"] for c in CATS)
        decided = n - g["draws"]
        row = {"hp": hp, "act_threshold": THR_LABEL[thr], "regen_stamina": rg,
               "fight_rounds_mean": round(g["rounds"] / n, 2),
               "draw_rate": r4(g["draws"] / n), "stalled_rate": r4(g["stalled"] / n),
               "attacks_per_fight": r4(s["att"] / n),
               "atk_roll_success": r4(div(s["succ"], s["att"])),
               "crit_rate_per_attack": r4(div(s["crit_roll"], s["att"])),
               "full_dodge_rate_given_success": r4(div(s["full_dodged"], s["succ"])),
               "parry_ok_rate": r4(div(s["parry_ok"], s["parry_try"])),
               "block_ok_rate": r4(div(s["block_ok"], s["block_try"])),
               "defense_acts_per_faced_attack": r4(div(s["def_acts"], s["faced"])),
               "dmg_hits_per_attack": r4(div(n_hits, s["att"])),
               "first_crit_side_wins": r4(div(g["first_crit_win"], g["first_crit_n"])),
               "first_crit_hit_side_wins": r4(div(g["first_crit_hit_win"], g["first_crit_hit_n"])),
               "decided_fights": decided}
        for c in CATS:
            row[f"hits_share_{c}"] = r4(div(s[f"n_{c}"], n_hits))
            row[f"dmg_share_{c}"] = r4(div(s[f"d_{c}"], d_tot))
        rows.append(row)
    write_csv("s2_e7_master_vs_master.csv", rows)

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    labels = [f"hp{r['hp']}\nthr{r['act_threshold']}\nregen{r['regen_stamina']}" for r in rows]
    x = range(len(rows))
    axes[0].bar(x, [r["fight_rounds_mean"] for r in rows])
    axes[0].set_title("平均回合數", fontsize=10)
    axes[1].bar(x, [r["draw_rate"] for r in rows])
    axes[1].set_title("平手率", fontsize=10)
    bottom = [0] * len(rows)
    for c, col in zip(CATS, ("C3", "C1", "C0")):
        v = [r[f"hits_share_{c}"] if r[f"hits_share_{c}"] == r[f"hits_share_{c}"] else 0
             for r in rows]
        axes[2].bar(x, v, bottom=bottom, label=c, color=col)
        bottom = [b + a for b, a in zip(bottom, v)]
    axes[2].set_title("造成傷害的出手:來源占比(致命滿骰 / 穿透招架格擋 / 無有效守備)", fontsize=9)
    axes[2].legend(fontsize=7)
    for ax in axes:
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=6)
    fig.suptitle("E7 150+5 vs 150+5,各技能五級")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s2_e7.png"), dpi=120)
    plt.close(fig)
    write_json("s2_summary_e7.json", {"rows": rows})


if __name__ == "__main__":
    which = sys.argv[1:] or ["e1", "e2", "e3", "e7"]
    for w in which:
        print("running", w, flush=True)
        globals()[w]()
