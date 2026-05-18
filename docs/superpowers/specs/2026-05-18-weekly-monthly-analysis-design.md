# Weekly & Monthly Trade Analysis + h4 Trend Filter — Design Spec

**Date:** 2026-05-18  
**Status:** Draft

---

## 1. Overview

เพิ่มระบบวิเคราะห์ประวัติการเทรดอัตโนมัติ 2 ระดับ:

- **Weekly Analysis**: สรุป performance metrics ทุกวันอาทิตย์เที่ยงคืน UTC → DIRECTOR อ่านเพื่อปรับ macro bias สัปดาห์ถัดไป
- **Monthly Analysis**: วิเคราะห์ candle pattern จาก MT5 history ทุกต้นเดือน → DIRECTOR อ่านเพื่อเห็น pattern ระยะยาว

ควบคู่กับ: เพิ่ม **Gate I (h4_trend filter)** — บล็อก XAU BUY เมื่อ h4_trend = DOWNTREND

---

## 2. Weekly Analysis

### Trigger
- ทุกวันอาทิตย์ 00:00 UTC (ISO weekday = 6, hour = 0)
- ตรวจสอบใน scan loop หลัก — ถ้าผ่านเงื่อนไขและยังไม่ได้รันสัปดาห์นี้ → รัน

### ข้อมูลที่วิเคราะห์ (จาก `trade_history` + `market_context`)

| Metric | คำอธิบาย |
|---|---|
| Win rate รายสัญลักษณ์ | BTC/XAU แยกกัน, TP/total |
| Win rate รายชั่วโมง UTC | hour 0-23, ระบุชั่วโมงที่ดี/แย่ |
| Score threshold effectiveness | score 5/6/7/8/10/12 → win rate แต่ละระดับ |
| DIRECTOR direction accuracy | ช่วง BUY_ONLY/SELL_ONLY/BOTH → win rate จริง |
| Avg win / Avg loss / Max drawdown | สุขภาพรายสัปดาห์ |

### Output format
บันทึกเป็น Markdown ที่ `analysis/weekly/YYYY-WNN.md`

```markdown
# Weekly Analysis — 2026-W20 (May 11–17)

## Performance Summary
- BTC: 32/48 trades (67%) | Total PnL: +$124
- XAU: 8/14 trades (57%) | Total PnL: +$38

## Hourly Win Rate (UTC)
| Hour | Trades | Win% |
|------|--------|------|
| 00 | 5 | 20% |
| 02 | 18 | 72% |
...

## Score Effectiveness
| Score | Trades | Win% |
|-------|--------|------|
| 6 | 22 | 55% |
| 8 | 8 | 37% |
...

## DIRECTOR Accuracy
| Direction | Trades | Win% |
|-----------|--------|------|
| BUY_ONLY | 15 | 73% |
| SELL_ONLY | 8 | 37% |
| BOTH | 39 | 61% |

## Max Drawdown: $14.2 | Avg Win: +$8.4 | Avg Loss: -$4.1
```

### DIRECTOR integration
แนบ weekly summary ล่าสุด (+ สัปดาห์ก่อนหน้า 1 สัปดาห์) ใน DIRECTOR prompt ทุกรอบที่รัน

---

## 3. Monthly Analysis

### Trigger
- ทุกวันอาทิตย์แรกของเดือน 00:00 UTC (ISO week = 1 ของเดือน)
- รันหลัง weekly analysis เสร็จ

### ข้อมูลที่วิเคราะห์
สำหรับแต่ละ trade ใน window ที่กำหนด:
1. ดึง entry_time จาก trade_history
2. Fetch MT5 candles ย้อนหลัง 20 แท่ง (M15) จาก entry_time
3. แปลงเป็น text description ส่งให้ AI summarize

```
Trade: XAU BUY, result: WIN (+$42)
Candles before entry (M15, newest first):
  -1: O=2345 H=2348 L=2344 C=2346 (bullish)
  -2: O=2342 H=2346 L=2339 C=2345 (bullish, strong)
  -3: O=2340 H=2344 L=2338 C=2342 (bullish)
  -4: O=2338 H=2341 L=2336 C=2340 (doji)
RSI: 45.4, Score: 6, h4: UPTREND
```

AI สรุป pattern ที่พบบ่อยในกลุ่ม WIN vs กลุ่ม LOSS

### Window ตาม symbol
| Symbol | Window |
|---|---|
| BTCUSDm | 1 เดือน (~200 trades) |
| XAUUSDm | **3 เดือน rolling** (sample น้อย ~60 trades) |

> **TODO**: ทบทวน XAU window เมื่อ live trade สะสมครบ 3 เดือน (ประมาณ Aug 2026)  
> ปัจจุบัน XAU มีเพียง ~20 trades/เดือน เนื่องจาก Gate G (BUY only) + Gate F (UTC 00/09 blocked)

### Output format
บันทึกที่ `analysis/monthly/YYYY-MM.md`

---

## 4. Backtest Seed Data

ใช้ `s3a.csv` (662 trades, Feb 11 – May 4, 2026) เพื่อ bootstrap analysis ก่อนที่ live data จะสะสมพอ

### การ label
```markdown
## [BACKTEST] Reference Data — Feb–May 2026 (662 trades)
> ⚠️ Simulated data — ใช้เป็น reference เท่านั้น ไม่แทน live trade
```

แยก section ชัดเจนจาก live data เสมอ — AI ต้องไม่ผสม context ทั้งสอง

### Fields ที่ใช้ได้จาก s3a.csv
`symbol, direction, entry_time, entry_price, result, net_profit, rsi_entry, score, allowed_direction`

---

## 5. XAU Strategy Improvements (validated จาก live data May 2-13)

Live data: XAU รวม -$13.65 ใน 20 trades — strategy 1+2 รวมกันทำให้ +$6.29

### Strategy 1 — ขยาย Gate F (XAU dead hours)

| Bad hours เพิ่ม | Win% | PnL | หมายเหตุ |
|---|---|---|---|
| UTC 01 | 21% | -443 | volume สูง แต่ WR ต่ำ |
| UTC 11 | 20% | -177 | London midday choppy |
| UTC 13 | 0% | -32 | ตัวอย่างน้อย แต่ 0% |
| UTC 17 | 0% | -33 | NY close volatile |
| UTC 18 | 0% | -201 | after NY close |
| UTC 19-20 | 0% | -33 | off-hours |

**Exception ใหม่**: UTC 00 ปกติบล็อก แต่ถ้า macro_bias = STRONG_BULLISH → อนุญาต
(live data: UTC 00 + STRONG_BULLISH ชนะ 3/3 trades, +$2.35)

### Strategy 2 — แก้ Gate G (XAU SELL)

| Condition | Win% | PnL | Action |
|---|---|---|---|
| SELL + SELL_ONLY macro | 43% | +261 | อนุญาต |
| SELL + BOTH/neutral | 18% | -708 | บล็อก |

แก้จาก "block ALL SELL" เป็น "block SELL unless allowed_direction = SELL_ONLY"

Live validation: 4 SELL trades (macro=None) → 3 SL, saving +$11.68

---

## 6. Gate I — h4_trend Filter (XAU BUY)

### ที่มา
จากการวิเคราะห์ s3 backtest: XAU BUY เมื่อ h4_trend = DOWNTREND มี **win rate 0%** ทุก trade (4/4 SL)  
เมื่อ h4_trend = UPTREND หรือ N/A → win rate 40-45%, PnL บวก

### Rule
```
Gate I: ถ้า symbol = XAUUSDm AND direction = BUY AND h4_trend = DOWNTREND → ❌ BLOCKED
```

### Implementation
เพิ่มใน GUARDIAN gate sequence ต่อจาก Gate H (score_blacklist)

```python
# Gate I — XAU h4 trend filter
if symbol == "XAUUSDm" and direction == "BUY":
    h4 = STRATEGY_DATA[symbol].get("h4_trend", "N/A")
    if h4 == "DOWNTREND":
        log.warning(f"🚫 [Gate I] XAU BUY blocked — h4=DOWNTREND")
        return False
```

### Note
`h4_trend` ต้องถูก set โดย DIRECTOR ใน `STRATEGY_DATA[symbol]["h4_trend"]` ในทุกรอบ macro analysis  
ถ้า h4_trend = "N/A" (ไม่มีข้อมูล) → ผ่าน gate (conservative default)

---

## 6. Architecture

```
Sunday 00:00 UTC
    ↓
[WeeklyAnalyzer]
    - query trade_history + market_context (last 7 days)
    - compute metrics
    - write analysis/weekly/YYYY-WNN.md
    - if first Sunday of month → trigger MonthlyAnalyzer
        ↓
    [MonthlyAnalyzer]
        - fetch MT5 candles for each trade entry
        - format as text
        - call GPT to summarize WIN/LOSS patterns
        - write analysis/monthly/YYYY-MM.md

DIRECTOR (every 4h)
    - read latest weekly MD
    - read latest monthly MD (if exists)
    - include in prompt as context
    - set allowed_direction + h4_trend per symbol
```

---

## 7. Files to Create/Modify

| File | Action |
|---|---|
| `analysis/weekly/` | สร้าง directory ใหม่ |
| `analysis/monthly/` | สร้าง directory ใหม่ |
| `weekly_analyzer.py` | สร้างใหม่ — query DB + compute metrics + write MD |
| `monthly_analyzer.py` | สร้างใหม่ — fetch MT5 candles + GPT pattern summary |
| `ai_engine.py` | แก้ DIRECTOR prompt ให้อ่าน weekly/monthly MD |
| `trade_manager.py` | เพิ่ม Gate I (h4_trend filter) |
| `gui_main.py` | เพิ่ม trigger weekly/monthly analyzer ใน scan loop |

---

## 8. Open Questions / TODO

- [ ] **XAU monthly window**: ทบทวนเป็น 1 เดือนเมื่อ live data สะสมถึง Aug 2026
- [x] **h4_trend source**: คำนวณจาก MT5 directly (programmatic) — ดึง H4 candles 10 แท่ง คำนวณ MA5 ถ้า close[-1] > MA5 → UPTREND, else DOWNTREND ไม่ใช้ AI (ประหยัด token, deterministic)
- [x] **backtest seed**: แยกเป็น reference file (`s3a.csv`) — DIRECTOR อ่านโดยตรง ไม่ import เข้า DB
- [x] **Monthly candle fetch fallback**: ถ้า MT5 offline → retry ทุก 15 นาทีจนกว่าจะได้ข้อมูล (ไม่ข้ามรอบ)
- [x] **DIRECTOR prompt size**: สรุปย่อก่อนแนบ (top 3 insights ต่อไฟล์ ~300 tokens) — ประหยัด token 10× เทียบกับแนบเต็ม
