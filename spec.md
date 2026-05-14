Architecture :

  Stack
  - Backend     : Python 3.x, asyncio
  - GUI         : customtkinter (dark mode desktop app)
  - Web API     : Flask (port 8080)
  - Broker      : MetaTrader5 (MT5) — market orders, SL/TP, trailing
  - AI          : OpenAI GPT-4o-mini (async streaming, 5-retry)
  - News        : cloudscraper (financial calendar)
  - Notifications: discord.py bot
  - Database    : SQLite3 (trading_history.db)

  Entry Points
  - gui_main.py         → Desktop GUI (PCTradingApp class)
  - web_app.py          → Flask REST server
  - discord_manager.py  → Discord bot (background thread)
  - trade_manager.py    → Core trading loop

  Core Loop — trading_job() runs every 1 min (news window) or 5 min (normal)
    1. DIRECTOR  (ai_engine.ai_macro_analysis)
         Refreshes every 4h or on ATR spike >1.2%
         Reads H4/D1 technicals + news → sets allowed_direction, global_risk
    2. ANALYST   (ai_engine.ai_analysis)
         Per-symbol, every ~4.5 min, RSI-triggered
         Scores 0–12 (Supertrend + SMC zones + RSI + SCOUT bonus)
         Decision: BUY / SELL / HOLD at threshold (4–8 by risk level)
    3. GUARDIAN  (risk_manager.RiskManager)
         4 sequential gates before any order:
           a. cooldown active? (5 min after SL)
           b. against DIRECTOR trend?
           c. spread too high? (dynamic ATR-based)
           d. max 3 layers per symbol hit?
    4. SENTINEL  watchdog — MT5 reconnect (3 retries, 5-sec backoff)

  Background Processes
  - Position tracker  : per-tick max profit/loss tracking, trailing SL/BE
  - scheduled_reports : hourly Discord P&L (discord_manager)
  - News scanner      : hourly calendar fetch → sets TODAY_NEWS_TIMES
  - Gear shifter      : auto 1-min mode within 30-min window of events
  - Daily reset       : clears news state at midnight

  State Machine
    STOPPED → WAITING → RUNNING ⇄ COOLDOWN(30m)
    DAY_OFF forces STOPPED during market closure

  Symbols traded: BTCUSDm, XAUUSDm

---

data contract :

  shared_state.py — global variables shared across all modules

    BOT_STATE             str         STOPPED | WAITING | RUNNING | COOLDOWN | DAY-OFF
    SCAN_COUNT            int         increments each trading_job() tick
    AI_RETRY_COUNT        int         retries before entering COOLDOWN
    CURRENT_LOOP_MINS     int         1 (news window) | 5 (normal) | 2 (fast boot)
    COOLDOWN_REMAINING    int         minutes until COOLDOWN ends
    CURRENT_RISK_LEVEL    int         1–5, set by GUI risk slider
    INITIAL_EQUITY        float       equity at session start
    MAX_DRAWDOWN_PERCENT  float       daily drawdown limit (default 10%)
    IS_DAY_OFF            bool        blocks trading during off-days
    TODAY_NEWS_TIMES      list[time]  high-impact event times for today
    NEWS_WINDOWS          list[dict]  {"start": time, "end": time, "currency": str}
    LAST_NEWS_DATE        date        prevents duplicate news fetches

    TRADE_LAYERS          dict        per-symbol 5-layer RSI flags
      {symbol: {"buy": [bool×5], "sell": [bool×5]}}

    ACTIVE_TRADE_TRACKER  dict        open position peak tracking
      {ticket: {"max_p": float, "max_l": float}}

    MACRO_DATA            dict        DIRECTOR output per symbol
      {symbol: {
        "bias"              : str     STRONG_BULLISH|STRONG_BEARISH|BULLISH|BEARISH|SIDEWAY|WAIT_FOR_NEWS
        "allowed_direction" : str     BUY_ONLY | SELL_ONLY | BOTH | NONE
        "global_risk"       : int     recommended risk level (3–5)
        "reason"            : str     short explanation <80 chars
        "atr_pct_h4"        : float   H4 ATR as % of price
        "h4_trend"          : str     UPTREND 🟢 | DOWNTREND 🔴 | SIDEWAY
        "d1_trend"          : str     UPTREND 🟢 | DOWNTREND 🔴 | SIDEWAY
        "set_time"          : datetime
        "none_since"        : datetime  (optional) when allowed_direction became NONE
      }}

    REGIME_STABILITY      dict        anti-flip-flop regime guard
      {symbol: {"current": str, "count": int, "pending": str, "pending_count": int}}
    REGIME_MIN_CONFIRMATIONS  int     3 consecutive confirms before regime switch

    MT5_DISCONNECT_COUNT  int
    AI_DISCONNECT_COUNT   int
    DISCORD_DISCONNECT_COUNT int
    LAST_REPORT_HOUR      int

    trade_layers_lock     asyncio.Lock
    tracker_lock          asyncio.Lock

  ---

  ai_engine.STRATEGY_DATA — per-symbol trading parameters

    {symbol: {
      "buy"              : list[float×5]  RSI thresholds descending e.g. [35,32,30,28,26]
      "sell"             : list[float×5]  RSI thresholds ascending  e.g. [65,68,70,72,74]
      "regime"           : str            TRENDING_UP|TRENDING_DOWN|RANGING|PULLBACK|VOLATILE
      "threshold"        : float          volatility spike % per M5 candle
      "atr_pct"          : float          current ATR as % of price
      "bb_pct"           : float          Bollinger Band position 0–100% (50=mid)
      "tp_activation"    : float          USD to unlock trailing SL (default 3.0)
      "pullback_pct"     : float          % pullback before close (default 0.30)
      "be_activation"    : float          USD to lock break-even (default 1.50)
      "be_lock_profit"   : float          profit locked on BE trigger (default 0.20)
      "max_spread"       : int            max spread in points
    }}

  ---

  AI response shapes

    DIRECTOR (ai_macro_analysis) → stored in MACRO_DATA[symbol]
      bias, allowed_direction, global_risk_level, reason,
      atr_pct_h4, h4_trend, d1_trend

    ANALYST (ai_analysis) → used for entry decision
      {
        "score"         : int    0–12
        "decision"      : str    BUY | SELL | HOLD
        "reason"        : str
        "analyst_score" : int
        "scout_score"   : int    0–2
      }

  ---

  advanced_indicators.py return shapes

    get_3_indicators() →
      {
        "supertrend"    : str    UPTREND 🟢 | DOWNTREND 🔴
        "supertrend_h1" : str
        "ob_zone"       : str    "Demand Zone 3000.12-3001.45 (age 5b)" | "No Zone"
        "ob"            : {"type": str, "high": float, "low": float, "open": float,
                           "close": float, "age": int}
        "long_stop"     : float  Chandelier exit for long
        "short_stop"    : float  Chandelier exit for short
      }

    get_macro_trends() →
      {"h4_trend": str, "d1_trend": str, "atr_pct_h4": float}

    get_scout_score(direction) →
      {"score": int, "macd_signal": str, "ema_aligned": bool, "reason": str}

  ---

  SQLite — trading_history.db

    trade_history
      ticket               INTEGER PK
      symbol               TEXT
      order_type           TEXT       BUY | SELL
      lot_size             REAL
      entry_time           TEXT
      entry_price          REAL
      entry_reason         TEXT       e.g. "L1:Score=7"
      slippage             REAL
      exit_time            TEXT
      exit_price           REAL
      net_profit           REAL
      max_floating_profit  REAL
      max_floating_loss    REAL
      exit_reason          TEXT       "Hit Take Profit 🎯" | "Hit Stop Loss 🛡️"
      balance_after_trade  REAL

    market_context
      ticket               INTEGER PK
      rsi_entry            REAL
      market_regime        TEXT
      volatility_threshold REAL
      risk_level           INTEGER
      macro_bias           TEXT
      allowed_dir          TEXT
      analyst_score        REAL
      scout_score          INTEGER
      atr_pct_entry        REAL
      spread_entry         REAL
      bb_pct_entry         REAL
      h4_trend             TEXT
      d1_trend             TEXT

    news_history
      news_id              INTEGER PK AUTOINCREMENT
      date                 TEXT
      time                 TEXT
      currency             TEXT
      impact               TEXT       HIGH | MEDIUM | LOW
      title                TEXT

    daily_performance
      date                 TEXT PK
      start_balance        REAL
      end_balance          REAL
      total_profit         REAL
      max_drawdown_pct     REAL
      total_trades         INTEGER
      win_trades           INTEGER

  ---

  bot_config.py — static configuration

    ACCOUNT_ID              int
    PWD                     str
    SRV                     str       MT5 server e.g. "Exness-MT5-Demo"
    MAGIC_NUMBER            int       999999
    LOT                     float     0.01 base lot
    TIMEFRAME               int       mt5.TIMEFRAME_M5

    SYMBOLS_CONFIG[symbol]
      lot                   float
      sl_pts                int       stop loss points  (BTC 30000, XAU 500)
      tp_pts                int       take profit points (BTC 50000, XAU 1000)
      threshold             float     volatility spike %
      rsi_buy_buffer        float     widen buy RSI threshold
      rsi_sell_buffer       float     widen sell RSI threshold
      min_atr_pct           float     minimum ATR% to trade
      analyst_score_offset  int       score adjustment vs base threshold
      max_spread_override   int       max spread in points
      pullback_rsi_threshold float    RSI for pullback entries

    TOKEN                   str       Discord bot token
    WEBHOOK_URL             str       Discord trade-alert webhook
    REPORT_CHANNEL_ID       int
    AI_KEY                  str       OpenAI API key
    LOG_PATH                str       "Logs"

  ---

  Flask API — web_app.py

    GET /api/status →
      {bot_state, scan_count, current_loop_mins, risk_level,
       balance, equity, open_positions, floating_pl,
       logs: [{time, level, msg}]}

    GET /api/trades →
      {trades: [{ticket, symbol, order_type, entry_price, exit_price,
                 net_profit, exit_time, market_regime, rsi_entry, macro_bias}],
       count}

    GET /api/performance →
      {daily: [{day, pnl, trades, wins}]}

  ---

  bot_status.json — persisted state for web dashboard

    {bot_state, scan_count, current_loop_mins, cooldown_remaining,
     risk_level, balance, equity, open_positions, floating_pl,
     last_update, connection_status: {mt5, discord, ai},
     macro_data: {symbol: {...MACRO_DATA fields}}}

---

done :

  - Backtest engine (DataLoader → MockDirector → MockAnalyst → MockGuardian
      → PositionSimulator → ReportPrinter → run_backtest)
  - Dynamic Chandelier SL/TP replacing fixed SL/TP
  - Quality filters for XAU/BTC off-hours (S1–S5 scenarios)
  - Daily loss limit + equity circuit breaker
  - S3-Balanced (s3a) design: 4 filters cut MaxDD 26.5% → 14.5%
  - S3A backtest verified: 708 trades, WR 36.6%, PnL +$639, MaxDD 14.5%, Sharpe 3.86
  - S3A Guardian gates E–H ported to production (risk_manager.py + trade_manager.py)

---

todo :

  - Monitor live performance of Gates E–H on production bot
  - Consider re-running full scenario suite (baseline + s1–s5 + s3a) on fresh data
  - Investigate score=8 anomaly root cause in ai_engine scoring formula

---

current state :

  S3A is live in production. Guardian now has 8 gates (A–H).
  Gates E–H block: BTC UTC 00-01, XAU UTC 00/09, XAU SELL, score=8.
  Backtest result: PnL +$639 / MaxDD 14.5% / Sharpe 3.86 over 3 months.
