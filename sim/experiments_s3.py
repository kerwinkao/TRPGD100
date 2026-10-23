"""Session 3 實驗:散修互毆(難度 1、技能 1–4 級、副技能 ≤ 1 級)。
E1 消耗格與喘點、E2 NPC 模板矩陣(含同經驗與副技能流)、E3 攻守貨幣、E8 任意重骰量化、E7 高手互打(次要)、E6 屬性引用統計。
用法:python3 sim/experiments_s3.py [e1 e2 e3 e8 e7 e6]
輸出到 sim/out/,檔名一律 s3_ 前綴(不覆蓋 Session 1、2)。

勝負分兩張矩陣:
- 失能:依設計文件,輕傷位 0 即失能、戰鬥結束(Params.end_on='incap')。
- 擊殺:依設計文件,失能時敗方重傷位 > 0 → 這條終止下擊殺只來自單擊過量(統計在 kill_rate_as_ruled)。
  另跑 end_on='death'(戰鬥打到重傷位 0,A40)得「打到死」的勝率矩陣。
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

import npc
from fight import Build, fight
from progression import cum_cost, level_increment, subskill_cost, tier_upgrade_diff
from resolve import Params, UsageLog, s3_params

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
               "refuse_n", "winded_n", "sub_fee", "stack_acts", "sub_active", "ev_gen",
               "ev_dmg_dealt", "ev_reacted", "ev_ignored", "ev_dmg_taken", "ev_react_cost")

CORE = npc.load_core()
TPL = npc.load_templates()
TPL_BY = {t["name"]: t for t in TPL}


def write_csv(name, rows):
    path = os.path.join(OUT, name)
    fields = list(dict.fromkeys(k for r in rows for k in r))        # 各列欄位可不同:取聯集,保留首見順序
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
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
    """b0 vs b1 打 n 場(先手每場交替)。回傳兩側統計總和與全局計數。"""
    b0, b1, p, n, seed = args
    rng = random.Random(seed)
    acc = [dict.fromkeys(STAT_FIELDS, 0), dict.fromkeys(STAT_FIELDS, 0)]
    for a in acc:
        a.update(winded_fights=0, winded_sum=0, rounds=0,
                 **{f"n_{c}": 0 for c in CATS}, **{f"d_{c}": 0 for c in CATS})
    g = dict(n=n, wins=[0, 0], kills=[0, 0], draws=0, stalled=0, rounds=0,
             loser_heavy=0, loser_light=0, decided=0, hp_end=[0, 0],
             first_crit_n=0, first_crit_win=0, first_crit_hit_n=0, first_crit_hit_win=0)
    for i in range(n):
        f = fight(b0, b1, p, rng, first=i % 2)
        g["rounds"] += f.rounds
        if f.winner is None:
            g["draws"] += 1
            g["stalled"] += f.stalled
        else:
            g["wins"][f.winner] += 1
            g["kills"][f.winner] += f.kill
            loser = f.states[1 - f.winner]
            g["decided"] += 1
            g["loser_heavy"] += max(loser.heavy, 0)
            g["loser_light"] += max(loser.hp, 0)
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
            for c in CATS:
                a[f"n_{c}"] += st.cat_n[c]
                a[f"d_{c}"] += st.cat_dmg[c]
    return acc, g


def merge_sides(acc):
    return {k: acc[0][k] + acc[1][k] for k in acc[0]}


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
    """列 vs 行。回傳 dict:M 勝率(平手算半)、Dec 只計分勝負、D 平手率、R 平均回合、
    K 列方「擊殺」(敗方重傷位 ≤ 0)的場數比例、LH 敗方重傷位餘量、G 全局明細。"""
    jobs, keys = [], []
    for i, a in enumerate(builds):
        for j, b in enumerate(builds):
            if i < j:
                jobs.append((a, b, p, n, seed + 1000 * i + j))
                keys.append((i, j))
    res = dict(zip(keys, pool.map(run_pair, jobs)))
    N = len(builds)
    z = lambda v: [[v] * N for _ in builds]
    M, Dec, R, D, K, LH = z(0.5), z(float("nan")), z(0.0), z(0.0), z(0.0), z(float("nan"))
    for (i, j), (acc, g) in res.items():
        w0, w1, dr = g["wins"][0] / n, g["wins"][1] / n, g["draws"] / n
        M[i][j], M[j][i] = w0 + dr / 2, w1 + dr / 2
        Dec[i][j], Dec[j][i] = div(w0, w0 + w1), div(w1, w0 + w1)
        R[i][j] = R[j][i] = g["rounds"] / n
        D[i][j] = D[j][i] = dr
        K[i][j], K[j][i] = g["kills"][0] / n, g["kills"][1] / n
        LH[i][j] = LH[j][i] = div(g["loser_heavy"], g["decided"])
    return dict(M=M, Dec=Dec, R=R, D=D, K=K, LH=LH)


def matrix_rows(tag, names, mt, md, extra):
    """把一對(失能、死亡)矩陣攤成列。mt = end_on 'incap' 的結果,md = 'death' 的結果。"""
    rows = []
    for i in range(len(names)):
        for j in range(len(names)):
            if i != j:
                rows.append({**extra, "row": names[i], "col": names[j],
                             "incap_win": r4(mt["M"][i][j]), "incap_win_decisive": r4(mt["Dec"][i][j]),
                             "incap_draw": r4(mt["D"][i][j]), "incap_rounds": round(mt["R"][i][j], 2),
                             "kill_rate_as_ruled": r4(mt["K"][i][j]),
                             "loser_heavy_left_as_ruled": r4(mt["LH"][i][j]),
                             "death_win": r4(md["M"][i][j]), "death_win_decisive": r4(md["Dec"][i][j]),
                             "death_draw": r4(md["D"][i][j]), "death_rounds": round(md["R"][i][j], 2)})
    return rows


# ---------------------------------------------------------------------------
# E1 消耗格與喘點(散修互毆:難度 1、技能 1–4 級、副技能 ≤ 1 級)
# ---------------------------------------------------------------------------

def e1_build(level, subs, name="甲"):
    kw = dict(link_w2p=1, link_p2w=1, counter=1) if subs else {}
    return Build(name, weapon=level, step=level, dodge=level, parry=level, block=level,
                 shield="small", step_def="dodge", **kw)


def e1(n=3000):
    thrs = (2, 3, 5, INF)
    levels = (1, 2, 3, 4)
    # (標籤, 持有副技能, 發動策略, 反應策略, 發動優先序)
    modes = (("無副技能", False, "always", "always", "link_first"),
             ("持有·發動(link 先)", True, "always", "always", "link_first"),
             ("持有·發動(反擊先)", True, "always", "always", "counter_first"),
             ("持有·發動(link 先)·不反應", True, "always", "never", "link_first"),
             ("持有·不發動", True, "never", "always", "link_first"))
    jobs, meta = [], []
    for lv in levels:
        for thr in thrs:
            for label, subs, act, rea, order in modes:
                p = s3_params(act_threshold=thr, activate=act, react=rea, sub_order=order)
                jobs.append((e1_build(lv, subs, "甲"), e1_build(lv, subs, "乙"), p, n, SEED + len(jobs)))
                meta.append((lv, thr, label))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (lv, thr, label), (acc, g) in zip(meta, res):
        s = merge_sides(acc)
        n_dmg = s["dmg_total"] + s["ev_dmg_dealt"]
        decided = g["decided"]
        rows.append({
            "level": lv, "act_threshold": THR_LABEL[thr], "subskills": label,
            "actions_per_round": r4(div(s["acts"], s["rounds"])),
            "attacks_per_round": r4(div(s["atk_acts"], s["rounds"])),
            "defense_acts_per_round": r4(div(s["def_acts"], s["rounds"])),
            "sub_fee_per_round": r4(div(s["sub_fee"], s["rounds"])),
            "sub_active_per_round": r4(div(s["sub_active"], s["rounds"])),
            "first_winded_round_mean": r4(div(s["winded_sum"], s["winded_fights"])),
            "pct_fighters_winded": r4(s["winded_fights"] / (2 * n)),
            "atk_roll_success": r4(div(s["succ"], s["att"])),
            "not_fully_dodged": r4(div(s["thru"], s["att"])),
            "net_hit_rate": r4(div(s["dmg_hit"], s["att"])),
            "parry_ok_rate": r4(div(s["parry_ok"], s["parry_try"])),
            "fight_rounds_mean": round(g["rounds"] / n, 2),
            "draw_rate": r4(g["draws"] / n),
            "incap_decided_rate": r4(decided / n),
            "kill_rate_as_ruled": r4(sum(g["kills"]) / n),
            "loser_heavy_left_mean": r4(div(g["loser_heavy"], decided)),
            "loser_light_left_mean": r4(div(g["loser_light"], decided)),
            "counter_events_per_fight": r4(s["ev_gen"] / n),
            "counter_reacted_share": r4(div(s["ev_reacted"], s["ev_gen"])),
            "counter_dmg_taken_per_fight": r4(s["ev_dmg_taken"] / n),
            "counter_dmg_share_of_damage": r4(div(s["ev_dmg_dealt"], n_dmg)),
            "counter_react_cost_per_event": r4(div(s["ev_react_cost"], s["ev_gen"])),
        })
    write_csv("s3_e1_cost_scan.csv", rows)

    fig, axes = plt.subplots(1, 3, figsize=(17, 4.8))
    labels = [m[0] for m in modes]
    for ax, col, ttl in zip(axes, ("actions_per_round", "first_winded_round_mean", "net_hit_rate"),
                            ("每回合平均動作數", "首次喘的回合", "淨命中率(造成傷害的出手 / 出手)")):
        for lb, c in zip(labels, ("C0", "C1", "C4", "C3", "C2")):
            for thr, ls in zip((3, INF), ("-", "--")):
                ys = [next(r[col] for r in rows if r["level"] == lv and r["subskills"] == lb
                           and r["act_threshold"] == THR_LABEL[thr]) for lv in levels]
                ax.plot(levels, ys, "o" + ls, color=c, label=f"{lb} 門檻{THR_LABEL[thr]}")
        ax.set_xticks(levels)
        ax.set_xlabel("各技能等級")
        ax.set_title(ttl, fontsize=10)
    axes[0].legend(fontsize=6)
    fig.suptitle("E1 散修互毆(難度 1、同等級鏡像、小盾;實線門檻 3、虛線 ∞)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s3_e1.png"), dpi=115)
    plt.close(fig)
    write_json("s3_summary_e1.json", {"rows": rows})

    # 使用紀錄樣本:鏢師·中 vs 遊俠·中 一場
    log = UsageLog()
    a, b = npc.instantiate(TPL_BY["鏢師"], "中", CORE), npc.instantiate(TPL_BY["遊俠"], "中", CORE)
    fight(a, b, s3_params(act_threshold=3), random.Random(SEED), first=0, log=log)
    log.to_csv(os.path.join(OUT, "s3_e1_usage_log_sample.csv"))


# ---------------------------------------------------------------------------
# E2 NPC 模板矩陣
# ---------------------------------------------------------------------------

def alloc_levels(entries, budget, cap=4):
    """entries = [(技能名, 難度階, 權重)];依權重比例分配經驗,等級上限 cap(散修 1–4);
    零頭依序給最便宜的下一級。回傳 (levels, 未花經驗)。"""
    lv = {k: 0 for k, _, _ in entries}
    tier = {k: t for k, t, _ in entries}
    wt = {k: w for k, _, w in entries}
    for k in lv:
        while lv[k] < cap and cum_cost(lv[k] + 1, tier[k]) <= budget * wt[k]:
            lv[k] += 1
    left = budget - sum(cum_cost(lv[k], tier[k]) for k in lv)
    while True:
        cand = [k for k in lv if lv[k] < cap and level_increment(lv[k] + 1, tier[k]) <= left]
        if not cand:
            break
        k = min(cand, key=lambda k: (level_increment(lv[k] + 1, tier[k]), -wt[k]))
        left -= level_increment(lv[k] + 1, tier[k])
        lv[k] += 1
    return lv, left


def template_at_budget(t, budget):
    """模板在固定戰鬥經驗下的實例:戰鬥技能(主用武器 + 招架 / 格擋 / 閃避 / 步法)依
    模板「中」檔的累計成本占比分配;已持副技能先扣(主技能同級 1/10)。非戰鬥技能不花經驗(對戰鬥無作用)。"""
    ents = npc.combat_skills(t)
    sub_cost = sum(subskill_cost(s["level"], e["tier"]) for e in ents
                   for s in e.get("subskills", []) if s["name"])
    tot = sum(cum_cost(npc.grade_level(e["level"], "中"), e["tier"]) for e in ents)
    entries = [(e["skill"], e["tier"], cum_cost(npc.grade_level(e["level"], "中"), e["tier"]) / tot)
               for e in ents]
    lv, left = alloc_levels(entries, budget - sub_cost)
    full = {e["skill"]: lv.get(e["skill"], 0) for e in t["skills"]}
    b = npc.instantiate(t, "中", CORE, name=t["name"], levels=full)
    # 範圍外標記:任一戰鬥技能等級落在模板範圍之外
    out_of_range = [e["skill"] for e in ents
                    if not e["level"][0] <= full[e["skill"]] <= e["level"][1]]
    return b, dict(levels=lv, xp_unspent=round(left, 1), sub_cost=sub_cost, out_of_range=out_of_range)


def sub_flow_builds(budget):
    """副技能流:長劍 2 級(300)+ 招架 1 級(100,link / 反擊要有招架才有意義)+ 難度 1 副技能各 1 級:
    長劍↔招架 link 兩條、反擊、致命(各 10)。散修副技能上限 1 級,所以其餘經驗花不掉(A41)。
    發動策略是模擬假設(A43),並列三種:全發動 / 只發動 link / 只發動反擊(致命不單列,見 REPORT)。
    對照組:同樣的 2 + 1 級,不持有任何副技能。不足 440 經驗時回傳 None。"""
    need = cum_cost(2) + cum_cost(1) + 4 * subskill_cost(1)
    if budget < need:
        return None
    held = dict(weapon=2, parry=1, link_w2p=1, link_p2w=1, counter=1, crit_sub=1)
    flows = [Build("副技能流·全發動", **held),
             Build("副技能流·只link", subs_on=("link_w2p", "link_p2w"), **held),
             Build("副技能流·只反擊", subs_on=("counter",), **held)]
    ctrl = Build("主2級·無副技能", weapon=2, parry=1)
    return flows, ctrl, dict(xp_spent=need, xp_unspent=budget - need)


E2_VARIANTS = [
    ("門檻3·發動", dict(act_threshold=3, activate="always")),
    ("門檻3·不發動副技能", dict(act_threshold=3, activate="never")),
    ("門檻∞·發動", dict(act_threshold=INF, activate="always")),
]


def e2(n=10_000, n_b=8_000):
    rows, builds_rows, summary = [], [], {}
    with ProcessPoolExecutor() as pool:
        # ---- A. 模板原樣:弱 / 中 / 強 三檔,同檔互打 ----
        figA, axA = plt.subplots(2, 3, figsize=(18, 11))
        for gi, grade in enumerate(npc.GRADES):
            builds = [npc.instantiate(t, grade, CORE, name=t["name"]) for t in TPL]
            names = [b.name for b in builds]
            for t, b in zip(TPL, builds):
                lv = npc.level_map(t, grade)
                builds_rows.append({"part": "A", "tier_or_budget": grade, "build": t["name"],
                                    "levels": json.dumps(lv, ensure_ascii=False),
                                    "xp_total_all_skills": round(npc.xp_cost(t, lv)),
                                    "weapon_value": b.value("weapon"), "dodge_value": b.value("dodge"),
                                    "parry_value": b.value("parry"),
                                    "hp_cap": b.attr("Str") + b.attr("Con"),
                                    "regen": max(0, (b.attr("Con") - 10) // 2)})
            for vi, (vname, kw) in enumerate(E2_VARIANTS):
                pt = s3_params(**kw)
                pd = s3_params(end_on="death", **kw)
                mt = matrix(builds, pt, n, SEED + 11 * gi + 101 * vi, pool)
                md = matrix(builds, pd, n, SEED + 11 * gi + 101 * vi + 5000, pool)
                rows += matrix_rows("A", names, mt, md, {"part": "A", "tier_or_budget": grade, "variant": vname})
                summary[f"A/{grade}/{vname}"] = {
                    "names": names, "incap": mt["M"], "death": md["M"],
                    "monopoly_incap": [names[i] for i in monopoly(mt["M"])],
                    "monopoly_death": [names[i] for i in monopoly(md["M"])]}
                if vi == 0:
                    heat(axA[0][gi], mt["M"], names, f"失能 · {grade}檔 · {vname}\n全綠列:"
                         + (",".join(names[i] for i in monopoly(mt["M"])) or "無"))
                    heat(axA[1][gi], md["M"], names, f"打到死(擊殺) · {grade}檔 · {vname}\n全綠列:"
                         + (",".join(names[i] for i in monopoly(md["M"])) or "無"))
        figA.suptitle("E2-A NPC 模板(同檔互打,列勝率,平手算半)")
        figA.tight_layout()
        figA.savefig(os.path.join(OUT, "s3_e2a.png"), dpi=105)
        plt.close(figA)

        # ---- B. 同經驗:300 / 1000 / 2500 戰鬥經驗 ----
        budgets = (300, 1000, 2500)
        figB, axB = plt.subplots(2, 3, figsize=(21, 13))
        for bi, budget in enumerate(budgets):
            builds, names = [], []
            for t in TPL:
                b, info = template_at_budget(t, budget)
                builds.append(b)
                names.append(t["name"])
                builds_rows.append({"part": "B", "tier_or_budget": budget, "build": t["name"],
                                    "levels": json.dumps(info["levels"], ensure_ascii=False),
                                    "xp_unspent": info["xp_unspent"], "sub_cost": info["sub_cost"],
                                    "out_of_template_range": ",".join(info["out_of_range"]),
                                    "weapon_value": b.value("weapon"), "dodge_value": b.value("dodge"),
                                    "parry_value": b.value("parry")})
            sf = sub_flow_builds(budget)
            if sf:
                flows, ctrl, info = sf
                builds += flows + [ctrl]
                names += [b.name for b in flows] + [ctrl.name]
                for b in flows + [ctrl]:
                    builds_rows.append({"part": "B", "tier_or_budget": budget, "build": b.name,
                                        "levels": json.dumps({"長劍": b.weapon, "招架": b.parry}, ensure_ascii=False),
                                        "xp_unspent": info["xp_unspent"] if b is not ctrl else budget - 400,
                                        "weapon_value": b.value("weapon"), "dodge_value": b.value("dodge"),
                                        "parry_value": b.value("parry")})
            else:
                builds_rows.append({"part": "B", "tier_or_budget": budget, "build": "副技能流",
                                    "levels": "不可行(長劍 2 級已用盡 300)"})
            pt, pd = s3_params(act_threshold=3), s3_params(act_threshold=3, end_on="death")
            mt = matrix(builds, pt, n_b, SEED + 700 + 13 * bi, pool)
            md = matrix(builds, pd, n_b, SEED + 700 + 13 * bi + 5000, pool)
            rows += matrix_rows("B", names, mt, md, {"part": "B", "tier_or_budget": budget, "variant": "門檻3·發動"})
            summary[f"B/{budget}"] = {"names": names, "incap": mt["M"], "death": md["M"],
                                      "monopoly_incap": [names[i] for i in monopoly(mt["M"])],
                                      "monopoly_death": [names[i] for i in monopoly(md["M"])]}
            heat(axB[0][bi], mt["M"], names, f"失能 · 經驗 {budget}\n全綠列:"
                 + (",".join(names[i] for i in monopoly(mt["M"])) or "無"), fs=6)
            heat(axB[1][bi], md["M"], names, f"打到死(擊殺) · 經驗 {budget}\n全綠列:"
                 + (",".join(names[i] for i in monopoly(md["M"])) or "無"), fs=6)
        figB.suptitle("E2-B 同經驗(戰鬥技能依模板比例分配,等級上限 4;列勝率,平手算半)")
        figB.tight_layout()
        figB.savefig(os.path.join(OUT, "s3_e2b.png"), dpi=105)
        plt.close(figB)
    write_csv("s3_e2_matrix.csv", rows)
    write_csv("s3_e2_builds.csv", builds_rows)
    write_json("s3_summary_e2.json", summary)


# ---------------------------------------------------------------------------
# E3 攻守貨幣
# ---------------------------------------------------------------------------

def e3_builds(N):
    """總等級 N 平分給配置;單技能 ≤ 4 級(散修)。全押攻超出 4 級的部分溢給閃避(A42)。"""
    w = min(N, 4)
    return [
        Build("全押攻", weapon=w, dodge=N - w),
        Build("均衡", weapon=(N + 1) // 2, dodge=(N - (N + 1) // 2 + 1) // 2, parry=(N - (N + 1) // 2) // 2),
        Build("全押守", dodge=(N + 1) // 2, parry=N // 2),
        Build("全押步法閃避", step=(N + 1) // 2, dodge=N // 2, step_def="dodge"),
    ]


def e3(n=4_000):
    thrs = (3, INF)
    Ns = (3, 4, 6)
    modes = (("incap", {}), ("death", {"end_on": "death"}))
    jobs, meta = [], []
    for N in Ns:
        builds = e3_builds(N)
        for thr in thrs:
            for mname, mkw in modes:
                p = s3_params(act_threshold=thr, **mkw)
                for i in range(len(builds)):
                    for j in range(i + 1, len(builds)):
                        jobs.append((builds[i], builds[j], p, n, SEED + 3000 + len(jobs)))
                        meta.append((N, thr, mname, builds[i].name, builds[j].name))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (N, thr, mname, a, b), (acc, g) in zip(meta, res):
        w0, w1, dr = g["wins"][0] / n, g["wins"][1] / n, g["draws"] / n
        rows.append({"total_levels": N, "act_threshold": THR_LABEL[thr], "end_on": mname,
                     "row": a, "col": b, "row_win_rate": r4(w0 + dr / 2),
                     "row_win_rate_decisive": r4(div(w0, w0 + w1)), "draw_rate": r4(dr),
                     "mean_rounds": round(g["rounds"] / n, 2),
                     "kill_rate_as_ruled": r4(sum(g["kills"]) / n),
                     "row_light_end": round(g["hp_end"][0] / n, 2),
                     "col_light_end": round(g["hp_end"][1] / n, 2)})
    write_csv("s3_e3_attack_vs_defense.csv", rows)
    names = [b.name for b in e3_builds(4)]
    rank_rows = []
    for N in Ns:
        for thr in thrs:
            for mname, _ in modes:
                sub = [r for r in rows if r["total_levels"] == N and r["act_threshold"] == THR_LABEL[thr]
                       and r["end_on"] == mname]
                score = {k: [] for k in names}
                for r in sub:
                    score[r["row"]].append(r["row_win_rate"])
                    score[r["col"]].append(1 - r["row_win_rate"])
                rank_rows.append({"total_levels": N, "act_threshold": THR_LABEL[thr], "end_on": mname,
                                  **{k: r4(sum(v) / len(v)) for k, v in score.items()},
                                  "order": " > ".join(sorted(names, key=lambda k: -sum(score[k]) / len(score[k])))})
    write_csv("s3_e3_ranking.csv", rank_rows)
    cfg_rows = []
    for N in Ns:
        for b in e3_builds(N):
            cfg_rows.append({"total_levels": N, "build": b.name, "weapon": b.weapon, "step": b.step,
                             "dodge": b.dodge, "parry": b.parry})
    write_csv("s3_e3_builds.csv", cfg_rows)

    fig, axes = plt.subplots(2, len(thrs), figsize=(13, 8), sharey=True)
    for mi, (mname, _) in enumerate(modes):
        for ti, thr in enumerate(thrs):
            ax = axes[mi][ti]
            for k, c in zip(names, ("C3", "C0", "C2", "C4")):
                ys = [next(r[k] for r in rank_rows if r["total_levels"] == N and r["end_on"] == mname
                           and r["act_threshold"] == THR_LABEL[thr]) for N in Ns]
                ax.plot(Ns, ys, "o-", color=c, label=k)
            ax.axhline(0.5, color="gray", ls=":")
            ax.set_xticks(Ns)
            ax.set_ylim(0, 1)
            ax.set_xlabel("總等級")
            ax.set_title(f"{'失能' if mname == 'incap' else '打到死'},門檻 {THR_LABEL[thr]}", fontsize=9)
            if mi == 0 and ti == 0:
                ax.legend(fontsize=7)
    axes[0][0].set_ylabel("對其他三種的平均勝率(平手算半)")
    fig.suptitle("E3 攻守貨幣(散修:單技能 ≤ 4 級)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s3_e3.png"), dpi=120)
    plt.close(fig)
    write_json("s3_summary_e3.json", {"ranking": rank_rows})


# ---------------------------------------------------------------------------
# E8 任意重骰(【劍意】【刀意】)的量化
# ---------------------------------------------------------------------------

def e8(n=10_000):
    cases = (("遊俠", "劍意"), ("鏢師", "刀意"))
    thrs = (3, INF)
    opp = [npc.instantiate(t, "中", CORE, name=t["name"]) for t in TPL]
    jobs, meta = [], []
    for tname, yi in cases:
        t = TPL_BY[tname]
        base = npc.instantiate(t, "中", CORE, name=f"{tname}")
        wl = base.weapon
        # 變體:基準 / 加任意重骰 / 主技能 +1 級
        variants = {"基準": base,
                    f"+{yi}": replace(base, name=f"{tname}+{yi}", reroll_any=True),
                    "+1級主技能": replace(base, name=f"{tname}+1級", weapon=wl + 1,
                                         base={**base.base})}
        for thr in thrs:
            p = s3_params(act_threshold=thr)
            for vn, vb in variants.items():
                for o in opp:
                    jobs.append((vb, replace(o, name=o.name + "(對手)"), p, n, SEED + 8000 + len(jobs)))
                    meta.append((tname, yi, thr, vn, o.name))
            # 直接對決:+劍意 vs +1級
            jobs.append((variants[f"+{yi}"], variants["+1級主技能"], p, n, SEED + 8000 + len(jobs)))
            meta.append((tname, yi, thr, f"+{yi} vs +1級主技能", "(直接對決)"))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (tname, yi, thr, vn, on), (acc, g) in zip(meta, res):
        a = acc[0]
        w0, w1, dr = g["wins"][0] / n, g["wins"][1] / n, g["draws"] / n
        rows.append({"base": tname, "reroll_skill": yi, "act_threshold": THR_LABEL[thr],
                     "variant": vn, "opponent": on,
                     "win_rate": r4(w0 + dr / 2), "win_rate_decisive": r4(div(w0, w0 + w1)),
                     "draw_rate": r4(dr), "mean_rounds": round(g["rounds"] / n, 2),
                     "atk_roll_success": r4(div(a["succ"], a["att"])),
                     "crit_rate_per_attack": r4(div(a["crit_roll"], a["att"])),
                     "fumble_rate_per_attack": r4(div(a["fumble"], a["att"])),
                     "attacks_per_fight": r4(a["att"] / n),
                     "net_hit_rate": r4(div(a["dmg_hit"], a["att"])),
                     "sub_fee_per_fight": r4(a["sub_fee"] / n)})
    write_csv("s3_e8_reroll_any.csv", rows)
    # 成本(經驗):劍意難度 3,需要持有難度階 ≥ 3(schema)
    cost_rows = []
    for tname, yi in cases:
        t = TPL_BY[tname]
        w = npc.weapon_entry(t)
        L = npc.grade_level(w["level"], "中")
        cost_rows.append({
            "base": tname, "weapon_level": L,
            "plus_1_level_xp": level_increment(L + 1, 1),
            "reroll_skill": yi,
            "reroll_1級本身_xp(1/10 × 難度3倍率1.35)": round(subskill_cost(1, 1) * 1.35, 2),
            "升難度3補差額_xp(已投入 × 0.35)": round(tier_upgrade_diff(L, 1, 3), 2),
            "升難度3鑽研幅度": 200,
            "備註": "副技能自身難度倍率待定(A15 / progression.md);此處按難度倍率估,非裁決"})
    write_csv("s3_e8_costs.csv", cost_rows)

    fig, axes = plt.subplots(1, 3, figsize=(17, 4.8))
    sel = [r for r in rows if r["act_threshold"] == "3" and r["opponent"] != "(直接對決)"]
    for ax, col, ttl in zip(axes, ("win_rate", "atk_roll_success", "crit_rate_per_attack"),
                            ("對六種模板中檔的平均勝率", "攻擊擲骰成功率", "致命率(每次出手)")):
        w = 0.25
        for ci, (tname, yi) in enumerate(cases):
            for vi, vn in enumerate(("基準", f"+{yi}", "+1級主技能")):
                vals = [r[col] for r in sel if r["base"] == tname and r["variant"] == vn]
                ax.bar(ci + (vi - 1) * w, sum(vals) / len(vals), w, color=("C7", "C1", "C0")[vi])
        ax.set_xticks(range(len(cases)))
        ax.set_xticklabels([f"{c[0]}({c[1]})" for c in cases])
        ax.set_title(ttl, fontsize=10)
    axes[0].legend(["基準", "+重骰", "+1 級"], fontsize=8)
    fig.suptitle("E8 任意重骰量化(門檻 3;每格 6 種對手 × 1 萬場平均)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s3_e8.png"), dpi=120)
    plt.close(fig)
    write_json("s3_summary_e8.json", {"rows": rows, "costs": cost_rows})


# ---------------------------------------------------------------------------
# E7 高手互打(次要):5 級主技能 + 疊 5 級輔助,疊加佔格付費
# ---------------------------------------------------------------------------

def master(name, stacks=(("戰鬥反應", 5), ("內功", 5))):
    return Build(name, weapon=5, step=5, dodge=5, parry=5, block=5, shield="large",
                 step_def="dodge", crit_bonus=3, stacks=stacks)


def e7(n=4_000):
    thrs = (3, INF)
    pairs = (("疊2 vs 疊2", master("高手甲"), master("高手乙")),
             ("疊2 vs 不疊", master("高手甲"), master("高手乙", stacks=())),
             ("疊1 vs 不疊", master("高手甲", stacks=(("戰鬥反應", 5),)), master("高手乙", stacks=())))
    jobs, meta = [], []
    for pn, a, b in pairs:
        for thr in thrs:
            p = s3_params(act_threshold=thr)
            jobs.append((a, b, p, n, SEED + 7000 + len(jobs)))
            meta.append((pn, thr))
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs))
    rows = []
    for (pn, thr), (acc, g) in zip(meta, res):
        s = merge_sides(acc)
        a0 = acc[0]
        n_hits = sum(s[f"n_{c}"] for c in CATS)
        row = {"pair": pn, "act_threshold": THR_LABEL[thr],
               "row_win_rate": r4((g["wins"][0] + g["draws"] / 2) / n),
               "draw_rate": r4(g["draws"] / n), "mean_rounds": round(g["rounds"] / n, 2),
               "stack_acts_per_attack_row": r4(div(a0["stack_acts"], a0["att"])),
               "attacks_per_fight": r4(s["att"] / n),
               "atk_roll_success": r4(div(s["succ"], s["att"])),
               "crit_rate_per_attack": r4(div(s["crit_roll"], s["att"])),
               "full_dodge_rate_given_success": r4(div(s["full_dodged"], s["succ"])),
               "first_crit_side_wins": r4(div(g["first_crit_win"], g["first_crit_n"])),
               "first_crit_hit_side_wins": r4(div(g["first_crit_hit_win"], g["first_crit_hit_n"]))}
        for c in CATS:
            row[f"hits_share_{c}"] = r4(div(s[f"n_{c}"], n_hits))
        rows.append(row)
    write_csv("s3_e7_master.csv", rows)
    write_json("s3_summary_e7.json", {"rows": rows})
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    labels = [f"{r['pair']}\n門檻{r['act_threshold']}" for r in rows]
    x = range(len(rows))
    axes[0].bar(x, [r["mean_rounds"] for r in rows])
    axes[0].set_title("平均回合數")
    axes[1].bar(x, [r["row_win_rate"] for r in rows])
    axes[1].axhline(0.5, color="gray", ls=":")
    axes[1].set_title("甲方勝率(平手算半)")
    axes[2].bar(x, [r["full_dodge_rate_given_success"] for r in rows])
    axes[2].set_title("擲骰成功後被全閃的比例")
    for ax in axes:
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=6)
    fig.suptitle("E7(次要)高手:5 級主技能 + 疊 5 級輔助(戰鬥反應、內功),疊加佔腦格付費")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s3_e7.png"), dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# E6 屬性引用統計
# ---------------------------------------------------------------------------

CATEGORIES = {
    "近戰武器": ["長劍", "單刀", "長槍", "長棍", "拳腳"],
    "防禦": ["招架", "格擋", "閃避", "步法"],
    "身體": ["攀爬", "游泳", "奔跑", "潛行", "騎術", "內功"],
    "感知反應": ["戰鬥反應", "臨場反應", "危機反應", "察覺", "搜索"],
    "知識": ["醫術", "藥理", "讀寫", "算學", "江湖見聞", "律令"],
    "社交": ["說服", "威嚇", "欺瞞", "議價", "化妝", "表演"],
    "生活": ["烹飪", "鍛造", "手藝", "開鎖", "野外求生", "追蹤", "駕車", "賭術"],
}
ATTRS = ("Str", "Con", "Ski", "Agi", "Int", "Wis", "Cha")


def e6():
    cat_of = {s: c for c, ss in CATEGORIES.items() for s in ss}
    assert set(cat_of) == set(CORE), set(CORE) ^ set(cat_of)
    N = len(CORE)
    combat = [s for c in ("近戰武器", "防禦") for s in CATEGORIES[c]]
    rows = []
    # 模板引用:每個模板技能條目依「中檔等級」加權
    tpl_refs = {a: 0 for a in ATTRS}
    tpl_levels = {a: 0 for a in ATTRS}
    tpl_entries = 0
    for t in TPL:
        for e in t["skills"]:
            attrs_ = npc.skill_attrs(e, CORE)
            tpl_entries += 1
            for a in attrs_:
                tpl_refs[a] += 1
                tpl_levels[a] += npc.grade_level(e["level"], "中")
    for a in ATTRS:
        sk = [s for s in CORE if a in CORE[s]["attributes"]]
        row = {"attribute": a, "skills_referencing": len(sk), "share_of_skills": r4(len(sk) / N),
               "expected_if_uniform": r4(N * 3 / 7), "vs_uniform": r4(len(sk) / (N * 3 / 7)),
               "in_combat_chain_9": sum(1 for s in sk if s in combat),
               "in_noncombat": sum(1 for s in sk if s not in combat),
               "template_entries": tpl_refs[a], "template_level_weighted": tpl_levels[a]}
        for c in CATEGORIES:
            row[f"cat_{c}"] = sum(1 for s in sk if cat_of[s] == c)
        row["base_tier_dist"] = json.dumps({1: len(sk)})          # 核心表全部難度 1 起始
        row["subskill_difficulty_dist"] = json.dumps(
            {1: sum(len(CORE[s]["subskills"]) for s in sk)})
        rows.append(row)
    rows.sort(key=lambda r: r["skills_referencing"])
    write_csv("s3_e6_attribute_refs.csv", rows)
    # 共現:兩屬性同時出現在多少技能
    co = []
    for i, a in enumerate(ATTRS):
        for b in ATTRS[i + 1:]:
            co.append({"a": a, "b": b, "skills_with_both": sum(
                1 for s in CORE.values() if a in s["attributes"] and b in s["attributes"])})
    write_csv("s3_e6_cooccurrence.csv", co)
    write_json("s3_summary_e6.json", {"rows": rows, "n_skills": N, "n_template_entries": tpl_entries})

    fig, ax = plt.subplots(figsize=(9, 4.8))
    order = [r["attribute"] for r in rows]
    bottom = [0] * len(order)
    for c, col in zip(CATEGORIES, plt.cm.tab10.colors):
        vals = [next(r[f"cat_{c}"] for r in rows if r["attribute"] == a) for a in order]
        ax.bar(order, vals, bottom=bottom, label=c, color=col)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.axhline(N * 3 / 7, color="k", ls="--", lw=1, label=f"均勻 {N * 3 / 7:.1f}")
    ax.set_ylabel("引用該屬性的技能數")
    ax.legend(fontsize=7, ncol=2)
    ax.set_title("E6 各屬性被核心技能表引用次數(依類別堆疊)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "s3_e6.png"), dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    which = sys.argv[1:] or ["e6", "e1", "e2", "e3", "e8", "e7"]
    for w in which:
        print("running", w, flush=True)
        globals()[w]()
