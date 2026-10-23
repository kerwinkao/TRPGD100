"""E1、E2、E3、E4、E7。用法:python3 sim/experiments.py [e1 e2 e3 e4 e7]
輸出到 sim/out/:每實驗 CSV + PNG,以及 summary_<exp>.json 供 REPORT 引用。
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

from fight import Build, attack, fight, new_state
from resolve import Layer, Params, UsageLog, check, crit_zone, damage, erode, overflow

OUT = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT, exist_ok=True)

for _f in ("WenQuanYi Zen Hei", "Noto Sans CJK TC", "Noto Sans CJK JP"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        break
plt.rcParams["axes.unicode_minus"] = False

SEED = 20261023


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


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


# ---------------------------------------------------------------------------
# E1 代價尺度掃描
# ---------------------------------------------------------------------------

def e1(n_attacks=100_000, n_fights=5_000):
    rng = random.Random(SEED)
    rows_a = []
    # A. 每擊淨命中率(池不設限):52 攻 vs 52 × k 層(全一級,上限 10)
    for fd in ("raw", "capped"):
        p = Params(full_defense=fd)
        for k in (0, 1, 2, 3):
            layers = [Layer(n, 52, 1) for n in ("閃避", "招架", "格擋")][:k]
            hits = rolled = 0
            tl = []
            for _ in range(n_attacks):
                a = check(52, rng, params=p)
                if not a.success:
                    continue
                T, steps = erode(a.margin, layers, rng, p)
                rolled += len(steps)
                if T > 0:
                    hits += 1
                    tl.append(T)
            rows_a.append({"full_defense": fd, "chain_len": k,
                           "net_hit_rate": hits / n_attacks,
                           "layers_rolled_per_attack": rolled / n_attacks,
                           "mean_T_left_on_hit": mean(tl)})
    write_csv("e1_hit_rate.csv", rows_a)

    # B. 對打:52 vs 52,體力 28,不回復(A13),HP 30(A11)
    rows_b = []
    sample_log = UsageLog()
    for declare in ("adaptive", "upfront"):
        for free_first in (False, True):
            for cost in (3, 5, 8):
                for k in (1, 2, 3):
                    p = Params(cost_layer=cost, declare=declare, free_first_layer=free_first)
                    b0 = Build("甲", weapon_level=1, dodge=1, parry=1, block=1, chain_max=k)
                    b1 = replace(b0, name="乙")
                    winded, rounds, lr, lp, hit_before, hit_after = [], [], [], [], [], []
                    for n in range(n_fights):
                        log = sample_log if (n == 0 and declare == "adaptive" and not free_first
                                             and cost == 5 and k == 3) else None
                        f = fight(b0, b1, p, rng, first=n % 2, log=log, session=n)
                        rounds.append(f.rounds)
                        for idx, s in enumerate(f.states):
                            winded.append(s.winded_at if s.winded_at is not None else float("nan"))
                            if s.defenses:
                                lr.append(s.layers_rolled / s.defenses)
                                lp.append(s.layers_paid / s.defenses)
                        # 喘之前 / 之後的被命中率
                        for side in (0, 1):
                            w = f.states[side].winded_at
                            cnt = 0
                            for att, out in f.events:
                                if att == 1 - side and out.attempted:
                                    if out.success:
                                        cnt += 1
                                    hit = out.T_left > 0
                                    (hit_before if (w is None or cnt < w) else hit_after).append(hit)
                    w_ok = [x for x in winded if x == x]
                    rows_b.append({
                        "declare": declare, "free_first_layer": free_first, "cost_layer": cost,
                        "chain_len": k,
                        "defenses_until_winded_mean": mean(w_ok),
                        "pct_fighters_winded": len(w_ok) / len(winded),
                        "layers_rolled_per_defense": mean(lr),
                        "layers_paid_per_defense": mean(lp),
                        "hit_rate_before_winded": mean(hit_before),
                        "hit_rate_after_winded": mean(hit_after),
                        "fight_rounds_mean": mean(rounds)})
    write_csv("e1_cost_scan.csv", rows_b)
    sample_log.to_csv(os.path.join(OUT, "e1_usage_log_sample.csv"))

    # 圖
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    sub = [r for r in rows_b if r["declare"] == "adaptive" and not r["free_first_layer"]]
    for k, c in zip((1, 2, 3), ("C0", "C1", "C2")):
        xs = [r["cost_layer"] for r in sub if r["chain_len"] == k]
        ys = [r["defenses_until_winded_mean"] for r in sub if r["chain_len"] == k]
        axes[0].plot(xs, ys, "o-", color=c, label=f"鏈長上限 {k}")
    axes[0].axhline(3, ls="--", color="gray", label="三招")
    axes[0].set_xlabel("cost_layer")
    axes[0].set_ylabel("首次付不起一層前的防禦次數")
    axes[0].set_title("E1 幾招喘(adaptive,每層付費,體力 28)")
    axes[0].legend()
    for fd, ls in (("raw", "-"), ("capped", "--")):
        r = [x for x in rows_a if x["full_defense"] == fd]
        axes[1].plot([x["chain_len"] for x in r], [x["net_hit_rate"] for x in r], "o" + ls,
                     label=f"full_defense={fd}")
    axes[1].set_xlabel("防禦層數(池不設限)")
    axes[1].set_ylabel("淨命中率")
    axes[1].set_title("E1 52 vs 52 每擊淨命中率")
    axes[1].set_xticks([0, 1, 2, 3])
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "e1.png"), dpi=120)
    plt.close(fig)
    write_json("summary_e1.json", {"hit_rate": rows_a, "cost_scan": rows_b})


# ---------------------------------------------------------------------------
# 等經驗/等級配置
# ---------------------------------------------------------------------------

def cum_cost(level, flat=False):
    """E2/E4 的佔位成本:level n 一級 100 × n(escalating)或一律 100(flat)。非設計值。"""
    return 100 * level if flat else 50 * level * (level + 1)


def alloc(budget, fracs, flat=False):
    """依比例把經驗分到技能,轉成等級;零頭依序給最便宜的下一級。回傳 (levels, leftover)。"""
    lv = {}
    spent = 0
    for k, fr in fracs.items():
        L = 0
        while cum_cost(L + 1, flat) <= budget * fr:
            L += 1
        lv[k] = L
        spent += cum_cost(L, flat)
    left = budget - spent
    while True:
        nxt = sorted(fracs, key=lambda k: (cum_cost(lv[k] + 1, flat) - cum_cost(lv[k], flat), -fracs[k]))
        k = nxt[0]
        c = cum_cost(lv[k] + 1, flat) - cum_cost(lv[k], flat)
        if c > left:
            break
        lv[k] += 1
        left -= c
    return lv, left


SUB_COST = 0   # 副技能的經驗成本:規則未給(A15),本輪記 0


def e2_builds(budget):
    out = []
    lv, left = alloc(budget, {"weapon": 1.0})
    out.append((Build("純攻", weapon_level=lv["weapon"], stacks=("戰鬥反應",)), lv, left))
    lv, left = alloc(budget, {"dodge": .5, "parry": .5})
    out.append((Build("純守", dodge=lv["dodge"], parry=lv["parry"], stacks=("戰鬥反應",)), lv, left))
    lv, left = alloc(budget, {"weapon": .25, "dodge": .25, "parry": .25, "block": .25})
    out.append((Build("均衡", weapon_level=lv["weapon"], dodge=lv["dodge"], parry=lv["parry"],
                      block=lv["block"], stacks=("戰鬥反應",)), lv, left))
    # 技術流:武器中等(一半),其餘閃避/招架;副技能依 unlock_level 解鎖
    lv, left = alloc(budget, {"weapon": .5, "dodge": .25, "parry": .25})
    w = lv["weapon"]
    out.append((Build("技術流", weapon_level=w, dodge=lv["dodge"], parry=lv["parry"],
                      stacks=("戰鬥反應",), shunshi=w >= 2, lianci=w >= 3, xieren=w >= 4), lv, left))
    lv, left = alloc(budget, {"weapon": .5, "neigong": .5})
    out.append((Build("內力流", weapon_level=lv["weapon"], neigong=lv["neigong"],
                      stacks=("內功", "戰鬥反應"), neili_first=True), lv, left))
    return out


def _matrix_job(args):
    b0, b1, p, n, seed = args
    rng = random.Random(seed)
    w0 = w1 = dr = 0
    rounds = 0
    for i in range(n):
        f = fight(b0, b1, p, rng, first=i % 2, keep_events=False)
        rounds += f.rounds
        if f.winner == 0:
            w0 += 1
        elif f.winner == 1:
            w1 += 1
        else:
            dr += 1
    return w0 / n, w1 / n, dr / n, rounds / n


def matrix(builds, p, n, seed, pool):
    jobs, keys = [], []
    for i, a in enumerate(builds):
        for j, b in enumerate(builds):
            if i < j:
                jobs.append((a, replace(b, name=b.name + "'") if a.name == b.name else b, p, n,
                             seed + 1000 * i + j))
                keys.append((i, j))
    res = dict(zip(keys, pool.map(_matrix_job, jobs)))
    M = [[0.5] * len(builds) for _ in builds]
    R = [[0.0] * len(builds) for _ in builds]
    D = [[0.0] * len(builds) for _ in builds]
    for (i, j), (w0, w1, dr, rr) in res.items():
        M[i][j] = w0 + dr / 2
        M[j][i] = w1 + dr / 2
        R[i][j] = R[j][i] = rr
        D[i][j] = D[j][i] = dr
    return M, R, D


def heat(ax, M, names, title):
    ax.imshow(M, cmap="RdYlGn", vmin=0, vmax=1)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names)
    for i in range(len(names)):
        for j in range(len(names)):
            ax.text(j, i, f"{M[i][j]:.2f}", ha="center", va="center", fontsize=8)
    ax.set_title(title, fontsize=10)


def monopoly(M):
    """任一列(對角線外)全 > 0.5 即為洞。"""
    n = len(M)
    return [i for i in range(n) if all(M[i][j] > 0.5 for j in range(n) if j != i)]


# ---------------------------------------------------------------------------
# E2 等經驗配置矩陣
# ---------------------------------------------------------------------------

def e2(n=10_000):
    variants = [("stack=level", Params(stack_mode="level")),
                ("stack=full", Params(stack_mode="full"))]
    budgets = (500, 1500, 5000)
    rows, builds_rows, summary = [], [], {}
    fig, axes = plt.subplots(len(variants), len(budgets), figsize=(15, 9.5))
    with ProcessPoolExecutor() as pool:
        for vi, (vname, p) in enumerate(variants):
            for bi, budget in enumerate(budgets):
                bl = e2_builds(budget)
                builds = [b for b, _, _ in bl]
                if vi == 0:
                    for b, lv, left in bl:
                        builds_rows.append({"budget": budget, "build": b.name,
                                            "levels": json.dumps(lv), "xp_unspent": left,
                                            "weapon": b.value("weapon"), "dodge": b.value("dodge"),
                                            "parry": b.value("parry"),
                                            "block": b.value("block") if b.block is not None else "",
                                            "neili_pool": b.value("neigong") if b.neigong else 0,
                                            "subskills": ",".join(k for k in ("shunshi", "lianci", "xieren")
                                                                  if getattr(b, k))})
                M, R, D = matrix(builds, p, n, SEED + 7 * bi + 100 * vi, pool)
                names = [b.name for b in builds]
                for i in range(len(builds)):
                    for j in range(len(builds)):
                        if i != j:
                            rows.append({"variant": vname, "budget": budget, "row": names[i],
                                         "col": names[j], "row_win_rate": round(M[i][j], 4),
                                         "draw_rate": round(D[i][j], 4),
                                         "mean_rounds": round(R[i][j], 1)})
                mono = [names[i] for i in monopoly(M)]
                summary[f"{vname}/{budget}"] = {"names": names, "M": M, "monopoly_rows": mono}
                heat(axes[vi][bi], M, names,
                     f"{vname},經驗 {budget}" + (f"\n全綠列:{','.join(mono)}" if mono else ""))
    fig.suptitle("E2 列勝率(列 vs 行,平手算半)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "e2.png"), dpi=120)
    plt.close(fig)
    write_csv("e2_matrix.csv", rows)
    write_csv("e2_builds.csv", builds_rows)
    write_json("summary_e2.json", summary)


# ---------------------------------------------------------------------------
# E3 攻守貨幣
# ---------------------------------------------------------------------------

def e3(n=4_000):
    variants = [
        ("無疊加", dict(stack_mode="none")),
        ("疊加=被疊技能值", dict(stack_mode="full")),
        ("疊加=10×等級", dict(stack_mode="level")),
        ("對策:疊加受等級上限", dict(stack_mode="full", stack_cap="by_level")),
        ("對策:疊加代價陡升", dict(stack_mode="full", stack_cost_growth="steep")),
    ]
    rows, summary = [], {}
    N_list = (4, 8)
    fds = ("raw", "capped")
    fig, axes = plt.subplots(len(fds) * len(N_list), 1, figsize=(10, 12))
    with ProcessPoolExecutor() as pool:
        for fi, fd in enumerate(fds):
            for ni, N in enumerate(N_list):
                builds = [
                    Build("全押攻", weapon_level=N, stacks=("戰鬥反應", "內功")),
                    Build("均衡", weapon_level=N // 2, dodge=N // 4, parry=N // 4,
                          stacks=("戰鬥反應", "內功")),
                    Build("全押守", dodge=N // 2, parry=N // 2, stacks=("戰鬥反應", "內功")),
                ]
                ax = axes[fi * len(N_list) + ni]
                labels, vals = [], {("全押攻", "均衡"): [], ("全押攻", "全押守"): [],
                                    ("均衡", "全押守"): []}
                for vname, kw in variants:
                    p = Params(full_defense=fd, **kw)
                    M, R, D = matrix(builds, p, n, SEED + 31 * N + len(vname), pool)
                    names = [b.name for b in builds]
                    for i in range(3):
                        for j in range(i + 1, 3):
                            rows.append({"full_defense": fd, "total_levels": N, "variant": vname,
                                         "row": names[i], "col": names[j],
                                         "row_win_rate": round(M[i][j], 4),
                                         "draw_rate": round(D[i][j], 4),
                                         "mean_rounds": round(R[i][j], 1)})
                            vals[(names[i], names[j])].append(M[i][j])
                    summary[f"{fd}/N{N}/{vname}"] = {"names": names, "M": M,
                                                     "monopoly_rows": [names[i] for i in monopoly(M)]}
                    labels.append(vname)
                x = range(len(labels))
                wbar = 0.27
                for k, (pair, v) in enumerate(vals.items()):
                    ax.bar([xi + (k - 1) * wbar for xi in x], v, wbar, label=f"{pair[0]} 勝 {pair[1]}")
                ax.axhline(0.5, color="gray", ls="--")
                ax.set_xticks(list(x))
                ax.set_xticklabels(labels, fontsize=8)
                ax.set_ylim(0, 1)
                ax.set_title(f"E3 full_defense={fd},總等級 {N}", fontsize=10)
                ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "e3.png"), dpi=120)
    plt.close(fig)
    write_csv("e3_attack_vs_defense.csv", rows)
    write_json("summary_e3.json", summary)


def e3b(n=4_000):
    """E3 敏感度:防禦層免費(cost_layer 0)或體力回復,無疊加。看「全押攻」佔優是否來自池耗盡。"""
    variants = [("基準 cost 5、不回復", dict()),
                ("防禦層免費 cost 0", dict(cost_layer=0)),
                ("體力每回合回 5", dict(regen_stamina=5)),
                ("HP 60", dict(hp=60))]
    rows = []
    with ProcessPoolExecutor() as pool:
        for N in (4, 8):
            builds = [Build("全押攻", weapon_level=N),
                      Build("均衡", weapon_level=N // 2, dodge=N // 4, parry=N // 4),
                      Build("全押守", dodge=N // 2, parry=N // 2)]
            for vname, kw in variants:
                M, R, D = matrix(builds, Params(stack_mode="none", **kw), n, SEED + N, pool)
                names = [b.name for b in builds]
                for i in range(3):
                    for j in range(i + 1, 3):
                        rows.append({"total_levels": N, "variant": vname, "row": names[i],
                                     "col": names[j], "row_win_rate": round(M[i][j], 4),
                                     "draw_rate": round(D[i][j], 4), "mean_rounds": round(R[i][j], 1)})
    write_csv("e3b_sensitivity.csv", rows)


# ---------------------------------------------------------------------------
# E4 進度曲線(確定性)
# ---------------------------------------------------------------------------

F = {"linear": lambda r: r, "sqrt": math.sqrt, "capped": lambda r: min(r, 1.0)}
# 校準:resolution 範例「哥布林(挑戰值 100)對總經驗 1000 的人值 12.5」→ base × f(0.1) = 12.5
BASE_REWARD = {k: 12.5 / f(0.1) for k, f in F.items()}


def e4(encounters=4, x0=500, max_sessions=2000, max_level=10):
    scenarios = [("固定對手 C=100", lambda X: 100.0),
                 ("梯子 ρ=0.5", lambda X: 0.5 * X),
                 ("梯子 ρ=1.5", lambda X: 1.5 * X)]
    rows = []
    fig, axes = plt.subplots(1, len(scenarios), figsize=(16, 4.8), sharey=False)
    for si, (sname, chal) in enumerate(scenarios):
        ax = axes[si]
        for fname, f in F.items():
            for flat in (True, False):
                X = float(x0)
                skill_xp = 0.0
                reached = {}
                for s in range(1, max_sessions + 1):
                    for _ in range(encounters):
                        r = BASE_REWARD[fname] * f(chal(X) / X)
                        X += r
                        skill_xp += r
                    while len(reached) < max_level and cum_cost(len(reached) + 1, flat) <= skill_xp:
                        reached[len(reached) + 1] = s
                    if len(reached) >= max_level:
                        break
                cost_name = "flat 100/級" if flat else "遞增 100×n/級"
                for L in range(1, max_level + 1):
                    rows.append({"scenario": sname, "xp_reward_f": fname, "level_cost": cost_name,
                                 "level": L, "sessions": reached.get(L, "")})
                xs = [L for L in range(1, max_level + 1) if L in reached]
                ax.plot(xs, [reached[L] for L in xs], "o-" if flat else "s--", ms=3,
                        label=f"{fname} / {cost_name}")
        ax.set_title(f"E4 {sname}", fontsize=10)
        ax.set_xlabel("技能等級")
        ax.set_ylabel("所需場數")
        ax.set_yscale("log")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "e4.png"), dpi=120)
    plt.close(fig)
    write_csv("e4_progression.csv", rows)
    write_json("summary_e4.json", {"base_reward": BASE_REWARD, "encounters_per_session": encounters,
                                   "x0": x0, "rows": rows})


# ---------------------------------------------------------------------------
# E7 溢出與致命
# ---------------------------------------------------------------------------

def _master_job(args):
    label, p, crit_b, lvl, n, seed = args
    rng = random.Random(seed)
    m = Build("高手", weapon_crit_bonus=crit_b, block=lvl, chain_max=3,
              skill_override={"weapon": 150, "dodge": 150, "parry": 150, "block": 150},
              level_override={"weapon": lvl, "dodge": lvl, "parry": lvl, "block": lvl})
    m2 = replace(m, name="高手'")
    att = crit_att = hit = crit_hit = 0
    dmg_total = crit_dmg = 0
    first_crit_wins = decided = draws = 0
    rounds = 0
    for i in range(n):
        f = fight(m, m2, p, rng, first=i % 2)
        rounds += f.rounds
        first_crit = None
        for who, o in f.events:
            if not o.attempted:
                continue
            att += 1
            if o.crit:
                crit_att += 1
                if first_crit is None:
                    first_crit = who
            if o.T_left > 0:
                hit += 1
                dmg_total += o.dmg
                if o.crit:
                    crit_hit += 1
                    crit_dmg += o.dmg
        draws += f.winner is None
        if f.winner is not None and first_crit is not None:
            decided += 1
            first_crit_wins += first_crit == f.winner
    return {"config": label, "crit_bonus": crit_b, "def_level": lvl,
            "cost_layer": p.cost_layer, "overflow_damage": p.overflow_damage,
            "crit_rate": crit_att / att, "hit_rate": hit / att,
            "p_hit_given_crit": crit_hit / max(crit_att, 1),
            "p_hit_given_noncrit": (hit - crit_hit) / max(att - crit_att, 1),
            "share_hits_from_crit": crit_hit / max(hit, 1),
            "share_dmg_from_crit": crit_dmg / max(dmg_total, 1),
            "first_crit_side_wins": first_crit_wins / max(decided, 1),
            "fight_rounds_mean": rounds / n, "draw_rate_300_rounds": draws / n}


def e7(n_rolls=10_000, n_fights=4_000):
    rng = random.Random(SEED)
    rows = []
    for od in ("pct", "flat"):
        p = Params(overflow_damage=od)
        for s in (100, 110, 125, 150):
            for cb in (0, 3, 5):
                crit = maxed = 0
                dmg = []
                for _ in range(n_rolls):
                    r = check(s, rng, crit_bonus=cb, params=p)
                    if r.crit:
                        crit += 1
                    if r.success:
                        d = damage(8, 1, overflow(s), r.crit, rng, p)
                        dmg.append(d)
                        maxed += r.crit and overflow(s) > 0
                base = 4.5
                exp_crit = crit_zone(s, cb) / 100
                o = overflow(s)
                # 期望值分解(每次成功):溢傷部分 vs 致命滿骰部分
                if od == "pct":
                    gain_over = base * o / 100
                    gain_crit = exp_crit * (8 - base) * (1 + o / 100) if o > 0 else 0
                else:
                    gain_over = o
                    gain_crit = exp_crit * (8 - base) if o > 0 else 0
                rows.append({"overflow_damage": od, "skill": s, "crit_bonus": cb,
                             "crit_rate": crit / n_rolls, "crit_rate_exact": exp_crit,
                             "crit_rate_half_up": crit_zone(s, cb, "half_up") / 100,
                             "maxdie_rate": maxed / n_rolls,
                             "mean_dmg_per_success": mean(dmg),
                             "dmg_gain_from_overflow_exact": round(gain_over, 3),
                             "dmg_gain_from_crit_maxdie_exact": round(gain_crit, 3)})
    write_csv("e7_overflow_crit.csv", rows)

    jobs = []
    for label, kw, cb, lvl in [
        ("150+5 互打,池不設限(cost 0)", dict(cost_layer=0), 5, 10),
        ("150+0 互打,池不設限(cost 0)", dict(cost_layer=0), 0, 10),
        ("150+5 互打,cost 5、體力 28", dict(cost_layer=5), 5, 10),
        ("150+5 互打,防禦等級 5(上限 50)", dict(cost_layer=0), 5, 5),
        ("150+5 互打,full_defense=capped,等級 5", dict(cost_layer=0, full_defense="capped"), 5, 5),
    ]:
        for od in ("pct", "flat"):
            jobs.append((label, Params(overflow_damage=od, **kw), cb, lvl, n_fights,
                         SEED + len(jobs)))
    with ProcessPoolExecutor() as pool:
        mrows = list(pool.map(_master_job, jobs))
    write_csv("e7_master_vs_master.csv", mrows)

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))
    sub = [r for r in rows if r["overflow_damage"] == "pct"]
    for cb, c in zip((0, 3, 5), ("C0", "C1", "C2")):
        r = [x for x in sub if x["crit_bonus"] == cb]
        axes[0].plot([x["skill"] for x in r], [x["crit_rate"] for x in r], "o-", color=c,
                     label=f"crit_bonus {cb}")
    axes[0].set_title("E7 致命率")
    axes[0].set_xlabel("技能值")
    axes[0].legend()
    for od, ax in (("pct", axes[1]), ("flat", axes[2])):
        r = [x for x in rows if x["overflow_damage"] == od and x["crit_bonus"] == 0]
        xs = [x["skill"] for x in r]
        ax.bar([x - 1.5 for x in xs], [x["dmg_gain_from_overflow_exact"] for x in r], 3,
               label="溢傷帶來的增傷")
        r5 = [x for x in rows if x["overflow_damage"] == od and x["crit_bonus"] == 5]
        ax.bar([x + 1.5 for x in xs], [x["dmg_gain_from_crit_maxdie_exact"] for x in r5], 3,
               label="致命滿骰帶來的增傷(crit_bonus 5)")
        ax.set_title(f"E7 每次成功的期望增傷分解(overflow_damage={od})", fontsize=10)
        ax.set_xlabel("技能值")
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "e7.png"), dpi=120)
    plt.close(fig)
    write_json("summary_e7.json", {"rolls": rows, "masters": mrows})


if __name__ == "__main__":
    which = sys.argv[1:] or ["e1", "e2", "e3", "e4", "e7"]
    for w in which:
        print("running", w, flush=True)
        globals()[w]()
