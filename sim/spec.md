# 模擬規格

目的:讓數字替手感說話。所有待定參數進這裡當變數掃,圖出來 KK 挑。

## Session 2 變更摘要(給重跑的人)
resolution.md 改了防禦鏈結構與消耗模型,Session 1 的 `fight.py` 回合迴圈要重寫,`resolve.py` 大致可留:
1. 招架、格擋不再侵蝕 T;改做**傷害對抗**(武器骰 / 盾值)。只有閃避侵蝕 T。
2. 固定 `cost_layer` 作廢。改為**消耗格**:手/腳/腦三格,回合內同格第 k 動耗 k 點;技能等級給 `ceil(level/2)` 次低價(各 1 點)。回合末歸零。
3. 新增步法(Step):幅度轉下一動技能值加值,吃腳格。
4. 盾值 = 1 或 2 + ⌊(Str−10)/2⌋。
5. ambiguities A0–A13、A21、A22 已裁,見 open-questions 底部表。開關改成預設值,不必再掃。
6. 攻方策略從「貪婪疊到付不起」改為「每動有價值門檻」:門檻 `act_threshold` 當參數掃。

## resolve.py

純函數,無隨機狀態外洩;隨機源可注入(測試用固定 seed)。

```
roll(rng) -> int                       # 1..100,00 記為 100
check(skill, rng, crit_bonus=0) -> Result   # success, margin, crit, fumble
  crit  : success and roll ≤ ceil(skill × 0.10) + crit_bonus
  fumble: roll == 100 or (skill < 100 and roll > 100 − ceil((100 − skill) × 0.10))
overflow(skill) -> int                 # max(skill − 100, 0)
step(skill, rng) -> int                # 成功回傳幅度 S,失敗 0
dodge(T, skill, level, rng) -> (T_left, full_dodge)   # 全閃用原始 m;否則 T −= min(m, 10×level)
parry(skill, weapon_die, rng) -> D_def | None          # 成功回傳武器骰
block(skill, shield_value, rng) -> D_def | None        # 技能 +10;成功回傳盾值
difficulty(D, aids, rng) -> D_left
damage(weapon_die, overflow, crit, D_def, rng) -> int  # max(0, 骰(+滿骰) + overflow − D_def)
```

每次 `check` 寫一行使用紀錄(格式見 resolution.md)。

### 回合狀態(fight.py)
```
slots = {hand: 0, foot: 0, brain: 0}        # 本回合各格已用次數,回合末歸零
cheap = {weapon_name: ceil(level/2), ...}   # 本回合各技能剩餘低價次數
cost(slot, skill) = 1 if cheap[skill] > 0 else slots[slot] + 1
```

### 參數物件 `Params`
```
act_threshold     # 攻方/守方「一動最多願付幾點」,掃 {2,3,5,∞}
regen_stamina     # 回合間體力回復(預設 0)
hp                # 常數 30(待公式)
shield            # 'small' | 'large' | None
dual_wield        # 無技能 / 1 級 / 抵銷完
weight_k          # 負重上限 Con × k(預設不啟用)
xp_reward_f       # 'linear' | 'sqrt' | 'capped'
free_xp_ratio     # 0.1 ~ 0.2
tier_cost         # 升級成本形狀(佔位 100 × n)
tier_slots        # 難度階 → 每級副技能槽
convert_cap       # 換池每級上限(預設 10)
```

## 範圍聲明
本輪模擬只驗證**解算層**(一條數線上的公式行為)。系統的上限在**技能樹層**(哪些技能存在、副技能怎麼掛、疊加白名單、難度階定價),目前尚未建立。REPORT 的結論限於「公式在這些數字下的行為」,不得推到「系統平衡」。樹建好後 E2 須用真實配置重跑。

## 模擬用技能集(與技能表無關,僅供本輪)

| 技能 | 三屬性 | 格 | 位置 |
|---|---|---|---|
| 長劍 | Str+Ski+Agi | 手 | 攻擊、招架(1d8) |
| 短劍 | Ski+Agi+Wis | 手 | 副手攻擊、招架(1d4) |
| 步法 | Agi+Ski+Con | 腳 | 幅度轉加值 |
| 閃避 | Agi+Con+Wis | 腳 | 侵蝕 T |
| 格擋 | Ski+Con+Str | 手 | 盾值吸收 |
| 戰鬥反應 | Agi+Ski+Wis | 腦 | 先攻、interrupt(E2 用) |
| 內功 | Con+Cha+Wis | — | 內力池(E2 內力流用) |

均值角色(全 14)底值皆 42。盾是裝備,有無盾屬配置。
E1/E3/E7 用長劍 + 步法/閃避/招架/格擋,先攻固定輪流。

## E2 配置原型(同經驗、不同花法)
- 純攻:全押長劍,防禦留底值
- 純守:閃避、招架各半,長劍留底值
- 均衡帶盾:長劍、閃避、招架、格擋平分,大盾
- 步法賭徒:步法、閃避各半,長劍留底值——疊步法賭全閃
- 技術流:長劍中等,副技能三條(schema example)
- 內力流:長劍中等 + 內功
- 雙持:長劍 + 短劍 + 雙持一級
- 賢者 vs 純系:術的解算未定,本輪不跑

## 實驗

### E1 消耗格與喘點
- 52 vs 52,各技能一級(低價 1 次)。
- `act_threshold ∈ {2,3,5,∞}` × `regen_stamina ∈ {0,3,6}`。
- 輸出:每回合平均動作數、首次放棄想做的動作在第幾回合、淨命中率。
- 另跑各技能三級(低價 2 次)與五級(低價 3 次),看等級買動作預算的效果。

### E2 等經驗配置矩陣(核心題)
- 固定總經驗 500 / 1500 / 5000,上列配置原型兩兩一萬場。
- 升級成本佔位 100 × n;副技能免費(A15 待定,註明)。
- 找壟斷解:任一列全綠即為洞。Session 1 的內力流壟斷是否因內力池公式改而消失。

### E3 攻守貨幣(重跑)
- Session 1 結論:全押攻必勝,與疊加無關,因守方等級買的是「上限」而攻方買的是「期望」。
- 新結構下守方多了步法賭全閃、招架格擋改傷害對抗。重跑總等級 4 / 8 平分三配置,看排序是否改變。
- 若全押攻仍壟斷,下一個嫌疑是生命值 30 太低(命中兩三次就死,守不守無差)。加跑 hp ∈ {30, 60, 100}。

### E4 進度曲線
- Session 1 已跑,結論:牆只在「固定對手 + 遞增成本」出現。待 KK 定 tier_cost 形狀後重跑,本輪不跑。

### E5 可塑性
- 暫不跑。

### E6 屬性引用統計(技能表填完後)
- 各屬性被引用次數、所掛技能的難度階分佈。低引用或只掛低階者 = 下一個儀容。

### E7 高手互打(重跑)
- Session 1 結論:三層各侵蝕 50,任何 T 削得乾,99.8% 平手。新結構下只有閃避侵蝕,招架格擋是骰子對抗,平手應消失。
- 150+5 vs 150+5,各技能五級(低價 3 次),hp ∈ {30, 60, 100}。
- 輸出:平均回合數、先致命者勝率、傷害來源分解(穿透招架 / 全閃失敗 / 致命滿骰)。

## 輸出
- 每實驗一個 CSV + 一張圖(matplotlib),放 `sim/out/`,檔名加 `s2_` 前綴,不覆蓋 Session 1。
- `sim/REPORT.md` 加一節「Session 2」,每實驗三句話——跑了什麼、看到什麼、建議改哪個參數。不下設計結論。
- 新的模糊處追加到 `design/ambiguities.md`,編號從 A23 起。
