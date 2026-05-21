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

### Flow

```
Phase 1 — Metadata Crawl (cheap, no GPT)
  1. Playwright login (ForexFactory account)
  2. Crawl all 86 pages of Trading Systems forum (~3,427 threads)
  3. Collect metadata only: title, url, replies, last_active
  4. Export strategy_candidates.xlsx
     columns: title | url | replies | last_active
  5. Sort by replies descending

Phase 2 — Human Filter (in Excel)
  6. User filters by keyword: Gold, XAU, BTC, Bitcoin, EUR, GBP, breakout, RSI, etc.
  7. User cuts threads with replies below personal threshold (e.g. < 500)
  8. Save filtered Excel → typically 50–100 threads remain

Phase 3 — Content Extraction (GPT)
  9. Script reads filtered Excel
  10. Fetch first post content of each remaining thread
  11. GPT extract → strategies_library.json
  12. GPT generate code skeleton per strategy
  13. User reviews code skeletons
  14. Approved skeletons merged into advanced_indicators.py
```

**Rationale for 3-phase split:**
- Phase 1 costs ~0 GPT tokens (just HTML scraping)
- Phase 3 GPT cost proportional to threads that survive filter, not all 3,427
- User controls quality threshold via replies cutoff

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

- **[x] กี่ thread ต่อรอบ?** — crawl metadata ทั้งหมด ~3,427 threads (86 หน้า), GPT extract เฉพาะ subset ที่ผ่าน human filter (คาดว่า 50–100 thread)
- **[ ] Replies threshold** — ตัด thread ที่ replies ต่ำกว่าเท่าไหร่? เช่น 200, 500, 1000? (กระทบจำนวน thread ที่ GPT ต้อง process)
- **[ ] Playwright mode** — headless หรือ headed? (headed ง่าย debug กว่า แต่ต้องมี display)
- **[ ] GPT model** — GPT-4o-mini (ถูก, เร็ว) หรือ GPT-4o (แม่นกว่า แต่แพงกว่า) สำหรับ extraction?
- **[ ] ForexFactory account** — สมัครแล้วหรือยัง?

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
