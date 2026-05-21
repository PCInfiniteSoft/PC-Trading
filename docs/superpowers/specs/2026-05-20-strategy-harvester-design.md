# Strategy Harvester — Design Spec
**Date:** 2026-05-20
**Status:** Draft (brainstorming in progress)
**Phase:** Sub-project 1 of 3

---

## Goal

Scale PC Trading bot from 2 symbols (BTC, XAU) to **5 symbols in Phase 2** and **10 symbols in Phase 3** by:
1. Discovering trading strategies from ForexFactory
2. Extracting indicator/condition ideas into the existing ANALYST scoring system
3. Backtesting to validate before going live

---

## Full Pipeline (3 Sub-projects)

```
[Sub-project 1] Strategy Harvester
    Scrape → Excel filter → GPT extract → JSON + code skeleton

[Sub-project 2] Backtest Enhancement
    strategy JSON + code → backtest.py → WR/RR report → Top 3 selection

[Sub-project 3] Bot Integration
    Top strategies → advanced_indicators.py → ai_engine.py ANALYST → SYMBOLS_CONFIG
```

Sub-projects are independent. Output of 1 feeds into 2, output of 2 feeds into 3.

---

## Sub-project 1: Strategy Harvester

### Approach: Crawl → Excel → Filter → Extract

**Why this approach:**
- Full auto crawl brings too many irrelevant threads
- Manual URL curation takes too long
- Excel middle step lets user sort by views/replies and quickly filter relevant threads before spending GPT tokens

### Search Strategy (ForexFactory)

Search ตาม keyword bucket แทนการ crawl ทั้ง forum — ได้ผลที่ตรงกว่า ใช้ page น้อยกว่า

**Filter settings (คงที่ทุก bucket):**
| Setting | Value | เหตุผล |
|---------|-------|--------|
| Forums | Trading Systems เท่านั้น | ตัด discussion/journal/rookie talk |
| Search In | Threads | เอา thread starter เท่านั้น ไม่เอา reply mention |
| Sort By | Relevancy | strategy เก่าๆ ยังมีคุณค่า ไม่ sort by Date |
| Date Range | ไม่ filter | เอา classic strategy ด้วย |

**Keyword Buckets:**
| Bucket | Keywords | Target threads |
|--------|----------|---------------|
| XAU/Gold | "XAU", "Gold", "XAUUSD" | 100 |
| BTC | "BTC", "Bitcoin", "BTCUSD" | 100 |
| Breakout | "breakout strategy" | 50 |
| RSI | "RSI strategy", "RSI system" | 50 |

รวม ~300 threads (หลัง deduplicate by URL น่าจะเหลือ ~200-250)

### Flow

```
Phase 1 — Keyword Search Crawl (cheap, no GPT)
  1. Playwright login (ForexFactory account)
  2. For each keyword bucket:
     a. Navigate to search with filters: Forum=Trading Systems, Search In=Threads, Sort=Relevancy
     b. Collect thread metadata: title, url, replies, last_active
     c. Paginate until hit target count or no more results
  3. Deduplicate by URL across all buckets
  4. Export strategy_candidates.xlsx
     columns: title | url | replies | last_active | bucket
  5. Sort by replies descending within each bucket

Phase 2 — Human Filter (in Excel, ~30 min)
  6. User reviews — delete irrelevant rows, save
  7. Expected remaining: 80–120 threads

Phase 3 — Content Extraction (GPT)
  8. Script reads filtered Excel
  9. Fetch first post content of each remaining thread
  10. GPT extract → strategies_library.json
  11. GPT generate code skeleton per strategy
  12. User reviews code skeletons
  13. Approved skeletons merged into advanced_indicators.py
```

**Rationale for 3-phase split:**
- Phase 1 costs ~0 GPT tokens (just HTML scraping)
- Phase 3 GPT cost proportional to threads that survive filter (~100 threads)
- User controls quality in Phase 2

### Output Files

| File | Description |
|------|-------------|
| `strategy_candidates.xlsx` | Raw thread list from forum crawl |
| `strategies_library.json` | Structured strategies after GPT extraction |
| `skeletons/strategy_<n>.py` | Auto-generated indicator function skeletons |

### strategies_library.json Schema

```json
{
  "strategies": [
    {
      "id": "ff_001",
      "title": "Thread title",
      "url": "https://forexfactory.com/...",
      "symbol_hint": "XAUUSD",
      "timeframe": "M5",
      "indicators": ["RSI", "EMA20", "ATR"],
      "entry_conditions": {
        "buy": "RSI < 30 AND price above EMA20",
        "sell": "RSI > 70 AND price below EMA20"
      },
      "exit_conditions": {
        "tp_ratio": "2:1 RR",
        "sl": "ATR * 1.5"
      },
      "analyst_bonus_idea": "Add +1 if RSI divergence detected",
      "notes": "Author claims 65% WR on 6 months"
    }
  ]
}
```

### Code Skeleton Example

```python
def get_ff001_score(symbol, order_type, timeframe=mt5.TIMEFRAME_M5) -> dict:
    """
    FF-001: [Thread title] — auto-generated skeleton, review before use.
    Entry idea: RSI divergence bonus (+0/+1)
    """
    result = {"score": 0, "reason": "Insufficient data"}
    # TODO: implement logic
    return result
```

---

## Open Questions

- **[x] กี่ thread ต่อรอบ?** — search by keyword bucket, target 100+100+50+50 = ~300 threads (หลัง dedup ~200-250), GPT extract เฉพาะที่ผ่าน human filter (~80-120)
- **[x] Search filter settings** — Forum=Trading Systems only, Search In=Threads, Sort=Relevancy, no date range
- **[x] ForexFactory account** — สมัครแล้ว (username: gambit4217)
- **[ ] Replies threshold** — ตัด thread ที่ replies ต่ำกว่าเท่าไหร่? เช่น 200, 500, 1000? (user ตัดสินใจใน Excel Phase 2)
- **[ ] Playwright mode** — headless หรือ headed?
- **[ ] GPT model** — GPT-4o-mini หรือ GPT-4o สำหรับ extraction?

---

## Sub-project 2: Backtest Enhancement (placeholder)

- รับ `strategies_library.json` + code skeletons ที่ผ่าน review แล้ว
- Extend `backtest.py` / `run_scenarios.py` ให้รัน scenario ต่อ strategy+symbol combo
- Output: WR, RR, total trades, max DD ต่อ combo
- Selection criteria: Top 3 ตาม WR × RR (composite score)
- Window: 3 months historical data (ใช้ MT5 ที่มีอยู่แล้ว)
- Detail spec: เขียนหลัง Sub-project 1 เสร็จ

---

## Sub-project 3: Bot Integration (placeholder)

- นำ top strategies ใส่ `advanced_indicators.py`
- Wire เข้า `ai_engine.py` ANALYST scoring (เหมือน SCOUT, Breakout, Pullback bonus)
- เพิ่ม symbol ใหม่ใน `bot_config.py` SYMBOLS_CONFIG พร้อม tune parameter
- Demo 2-3 สัปดาห์ก่อน live
- Detail spec: เขียนหลัง Sub-project 2 เสร็จ
