# D100 TRPG 系統重建

KK 設計五年、迭代十餘版、已由玩家繼承的 d100 系統,以 LLM 協助重做。

## 目錄
```
design/principles.md     憲法。衝突時以此為準。
design/attributes.md     七屬性、池、家、三屬性校準
design/resolution.md     判定、幅度侵蝕鏈、溢出、致命、使用紀錄
design/progression.md    兩池經驗、用則長、創角
design/skill-schema.json 技能資料庫 schema + 範例
design/open-questions.md 待定事項(設計/模擬/確認)
sim/spec.md              resolve.py 規格與實驗
design/systems.md        近戰與法術:池的線×階×源、school 對照、八系、反制、三層殼、距離
play/README.md           模型 DM:引導創角、間隔期單人戲、NPC 模板
```

## 雲端 session 派工(建議順序)

### Session 1:解算器與模擬
```
讀 design/ 全部與 sim/spec.md。
實作 sim/resolve.py(純函數、可注入 rng、每次 check 寫使用紀錄)。
依 spec 跑 E1、E3、E4、E7(E2、E5 等參數初值定了再跑)。
輸出到 sim/out/,寫 sim/REPORT.md。
不下設計結論。規則沒講清楚的地方寫進 design/ambiguities.md。
開 PR。
```

### Session 2:技能表(核心 + 武俠模組)
```
讀 design/principles.md、attributes.md、skill-schema.json。
產 skills/core.json(六十到百條跨時代技能)與 skills/module-wuxia.json。
只填 schema 欄位。禁止自創數值、加機制、抄既有系統敘述。
副技能總效果不得超出預算;超出者寫進 ambiguities.md,不自行調整。
完成後跑 E6(屬性引用統計),附在 PR。
```

### Session 3(之後):模型 DM 試跑
```
用本系統跑十場。產出不是故事,是規則的 bug report:
每次規則卡住、模糊、要查表、DM 必須臨場裁的地方,一條一筆。
```

## 給填表者的三條硬規矩
1. 一切 % 皆為百分點。
2. 填表不下判斷。
3. 「有的是 A、有的是 B」不是規則,是 bug;寫進 ambiguities.md。
