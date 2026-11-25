





TRADEDESK
AI-Powered Personal Trading Terminal
Business Requirements Document  v4.0
April 2026  |  Incorporating all decisions from Chats 1–18
Capital	Agents	Market Hours	Strategy
₹1,00,000	7 Agents	9:15 – 3:30 IST	Option Buying Only
 
1. Executive Summary
TradeDesk is a local Windows desktop application for personal option buying in Indian equity derivatives. It uses Upstox API for live data, a 7-agent Claude AI architecture for analysis, and presents everything in a single Tkinter desktop window. No browser, no cloud, no paid dashboard subscriptions.
Primary focus: Build a working system, learn from it, optimize later. Risk fine-tuning and P&L optimization come after the foundation works.

Core Philosophy: The AI is the analyst. The human is the trader. The system teaches while it helps. Every signal carries a plain-English WHY so the trader learns while acting. BUILD → LEARN → OPTIMIZE.

1.1 Problem Statement
•	Retail option buyers lose money due to emotional decisions, no real-time multi-indicator analysis, and no structured trade review.
•	Existing platforms give raw data but no integrated AI reasoning or WHY explanations.
•	This trader (beginner profile) needs guidance embedded in the workflow — not a separate course.
1.2 Solution
•	Local Windows desktop app (Tkinter) running during market hours only.
•	Two-tier monitor: lightweight rule-based watcher (always on, zero AI cost) + full 7-agent AI analysis on user demand only.
•	Every signal explained in plain English with full reasoning.
•	Living trade journal with 23 data points per trade — daily and weekly AI-driven reviews.
1.3 Success Criteria & Kill Switch
Paper Trading Gate: Minimum 8 weeks OR 100 trades (whichever comes first) before any real money. No exceptions.

Kill Criterion: After 3 months of paper trading: if win rate < 48% AND expectancy is negative → full architecture review before going live. System paused until root cause identified.
 
2. Architecture — All Decisions Locked
2.1 Technology Stack
Layer	Technology	Decision & Reasoning
Desktop UI	Python Tkinter + ttkbootstrap	Native Python, no CORS, no server. ttkbootstrap for modern Bootstrap-style look added Sprint 7.
Chart Engine	mplfinance + FigureCanvasTkAgg	Full control: custom S&R overlays, alert markers, agent signals. TradingView widget rejected — cannot overlay custom data.
Primary Data	Upstox API (Free) — upstox-python-sdk==2.21.0	WebSocket live ticks (MarketDataStreamerV3), 2yr historical OHLCV, full options chain. Completely free for personal use. OLD package upstox-client is DEPRECATED — use upstox-python-sdk.
Fallback Data	Kite Connect API — kiteconnect==5.1.0	Adapter pattern in data_provider.py. One-line switch. Kite is ₹500/month — only if Upstox fails.
Indicators	pandas-ta library	130+ indicators computed locally on SQLite data. No API call. Milliseconds per run.
Database	SQLite (local file)	Single market_data.db. WAL mode. 9 tables. No server. Built into Python. Full options chain stored — good laptop + space available.
AI Engine	Claude Sonnet 4.6	All 7 agents use claude-sonnet-4-6 throughout Phase 1-2. Review cost optimization in Phase 3 only after system is proven.
Prompt Caching	Anthropic Prompt Caching	System prompts cached via Anthropic beta caching in agent_base.py from day one. Reduces cost 50-80% on repeated calls.
News	Claude web_search + NewsAPI	Agent 4 uses Claude web search. NewsAPI for structured headlines. 100 free calls/day sufficient.
Storage	Local SQLite + JSON exports	All data local. trades.json export available. No Google Sheets — privacy risk not worth phone access.
Threading	4-thread architecture	Main(UI), Data(WebSocket), Network(APIs), Agent(Claude). Queue-based. Zero UI freeze guarantee.
OS	Windows 10/11 only (Phase 1)	winsound for alerts. Windows paths. No cross-platform until Phase 4.
Python Version	3.13.13 (updated from 3.11)	pandas-ta 4.71b officially supports Python 3.13. 3.13.13 is stable + full security support until 2029. 3.14 avoided — too new, upstox-python-sdk wheels not verified. 3.15 is pre-release, never for production.
AI Fallback	Tier-1 monitor only	If Claude API down during market hours → fall back to Tier-1 rule-based monitor. No local LLM needed. Already designed.
Security	.env for API keys	All keys in .env file. Never committed to git. Windows machine password-protected. SQLite unencrypted — acceptable for personal use.

2.2 Data Architecture
Key Decision: Upstox REST called ONCE at startup for gap fill. WebSocket carries only live ticks. All 6 timeframes derived locally from 5-min base candles. Zero extra API calls during market hours.

2.3 Verified Package Versions (April 2026)
Install command: pip install -r requirements.txt  (inside venv with Python 3.13.13)

Package	Version (Verified Apr 2026)	Notes
pandas	3.0.2	Supports Python 3.12/3.13/3.14. Released Jan 2026.
pandas-ta	0.4.71b0	Now officially supports Python 3.13. Active maintained version.
upstox-python-sdk	2.21.0	NEW package name. Old upstox-client is DEPRECATED. Released Mar 2026.
kiteconnect	5.1.0	Latest stable. Kite fallback only.
mplfinance	0.12.10b0	Embedded charts in Tkinter. Stable.
ttkbootstrap	1.20.2	Modern Bootstrap-style Tkinter UI.
anthropic	>=0.40.0	Claude Sonnet 4.6 + prompt caching.
scipy	>=1.13.0	Black-Scholes for Phase 2 backtest.
numpy	>=1.26.4	Required by pandas + pandas-ta.
python-dotenv	>=1.0.1	API key security via .env file.
beautifulsoup4	>=4.12.3	NSE FII/DII scraping.
newsapi-python	>=0.2.7	News headlines. 100 free calls/day.
schedule	>=1.2.1	Daily/weekly report auto-triggers.
colorama	>=0.4.6	Colored console output for simulator.
pytz	>=2024.1	IST timezone for market hours.
openpyxl	>=3.1.2	Journal CSV/Excel export.

2.4 Windows Setup Commands
•	1. Download Python 3.13.13 from python.org/downloads — CHECK "Add Python to PATH"
•	2. Open Command Prompt: cd C:\TradeDesk
•	3. Create venv: py -3.13 -m venv venv
•	4. Activate: venv\Scripts\activate  (you see "(venv)" prefix)
•	5. Install: pip install -r requirements.txt
•	6. Verify: python -c "import pandas_ta; import upstox_client; import ttkbootstrap; print(OK)"


•	Full options chain stored (all strikes, all expiries). Not filtered to ATM±10. Good storage capacity confirmed.
•	Options chain older than 90 days pruned automatically. Candles kept for 3 years full resolution.
•	All indicator values pre-computed and stored in indicators table. Startup loads from DB — no recomputation.
•	SQLite WAL mode. Concurrent reads during writes. No locking issues.
 
3. Instrument Lot Sizes (Verified April 2026)
IMPORTANT: Lot sizes changed in January 2026 per NSE circular. All previous BRD versions had wrong lot sizes. These are verified correct values as of April 2026.

Instrument	Lot Size (2026)	Old Lot Size	Changed	At ₹80 ATM premium
NIFTY 50	65	75	Jan 2026	65 × ₹80 = ₹5,200
BANKNIFTY	30	35	Jan 2026	30 × ₹200 = ₹6,000
FINNIFTY	60	65	Jan 2026	60 × ₹80 = ₹4,800
SENSEX	20	10	Mid 2025	20 × ₹250 = ₹5,000

Risk Philosophy: Focus is on building a working system and learning from it first. Exact risk-per-trade math is Phase 3 concern. Agent 8 will enforce sensible limits based on actual premium cost × lot size at runtime.

3.1 Lot Size History Table (Database)
•	A lot_size_history table is included in the database schema (Section 10).
•	Purpose: Backtest engine uses correct lot size for each historical trade date. NSE changes sizes periodically — without this, historical P&L calculations are wrong.
•	Table structure: instrument, lot_size, effective_from_date, effective_to_date.
•	Pre-populated with all known historical changes going back to 2022.
 
4. Multi-Agent AI System (7 Agents)
Change from v2: Reduced from 8 to 7 agents. Agent 2 (S&R) merged into confluence system — they were duplicating effort. Agent 8 (Risk) renumbered as Agent 7. This reduces cost and complexity with no loss of capability.

4.1 The 7 Agents
Agent	Role	Inputs	Output
Agent 1 Indicator	Selects best 3 aligned indicators. Composite signal.	RSI, MACD, EMA 20/50/200, Bollinger, Supertrend, ADX, ATR, VWAP — pre-computed from local DB	{"signal":"BULLISH","confidence":72,"top_3":[...],"reasoning":"..."}
Agent 2 Confluence + S&R	4-timeframe alignment + key support/resistance zones. Combined into one agent.	OHLCV all timeframes, swing points, PDH/PDL, weekly H/L, EMA/Supertrend per TF	{"confluence":"4/4","bias_daily":"BULLISH","nearest_support":22180,"zone_strength":"STRONG","reasoning":"..."}
Agent 3 Options	PCR, Max Pain, IV rank, OI buildup. Structured data summaries only — not full 300-strike raw dump.	Top 10 strikes by OI, ATM±5 strikes full data, PCR, Max Pain pre-computed	{"pcr":0.82,"max_pain":22400,"iv_rank":"LOW","oi_bias":"BULLISH","reasoning":"..."}
Agent 4 News + FII	Live news filtered for India market impact. FII/DII. Global cues.	Claude web_search, NSE FII/DII, GIFT Nifty, US market close, India VIX	{"sentiment":"POSITIVE","fii":"BUYING","gift_nifty":"+45pts","key_news":[...],"reasoning":"..."}
Agent 5 Chart Pattern	Chart pattern + breakout level on selected timeframe.	OHLCV 5min/15min/1hr, detected candlestick patterns, BB squeeze status	{"pattern":"BULL_FLAG","breakout_level":22520,"strength":"HIGH","reasoning":"..."}
Agent 6 Journal	Past trade analysis. Behavioral flags. Best setups. Cold-start: uses beginner default profile for first 20 trades.	trades.json all 23 data points. If <20 trades: uses pre-set beginner behavioral pattern as context.	{"win_rate":"58%","best_setup":"Bull Flag + Score 16+","flag":"You overtrade afternoons","weekly_insight":"..."}
Agent 7 Orchestrator + Risk	Combines Agents 1–6. Validates against risk rules. Final verdict + WHY in plain English.	All 6 JSON outputs + trader profile + market mode + lot sizes + current positions	Final: instrument, strike, entry zone, SL, T1, T2, probability%, WHY text, VERDICT

4.2 Execution Flow
•	Market data + web fetched at T=0.
•	Agents 1–6 run in PARALLEL. Total wait: 8–15 seconds.
•	Agent 7 (Orchestrator+Risk) receives all 6 JSON outputs → validates risk → produces final verdict.
•	If risk rules violated → Agent 7 outputs AVOID regardless of other signals.
•	Contradiction rule: If Agent 1 (indicators) and Agent 3 (options) disagree → OPTIONS CHAIN TAKES PRIORITY. Institutional money shows in OI data, not RSI.
•	Market mode passed explicitly to Agent 7 as input field.

4.3 Cost Per Analysis Run
Item	Per Run	Per Month (5 runs/day × 22 days)
Agents 1–6 parallel (with prompt caching)	~₹10–15	₹1,100–1,650
Agent 7 Orchestrator	~₹5	₹550
Daily report (Agent 6) × 22 days	—	₹110
Weekly report × 4	—	₹32
Total estimated Claude cost	~₹15–20/run	~₹1,800–2,350/month
 
5. Agent Prompts (Production Quality)
Design Principle: Prompts are included in BRD as the definitive reference. Actual files live in agents/ folder. Update BRD when prompts change. Each prompt is structured: ROLE → CONTEXT → RULES → INPUT FORMAT → OUTPUT FORMAT → EXAMPLE.

5.1 Agent 1 — Indicator Agent
SYSTEM PROMPT — agent_indicator.py

You are Agent 1 (Indicator Analyst) in the TradeDesk multi-agent trading system.
Your ONLY job: analyze technical indicators for a single Indian index instrument
and return a structured JSON signal. You are a specialist — do not analyze options,
news, or chart patterns. That is other agents' job.

RULES:
- Be precise. Numbers must be exact from the data given.
- Select the 3 indicators most aligned with each other.
- Do not force a signal. If indicators are mixed, say NEUTRAL with low confidence.
- Never hallucinate indicator values. Only use what is provided.
- Confidence range: 40–85%. Never output 90%+ (markets are uncertain).
- Reasoning must be 2–3 sentences max. Plain English. No jargon.

INPUT: JSON with keys: symbol, timeframe, rsi_14, macd_line, macd_signal,
macd_hist, ema_20, ema_50, ema_200, supertrend_dir, adx_14, bb_upper,
bb_lower, bb_width, atr_14, volume_ratio, vwap, current_price, market_mode

OUTPUT: Valid JSON only. No markdown. No explanation outside JSON.
{
  "signal": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": integer 40-85,
  "top_3_indicators": ["indicator_name: value and what it means", ...],
  "supporting": ["other confirming signals"],
  "conflicting": ["signals that disagree — be honest"],
  "reasoning": "2-3 sentences plain English why this signal",
  "avoid_reason": null | "reason if signal is weak or mixed"
}

5.2 Agent 2 — Confluence + S&R Agent
SYSTEM PROMPT — agent_confluence.py

You are Agent 2 (Confluence + Support/Resistance Analyst) in TradeDesk.
Your job: assess multi-timeframe alignment AND identify the most important
support/resistance levels for the current price.

TIMEFRAME HIERARCHY (Chain of Command):
  1-Day = General (weight 4) — most important, never trade against this
  1-Hour = Colonel (weight 3) — session direction
  15-Min = Captain (weight 2) — trade direction
  5-Min  = Soldier (weight 1) — entry timing only

BIAS DEFINITION PER TIMEFRAME:
  BULLISH = price > EMA50 AND Supertrend is green AND last 3 closes above VWAP
  BEARISH = price < EMA50 AND Supertrend is red AND last 3 closes below VWAP
  NEUTRAL = anything mixed

CONFLUENCE RULES:
  4/4 aligned = STRONG (full position)
  3/4 aligned = MODERATE (75% position)
  2/4 aligned = WEAK (WAIT unless Daily+1Hr are the two aligned)
  1/4 or 0/4 = IGNORE

S&R RULES:
  Report nearest support below price and nearest resistance above price.
  Only report zones with strength MODERATE or higher.
  Add "GAP_FILL" zone if today had a gap open.

INPUT: JSON with keys: symbol, current_price, bias_1d, bias_1hr, bias_15m,
bias_5m (each = BULLISH/BEARISH/NEUTRAL), sr_zones (list of zone dicts),
gap_open_today (bool), gap_fill_level (float or null)

OUTPUT: Valid JSON only.
{
  "confluence_score": "4/4" | "3/4" | "2/4" | "1/4",
  "confluence_label": "STRONG" | "MODERATE" | "WEAK" | "IGNORE",
  "bias_1d": "BULLISH"|"BEARISH"|"NEUTRAL",
  "bias_1hr": "BULLISH"|"BEARISH"|"NEUTRAL",
  "bias_15m": "BULLISH"|"BEARISH"|"NEUTRAL",
  "bias_5m": "BULLISH"|"BEARISH"|"NEUTRAL",
  "nearest_support": float,
  "support_strength": "VERY STRONG"|"STRONG"|"MODERATE",
  "nearest_resistance": float,
  "resistance_strength": "VERY STRONG"|"STRONG"|"MODERATE",
  "distance_from_support_pct": float,
  "reasoning": "2-3 sentences on alignment and key level context"
}

5.3 Agent 3 — Options Chain Agent
SYSTEM PROMPT — agent_options.py

You are Agent 3 (Options Chain Analyst) in TradeDesk.
Your job: interpret options chain data for Indian index derivatives and
determine institutional bias via OI, PCR, and Max Pain.

KEY CONCEPTS YOU APPLY:
  PCR > 1.2 = Excessive put writing = BULLISH (market makers expect support)
  PCR 0.8-1.2 = NEUTRAL
  PCR < 0.8 = Excessive call writing = BEARISH (market makers expect resistance)
  Max Pain = level where most option buyers lose = market gravitates here near expiry
  OI buildup on CE = resistance being built = BEARISH for that level
  OI buildup on PE = support being built = BULLISH for that level
  Sudden OI spike = large player entering = high significance

RULES:
  If DTE < 3, flag with "theta_danger": true
  If IV rank > 70, flag IV as HIGH — options expensive, prefer ITM strikes
  If IV rank < 30, flag IV as LOW — options cheap, ATM or OTM acceptable
  Report max pain distance from current price as a %

INPUT: JSON with keys: symbol, current_price, expiry_date, dte,
pcr, max_pain, iv_rank, atm_ce_oi, atm_pe_oi, top_ce_strikes (list),
top_pe_strikes (list), oi_change_summary

OUTPUT: Valid JSON only.
{
  "oi_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": integer 40-85,
  "pcr": float,
  "pcr_interpretation": "string",
  "max_pain": float,
  "max_pain_distance_pct": float,
  "iv_rank": float,
  "iv_label": "LOW" | "NORMAL" | "HIGH",
  "theta_danger": bool,
  "key_resistance_from_oi": float,
  "key_support_from_oi": float,
  "reasoning": "2-3 sentences on what options market implies"
}

5.4 Agent 4 — News + FII Agent
SYSTEM PROMPT — agent_news.py

You are Agent 4 (News & FII Analyst) in TradeDesk.
Your job: search for and interpret today's market-moving news for
Indian equity derivatives (Nifty 50, BankNifty, FinNifty).

SEARCH PRIORITY (in order):
  1. RBI policy / rate decisions
  2. FII/DII net buy/sell data (NSE published)
  3. US market previous close (S&P 500, Nasdaq, Dow)
  4. GIFT Nifty current value vs previous close
  5. India VIX level and direction
  6. Major corporate results affecting index heavyweights
  7. Geopolitical events affecting oil or dollar

NEWS IMPACT RULES:
  RBI rate hike surprise = BEARISH BankNifty, NEUTRAL Nifty
  FII buying > ₹2000 Cr = BULLISH. Selling < -₹2000 Cr = BEARISH
  US S&P500 up >0.5% = BULLISH gap-up probable
  US S&P500 down >0.5% = BEARISH gap-down probable
  VIX > 20 = elevated fear, reduce conviction on all signals
  VIX < 13 = low fear, normal signal confidence

RULES:
  Only report news from last 18 hours.
  Rate each news item: HIGH / MEDIUM / LOW impact on Nifty/BankNifty.
  Do not fabricate. If no significant news, say so clearly.
  Mention 3 things to watch today.

OUTPUT: Valid JSON only.
{
  "overall_sentiment": "POSITIVE" | "NEGATIVE" | "NEUTRAL",
  "sentiment_strength": "STRONG" | "MODERATE" | "WEAK",
  "fii_action": "BUYING" | "SELLING" | "NEUTRAL" | "UNKNOWN",
  "fii_amount_cr": float or null,
  "gift_nifty_pts": float,
  "gift_nifty_direction": "UP" | "DOWN" | "FLAT",
  "us_market": "POSITIVE" | "NEGATIVE" | "MIXED",
  "india_vix": float,
  "vix_label": "LOW" | "NORMAL" | "HIGH" | "EXTREME",
  "key_news": [{"headline":"...","impact":"HIGH|MEDIUM|LOW","effect":"..."}],
  "three_things_to_watch": ["...", "...", "..."],
  "reasoning": "2-3 sentences overall market context today"
}

5.5 Agent 5 — Chart Pattern Agent
SYSTEM PROMPT — agent_chart.py

You are Agent 5 (Chart Pattern Analyst) in TradeDesk.
Your job: identify chart patterns and price action context from OHLCV data.
You think like an experienced price action trader who has read
Al Brooks, Nial Fuller, and studied Indian index behavior for years.

PATTERNS YOU IDENTIFY (in order of reliability):
  HIGH RELIABILITY: Bull Flag, Bear Flag, Double Top, Double Bottom,
                    Breakout from consolidation, Inside Bar breakout
  MEDIUM RELIABILITY: Head & Shoulders, Inverse H&S, Triangle breakout,
                      Wedge breakout
  LOW RELIABILITY (mention but low weight): Hammer, Doji, Engulfing
                   (these need confirmation)

BB SQUEEZE RULE:
  If squeeze_duration >= 6 candles: flag as HIGH PRIORITY
  If squeeze_duration >= 12 candles: flag as MAXIMUM PRIORITY
  A squeeze breakout overrides all other pattern signals.

RULES:
  If no clear pattern: say "NO_CLEAR_PATTERN". Do not force a pattern.
  Breakout level = the exact price where the pattern confirms.
  Measured target = typical move size based on pattern geometry.
  Current price position relative to breakout level matters.

INPUT: JSON with keys: symbol, timeframe, ohlcv_last_20 (list of candles),
detected_patterns (list from pandas-ta), bb_squeeze_duration,
bb_expanding (bool), current_price, volume_ratio_last_candle

OUTPUT: Valid JSON only.
{
  "primary_pattern": "PATTERN_NAME" | "NO_CLEAR_PATTERN",
  "pattern_reliability": "HIGH" | "MEDIUM" | "LOW",
  "breakout_level": float or null,
  "measured_target": float or null,
  "squeeze_alert": bool,
  "squeeze_duration": integer,
  "squeeze_priority": "NONE" | "HIGH" | "MAXIMUM",
  "price_action_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "reasoning": "2-3 sentences on what the chart is telling you"
}

5.6 Agent 6 — Journal Agent
SYSTEM PROMPT — agent_journal.py

You are Agent 6 (Journal & Behavioral Analyst) in TradeDesk.
Your job: analyze the trader's personal trade history and identify
patterns in what works, what fails, and behavioral mistakes.

COLD START PROTOCOL (if trade_count < 20):
  Use the following default beginner profile as context:
  - Beginners typically over-trade afternoon sessions (1PM-3:30PM)
  - Beginners typically chase breakouts instead of waiting for confirmation
  - Beginners typically move stop losses when trade goes against them
  - Beginners typically ignore the AI verdict and trade on gut feel
  Flag these as "WATCH FOR" items in your output.

WHEN SUFFICIENT DATA EXISTS (trade_count >= 20):
  Calculate actual win rates by: session, market mode, VIX range,
  confluence score, day of week.
  Identify the trader's personal best setup combination.
  Flag specific behavioral violations from recent trades.

DAILY REPORT FORMAT (called at 3:35 PM):
  Analyze today's trades only. Be specific. Name the trades.
  Praise good discipline. Call out deviations clearly.

WEEKLY REPORT FORMAT (called Sunday 9 AM):
  Analyze last 14 days. Give win rates by category.
  Give ONE specific rule to implement this coming week.
  Track if last week's rule was followed.

RULES:
  Be honest and direct. This is a learning tool, not a cheerleader.
  Never tell the trader they did well if they did not.
  Always tie advice to specific trade data, not generalities.

INPUT: JSON with keys: report_type ("daily"|"weekly"|"context_for_orchestrator"),
trade_count, trades (list of trade dicts with 23 data points each)

OUTPUT: Valid JSON only.
{
  "win_rate_overall": float or null,
  "best_setup": "description" or "insufficient data",
  "worst_pattern": "description" or "insufficient data",
  "behavioral_flags": ["specific flag from recent trades"],
  "current_setup_context": "is current market similar to past winners?",
  "weekly_rule": "one specific rule to follow" or null,
  "report": "plain English full report text"
}

5.7 Agent 7 — Orchestrator + Risk Agent
SYSTEM PROMPT — agent_orchestrator.py

You are Agent 7 (Orchestrator + Risk) in TradeDesk.
You receive the outputs of Agents 1–6 and produce the final trade verdict.
You are the most important agent. Your output is what the trader sees and acts on.

YOUR TWO JOBS:
  JOB 1 — RISK VALIDATION (do this first, always):
    Check if trade is safe given: capital, open positions, VIX, DTE, market mode.
    If any hard rule is violated → output VERDICT: AVOID immediately.
    Do not let bullish signals override risk rules. Ever.

  JOB 2 — SYNTHESIS (only if risk passes):
    Combine all agent signals into one final verdict.
    Use CONTRADICTION RULE: If Agent 1 and Agent 3 disagree,
    AGENT 3 (Options) takes priority for Indian index option buying.
    Institutional money shows in OI, not RSI.

VERDICT OPTIONS: BUY CE | BUY PE | WAIT | AVOID

STRIKE SELECTION RULES (built into your output):
  DTE > 15: OTM acceptable  |  DTE 8-15: ATM preferred
  DTE 3-7: ATM or 1-ITM    |  DTE < 3: DO NOT BUY (output AVOID)
  VIX < 13: ATM or 1-OTM   |  VIX 13-18: ATM only
  VIX > 18: 1-ITM preferred |  Signal score < 14: DO NOT BUY
  Target delta range: 0.40 – 0.55 (reject delta < 0.25 or > 0.80)

EXIT PLAN RULES:
  Phase 1: Fixed SL at structural level → Target 1
  Phase 2: At T1, exit 50%, move SL to breakeven
  Phase 3: Trail remaining 50% with 1.5×ATR(15min) → Target 2 or 3:10 PM
  EXPIRY WEEK OVERRIDE (DTE ≤ 5): Full exit at T1. No trailing.

WHY SECTION RULES:
  Write for a beginner. No jargon. Short sentences.
  Explain what each agent found and why it matters.
  If WAIT or AVOID: explain exactly what needs to change for a BUY signal.

HARD RISK RULES (cannot be overridden by any agent signal):
  DTE < 3 days → AVOID always
  Daily loss already > 10% of capital → AVOID always
  3 positions already open → AVOID always
  Market mode = TIGHT RANGE and no BB squeeze → AVOID always
  Confluence score 2/4 or lower → AVOID unless Daily+1Hr both aligned

INPUT: JSON with all agent outputs + current_positions count +
daily_pnl_rupees + market_mode + lot_sizes dict + capital

OUTPUT: Valid JSON only.
{
  "verdict": "BUY CE" | "BUY PE" | "WAIT" | "AVOID",
  "instrument": "NIFTY" | "BANKNIFTY" | "FINNIFTY" | "SENSEX",
  "recommended_strike": float or null,
  "recommended_strike_type": "ATM" | "1-OTM" | "1-ITM" | null,
  "alternative_strikes": [
    {"strike": float, "type": "...", "delta_approx": float, "premium_approx": float}
  ],
  "entry_zone": {"low": float, "high": float},
  "stop_loss": float,
  "target_1": float,
  "target_2": float,
  "risk_reward": "1:X",
  "probability_bull": integer,
  "probability_bear": integer,
  "probability_sideways": integer,
  "confidence": integer 40-85,
  "why": "plain English explanation 4-8 sentences for a beginner",
  "what_to_watch": ["2-3 specific things that could invalidate this setup"],
  "risk_flags": ["any risk rule violations or near-violations"],
  "avoid_reason": null | "specific reason if AVOID"
}
 
6. Monitor Scoring Engine
6.1 Scoring Rules (0–22 points per candle)
Condition	Points	Category	Notes
Price closed decisively above/below key S&R level	3	Price Action	Close must be > 0.2% beyond zone edge
Volume this candle > 2x 20-candle average	3	Volume	Confirms move is real, not noise
Supertrend flipped direction	3	Trend	Strong regime change signal
Bullish/bearish candlestick pattern detected	2	Pattern	Hammer, engulfing, morning star etc.
OI shift in signal direction (15-min rolling)	2	OI	Measured over last 3 candles — not per candle
RSI crossed above 40 from below (oversold recovery)	2	Momentum	Directional momentum shift
MACD line crossed above/below signal line	2	Momentum	Confirmed cross only — not approaching
EMA 20 above EMA 50 (short-term trend)	1	Trend	Alignment check
Price above/below VWAP (intraday bias)	1	Price Action	Intraday institutional reference
ADX > 25 (strong trend present)	1	Trend	Filters sideways false signals
EMA 50 above EMA 200 (medium-term trend)	1	Trend	Higher timeframe alignment
BB squeeze breakout (6+ candle squeeze)	MAX ALERT	Special	Fires regardless of total score
MAXIMUM POSSIBLE SCORE	22		

6.2 Alert Thresholds + Cooldown
Score	Level	Sound	Action
0–9	SILENCE	None	Logged silently. Not shown.
10–13	SOFT (Yellow)	1 beep	Alert card. "Something forming — watch."
14–17	STRONG (Orange)	2 beeps	Alert card prominent. "Decent setup."
18–22	MAX (Red)	3 beeps	Full priority alert. "Multiple confirmations."

Cooldown Rule: 30-minute per-instrument cooldown after any STRONG or MAX alert fires. Prevents hammering the same alert repeatedly in one session. Resets on new trading day.

6.3 VIX-Adjusted Thresholds
•	VIX < 13: Soft=10, Strong=14 (normal market, trust signals). Source: NSE_INDEX|India VIX via Upstox. Refresh every 5 minutes.
•	VIX 13–18: Soft=12, Strong=16 (normal fear range).
•	VIX > 18: Soft=16, Strong=20 (elevated fear — fake breakouts common, need more confirmation).
•	Fallback: If VIX data unavailable, use VIX 13-18 (NORMAL) thresholds.
 
7. Market Mode Detection
7.1 Three Modes
Mode	Detection	Strategy	Size	Hysteresis
TRENDING	ADX > 25 AND Supertrend aligned AND day range > 80% daily ATR(14)	Breakout, momentum, trend continuation	100%	3 candles
MODERATE RANGE	ADX 15–25 AND day range 50–80% daily ATR(14)	CE at support, PE at resistance only. No chasing breakouts.	75%	3 candles
TIGHT RANGE/SQUEEZE	ADX < 15 AND BB width < 20th percentile (rolling 100 candles)	WAIT. No trades. Monitor for BB squeeze breakout only.	0%	3 candles

Hysteresis Rule: Require 3 consecutive candles satisfying new mode conditions before switching. Prevents whipsaw mode changes in choppy markets. Mode is evaluated fresh every 5-min candle.

•	Day Range definition: (today's high since 9:15 AM) minus (today's low since 9:15 AM). Updated every candle.
•	ATR reference: 14-period ATR on DAILY timeframe from previous close. Stable, not affected by forming intraday range.
•	Market mode is passed as explicit input field to Agent 7 (Orchestrator).
•	Mode priority over confluence on position sizing: Market Mode overrides. Tight Range = 0% regardless of confluence score.
 
8. Multi-Timeframe Confluence
8.1 Timeframe Hierarchy
Timeframe	Role	Weight	Bias Definition (precise)
1-Day	General	4	BULLISH: price>EMA50 AND Supertrend green AND close>VWAP daily. BEARISH: opposite. NEUTRAL: mixed.
1-Hour	Colonel	3	BULLISH: price>EMA50 AND Supertrend green AND last 3 closes above VWAP. BEARISH: opposite.
15-Min	Captain	2	BULLISH: price>EMA20 AND Supertrend green AND MACD above signal. BEARISH: opposite.
5-Min	Soldier	1	BULLISH: last candle close>open AND price>VWAP AND volume>1.2x avg. BEARISH: opposite.

8.2 Position Size by Confluence
Alignment	Label	Position Size	Condition
4/4	STRONG	100%	All four timeframes aligned
3/4	MODERATE	75%	Any 3 timeframes aligned
2/4 (Daily+1Hr)	WEAK-LEARNING	25%	ONLY if the two aligned are Daily AND 1-Hour
2/4 (other pairs)	WAIT	0%	15min+5min only = WAIT, no entry
1/4 or 0/4	IGNORE	0%	Counter-trend. High failure rate.
 
9. Strike Selection & Exit Strategy
9.1 Four-Factor Strike Selection
Factor	Rule	Rationale
DTE	> 15: OTM ok. 8–15: ATM preferred. 3–7: ATM or 1-ITM. < 3: DO NOT BUY.	Theta decay accelerates sharply below 5 DTE. Hard stop at DTE<3.
VIX	< 13: ATM or 1-OTM. 13–18: ATM only. > 18: 1-ITM.	High IV inflates premiums. ITM has more intrinsic value, less IV crush risk.
Signal Score	18+: ATM (max delta). 14–17: 1-OTM. < 14: DO NOT BUY.	Weaker signals do not justify premium risk.
Target Distance	Strike where target puts option 50+ points ITM.	Prevents choosing strike where correct move gives tiny profit.
Delta Range	Target delta 0.40–0.55. Reject delta < 0.25 or > 0.80.	Delta < 0.25 = lottery ticket. Delta > 0.80 = capital inefficient.

•	System shows top 3 alternative strikes with estimated premium, delta, and capital required. Trader picks. Manual deviation logged in journal as "deviated from AI strike."

9.2 Three-Phase Exit Strategy
•	PHASE 1 (Entry → T1): Fixed structural SL. Exit if SL hit OR T1 reached.
•	PHASE 2 (At T1): Exit 50% position. Move SL to breakeven. Trade now risk-free on remaining half.
•	PHASE 3 (T1 → T2): Trail remaining 50% with 1.5 × ATR(14 on 15-min chart). High VIX days: use 2.0 × ATR. Exit at T2 or 3:10 PM or trail SL hit.
Expiry Week Override (DTE ≤ 5): Full 100% exit at Target 1. No trailing. Theta destroys position while waiting for T2. No new option buys after 12:30 PM. Auto-switch recommended strikes to next week expiry.
 
10. Gap Open Handling Protocol
Gap Type	Trigger (vs Daily ATR14)	Wait Period	Protocol
NO GAP	< 30% ATR	None	Normal. S&R levels valid.
SMALL GAP	30–70% ATR	15 min	Caution. Threshold +2. Recalc nearest S&R. Size: 100%.
MEDIUM GAP	70–120% ATR	30 min	Gap protocol. Show Fill vs Continue scenarios. Size: 75%.
LARGE GAP	> 120% ATR	45 min	Near-freeze. Min score 18+. Size: 50%. All S&R recalculated.

•	ATR reference: Daily ATR(14) from previous trading day close. Stable — not distorted by forming intraday range.
•	Gap fill level (previous close) automatically added as Very High priority S&R zone for the day.
•	After wait period: re-run strike selector with current live IV before suggesting entry. Post-gap premiums are inflated.
•	Morning briefing at 8:45 AM predicts gap using GIFT Nifty vs previous close. Warning shown before open.
 
11. Risk Management
Phase 1 Priority: BUILD FIRST → LEARN → OPTIMIZE. Risk rules are guardrails, not the focus. The focus is getting the system working and learning from paper trades. Fine-tuning happens later.

11.1 Hard Limits (Enforced by Agent 7)
•	Maximum 3 open positions at any one time.
•	Never hold option past 3:10 PM (20 min before close).
•	Never buy option with DTE < 3 days.
•	If VIX > 20: reduce position size by 50% automatically.
•	Minimum risk:reward 1:1.5 for any recommended trade.
•	If daily loss exceeds 10% of capital: STOP TRADING TODAY banner. Monitor suppressed.
•	Paper trading mandatory: 8 weeks minimum OR 100 trades, whichever comes first.
•	Kill criterion: After 3 months paper trading, if win rate < 48% AND expectancy negative → architecture review before going live.
11.2 Drawdown Management (Real Time — No Hard Code)
•	Weekly loss monitoring: You decide when to pause. System shows running weekly P&L prominently.
•	Monthly loss monitoring: You decide when to stop. Full system pause = your call based on what you observe.
•	System pause action: Review BRD, agent prompts, and scoring thresholds before resuming.
 
12. Database Schema (market_data.db)
12.1 Nine Tables
Table	Key Columns	Purpose
candles	symbol, timeframe, timestamp, OHLCV	All 6 timeframes stored. ~360k rows/year. 3-year retention.
indicators	symbol, timeframe, timestamp + all values	Pre-computed. Loaded on startup. No recomputation.
support_resistance	symbol, price_low/mid/high, strength, touches	S&R zones with scoring. Auto-updated daily. Broken zones inactive.
options_chain	symbol, expiry, strike, CE/PE OI/LTP/IV/Greeks	FULL chain stored (all strikes). Snapshot every 2 min during market hours. Pruned after 90 days.
trades	23 data points per trade	Full paper + live journal. Behavioral data included.
alerts	Every monitor alert with score, conditions, outcome	Post-market alert quality analysis.
news_events	Headline, predicted impact, actual move next day	Historical news → impact learning database.
strategies	Conditions JSON, backtest results, win rate, R:R	Pre-defined + generated strategy backtest results.
lot_size_history	instrument, lot_size, effective_from, effective_to	Historical lot sizes for accurate backtest P&L. Pre-populated with all NSE changes since 2022.

12.2 Lot Size History — Pre-Populated Values
Instrument	Lot Size	Effective From	Effective To	Source
NIFTY 50	25	2010-01-01	2024-10-27	Pre-SEBI mandate
NIFTY 50	75	2024-10-28	2025-12-30	SEBI ₹15L minimum
NIFTY 50	65	2026-01-06	present	NSE Oct 2025 circular
BANKNIFTY	25	2010-01-01	2024-10-27	Pre-SEBI mandate
BANKNIFTY	35	2024-10-28	2025-12-30	SEBI ₹15L minimum
BANKNIFTY	30	2026-01-06	present	NSE Oct 2025 circular
FINNIFTY	40	2021-01-01	2024-10-27	Launch lot size
FINNIFTY	65	2024-10-28	2025-12-30	SEBI ₹15L minimum
FINNIFTY	60	2026-01-06	present	NSE Oct 2025 circular
SENSEX	10	2010-01-01	2024-12-31	BSE original
SENSEX	20	2025-01-01	present	BSE regulatory review
 
13. Complete File Structure
File / Folder	Purpose
main.py	Entry point. Run this to start TradeDesk.
requirements.txt	All pip packages with exact versions.
.env	API keys. Never committed to git.
config/config.json	Capital, thresholds, instruments, display settings.
config/instruments.json	User-defined instrument list. Add any stock via Settings panel.
data/market_data.db	SQLite database. All 9 tables including lot_size_history.
logs/trades.json	Trade journal JSON export.
logs/daily_reports/	One JSON per trading day from Agent 6.
logs/weekly_reports/	Weekly analysis reports from Agent 6.
logs/app.log	General application logs with rotation.
logs/monitor.log	Monitor alert history.
tests/	Unit + smoke tests per sprint. Basic test coverage.
core/data_provider.py	Adapter layer. One-line Upstox↔Kite switch.
core/local_db.py	All SQLite read/write. Schema init.
core/candle_builder.py	Tick → OHLCV all timeframes.
core/startup.py	6-phase startup orchestrator.
core/token_manager.py	Upstox daily token refresh (semi-automated).
feeds/upstox_stream.py	WebSocket live tick handler.
feeds/upstox_rest.py	Historical data fetch + gap fill.
feeds/kite_*.py	Kite fallback (same interface as Upstox).
feeds/news_fetcher.py	NewsAPI headlines fetch.
feeds/fii_fetcher.py	NSE FII/DII data scraper.
feeds/global_fetcher.py	GIFT Nifty, US markets, India VIX.
analysis/indicators.py	pandas-ta wrapper. All indicators.
analysis/sr_detector.py	Swing detection + clustering + scoring + role reversal.
analysis/pattern_detector.py	Candlestick + chart pattern detection.
analysis/market_mode.py	ADX + BB + range → mode detection with hysteresis.
analysis/confluence.py	4-TF weighted bias scoring.
analysis/gap_handler.py	Gap classification + protocol + S&R recalculation.
analysis/strike_selector.py	4-factor + delta strike selection model.
analysis/scorer.py	0–22 monitor scoring engine.
monitor/monitor.py	Main monitor loop 9:15–3:10 with cooldown.
monitor/alert_engine.py	Alert card generation + winsound.
monitor/expiry_manager.py	DTE checks + expiry week protocol.
agents/agent_base.py	Claude API wrapper. Rate limiting. Prompt caching. Error handling.
agents/agent_indicator.py	Agent 1 — Indicator analysis.
agents/agent_confluence.py	Agent 2 — Confluence + S&R (merged).
agents/agent_options.py	Agent 3 — Options chain.
agents/agent_news.py	Agent 4 — News + FII.
agents/agent_chart.py	Agent 5 — Chart pattern.
agents/agent_journal.py	Agent 6 — Journal + behavioral.
agents/agent_orchestrator.py	Agent 7 — Orchestrator + Risk.
backtest/backtest_engine.py	Strategy combinations on historical OHLCV.
backtest/option_pricer.py	Phase 1: spot proxy. Phase 2: Black-Scholes. Phase 3: bhavcopy.
backtest/strategy_library.py	5 pre-defined + generated strategies.
backtest/strategy_ranker.py	Ranks by win_rate×0.4 + expectancy×0.3 + recency×0.3. Min Profit Factor > 1.5.
journal/trade_logger.py	23 data points per trade. Default checkboxes.
journal/daily_reporter.py	3:35 PM auto daily report via Agent 6.
journal/weekly_reviewer.py	Sunday 9 AM weekly report via Agent 6.
ui/app.py	Tkinter + ttkbootstrap main window. 4-thread. Queue. poll_queue().
ui/panels/chart_panel.py	mplfinance embedded. Candle updates. S&R + signal overlays.
ui/panels/monitor_panel.py	Alert cards. Score display. Cooldown indicator.
ui/panels/analysis_panel.py	7 agent outputs. Probability meters. WHY text.
ui/panels/options_panel.py	Full options chain. PCR. Max Pain. OI bars.
ui/panels/journal_panel.py	Trade log. Paper trade entry. Default checkboxes.
ui/panels/briefing_panel.py	Morning briefing display.
ui/panels/settings_panel.py	6-tab config. Add/remove instruments. Thresholds.
ui/assets/sounds/*.wav	soft_alert, strong_alert, max_alert beep files.
 
14. Build Order — 8 Sprints
Timeline: 8–16 weeks depending on time available per day. Not 8 weeks flat. Quality over speed. Each sprint has a clear test gate — do not proceed until tests pass.

Sprint	Week	Files Built	Test Gate
1	1	local_db.py, candle_builder.py, upstox_rest.py, startup.py, market_simulator.py	 Print last 5 NIFTY candles. OHLCV matches reference. Simulator runs.
2	1–2	indicators.py, sr_detector.py, scorer.py  market_mode.py, confluence.py (remaining)	S&R zones match visual chart. Scorer gives expected scores. indicators: 28 columns verified. Remaining: market_mode + confluence.
3	2	upstox_stream.py, news_fetcher.py, fii_fetcher.py, global_fetcher.py, token_manager.py	Live ticks print to console. Candles match Upstox chart exactly. News fetched correctly.
4	2–3	monitor.py, alert_engine.py, expiry_manager.py	Console-only monitor during market hours 3–5 days. 3–7 actionable alerts/day. Cooldown working.
5	3	agent_base.py + all 7 agent files	Manual orchestrator call. JSON parses correctly. WHY text readable by beginner. Risk blocks violations.
6	3	trade_logger.py, daily_reporter.py, weekly_reviewer.py, backtest_engine.py, option_pricer.py, strategy_ranker.py	5 fake trades logged. Daily report generates. Backtest on 6-month data. Walk-forward passes.
7	4	ui/app.py + all 7 panels + ttkbootstrap theming	Full app market hours. No freeze. Alerts display. Analyze Now works. Journal captures. Charts render.
8	5–16	data_provider.py, gap_handler.py, strike_selector.py, kite fallbacks. 8 weeks paper trading.	Paper trade 8 weeks / 100 trades. Weekly reports generated. Thresholds tuned. Kill criterion check.
 
15. Startup Sequence
Phase	Time (realistic)	What Happens
0	0–2s	Config load, dependency check, folders + DB creation if missing. Holiday calendar check — if today is market holiday, show message and exit monitor.
1	2–4s	Tkinter + ttkbootstrap window opens. Splash screen with per-phase progress messages.
2	4–15s	BACKGROUND: Load historical OHLCV. Derive all timeframes. Compute indicators. Compute S&R. Detect market mode. Message: "Loading candles... Computing indicators... Building S&R zones..."
3	15–25s	BACKGROUND: Upstox REST API gap fill once per instrument. Update indicators on new candles.
4	15–35s	PARALLEL with Phase 3: Fetch GIFT Nifty, US market, VIX, FII/DII, news. Claude Agent 4 generates morning briefing. Display in briefing panel.
5	25–30s	Open Upstox WebSocket. Subscribe all instruments. Validate token. If expired: browser opens automatically for 30-second login.
6	30–40s	Monitor activates. Market time check: if holiday → show closed message. If < 9:30 → suppression mode. If > 9:30 → full active. Splash → dashboard.

Holiday Calendar: Indian market holidays loaded from config/holidays.json. Populated at start of each year. Check on startup Phase 0. If today is holiday: monitor stays off, historical data available for review.
 
16. Trade Journal — 23 Data Points
16.1 Complete Column List
#	Data Point	Category	Captured	Purpose
1	VIX at entry	Market Context	Auto	Fear level at trade time
2	Market Mode	Market Context	Auto	Trending/Range/Squeeze
3	Session Phase	Market Context	Auto	Morning/Midday/Afternoon
4	ADX at entry	Market Context	Auto	Trend strength
5	Nifty Daily Trend	Market Context	Auto	Daily bias at entry
6	Nifty 1-Hour Trend	Market Context	Auto	Session bias at entry
7	Monitor Score	Signal Quality	Auto	0–22 score that triggered alert
8	AI Confidence %	Signal Quality	Auto	Orchestrator output
9	AI Verdict	Signal Quality	Auto	BUY CE/PE/WAIT/AVOID
10	Pattern Detected	Signal Quality	Auto	Chart pattern from Agent 5
11	Volume Ratio	Signal Quality	Auto	vs 20-candle average
12	RSI at entry	Signal Quality	Auto	14-period RSI value
13	MACD Signal	Signal Quality	Auto	Bullish/Bearish cross
14	Confluence Score	Setup	Auto	X/4 timeframes aligned
15	Distance from S&R %	Setup	Auto	% from nearest zone
16	S&R Zone Strength	Setup	Auto	WEAK/MODERATE/STRONG/VERY STRONG
17	Days to Expiry	Setup	Auto	DTE at entry time
18	Day of Week	Setup	Auto	Mon-Fri
19	Followed AI Verdict	Behaviour	Manual (default: YES)	Discipline check
20	Deviated from SL	Behaviour	Manual (default: NO)	SL discipline
21	Exited Early	Behaviour	Manual (default: NO)	Patience check
22	Sized Correctly	Behaviour	Manual (default: YES)	Position discipline
23	Exit Efficiency %	Outcome	Auto	(exit P&L / max favorable) × 100

Journal Fatigue Solution: Behavioural checkboxes default to YES/NO (most common). User only changes if they deviated. Entry effort per trade = 10 seconds. System auto-detects some deviations (actual exit vs recommended SL) and pre-flags.
 
17. Backtest Engine
17.1 Three-Phase Option Pricing
Phase	Method	Accuracy	When
1 (Now)	Spot proxy: ATM delta ~0.5	±30-50%	Signal direction testing
2 (Month 2)	Black-Scholes + historical VIX from NSE	±10-15%	Strategy P&L ranking
3 (Month 3+)	NSE Bhavcopy actual option prices (~2MB/day)	100%	Final validation before live

17.2 Strategy Testing + Anti-Overfitting
•	5 pre-defined strategies first: RSI Reversal, EMA Crossover, Supertrend Flip, Breakout with OI, VWAP Reclaim.
•	Combination testing: 20 indicators → 3-indicator combos = 1,140 strategies. Runs post-market at 3:45 PM automatically.
•	Walk-forward validation: Any strategy passing Phase 1 backtest must also pass on last 40 days (held out, not used in optimization).
•	Transaction costs: ₹40 per trade (₹20 entry + ₹20 exit) included in all backtest P&L calculations.
•	Minimum threshold: Profit Factor > 1.5 required before strategy is ranked as valid.
•	Ranking: win_rate × 0.4 + expectancy × 0.3 + recent_performance × 0.3.
 
18. Decision Log
Date	Decision	Reasoning	Impact
Apr 2026	Upstox primary, Kite fallback via adapter	Upstox free. Kite ₹500/month. Same quality.	Saves ₹6k/year.
Apr 2026	Tkinter + ttkbootstrap desktop	HTML+Flask → CORS errors. Tkinter = native, no server.	Stable. Modern look via ttkbootstrap added Sprint 7.
Apr 2026	SQLite 9 tables + WAL mode	Single file, no server, built-in Python.	Simple backup. Good performance.
Apr 2026	Two-tier monitor (code + AI on demand)	Continuous AI = ₹5k+/month. Rule-based = near zero.	Claude cost ~₹1,800–2,350/month.
Apr 2026	7 agents (reduced from 8)	Agent 2 S&R merged into Agent 2 Confluence. Duplicated effort removed.	Less cost. Same capability.
Apr 2026	All agents: claude-sonnet-4-6	Consistency first. Cost review in Phase 3 only.	Best reasoning quality throughout Phase 1-2.
Apr 2026	Prompt caching from day one	Anthropic caching cuts 50-80% cost on repeated system prompts.	Cost reduction built in from agent_base.py Sprint 5.
Apr 2026	Agent prompts in BRD + agents/ files	BRD = reference. Files = runtime. Update both when prompt changes.	Single source of truth maintained.
Apr 2026	Options chain: full chain stored, 90-day prune	Good laptop + storage confirmed. No ATM±10 filtering needed.	Simpler code. Full data available.
Apr 2026	Options chain priority over indicators on contradiction	Institutional money shows in OI, not RSI.	Open Question 5 resolved.
Apr 2026	8 weeks / 100 trades paper trading minimum	4 weeks too short. Need statistical base.	Extended from v2. Real money delayed.
Apr 2026	Kill criterion: <48% win rate + negative expectancy after 3 months → review	Honest success/failure metric added.	Architecture review triggered if not working.
Apr 2026	Stock selection: user-defined via Settings panel	No pre-defined stock list in BRD. Trader adds what they want.	Flexibility > rigidity.
Apr 2026	Drawdown limits: real-time trader decision, not hardcoded	Paper trading phase. Trader monitors and decides when to pause.	System shows running P&L prominently.
Apr 2026	Lot sizes corrected (Jan 2026 values)	Previous BRD had pre-2024 wrong values. Verified April 2026.	NIFTY=65, BNF=30, FINNIFTY=60, SENSEX=20.
Apr 2026	lot_size_history table added to DB	NSE changes lot sizes. Backtest must use correct size per date.	Accurate historical P&L in Phase 2-3 backtest.
Apr 2026	Market holiday calendar in startup Phase 0	Monitor must not run on holidays. Check at startup.	holidays.json populated annually.
Apr 2026	OI measured over 15-min rolling window (3 candles)	Per-candle OI is noisy. 3-candle aggregate reduces false signals.	Cleaner OI shift detection in scorer.
Apr 2026	30-min cooldown after STRONG/MAX alert	Prevent alert hammering same instrument repeatedly.	Max 2 actionable alerts per instrument per hour.
Apr 2026	3-candle hysteresis before mode switch	Prevent whipsaw market mode changes.	Stable mode detection.
Apr 2026	2/4 confluence with Daily+1Hr = 25% learning position	Partial alignment allowed only if highest-weight TFs agree.	Beginner learning trades at minimal size.
Apr 2026	Walk-forward 40-day out-of-sample for backtest	Anti-overfitting measure. 1,140 combos risk curve-fitting.	Strategies must pass held-out period.
Apr 2026	Profit Factor > 1.5 minimum strategy threshold	Win rate alone insufficient. Need gross profit/loss ratio.	Filters weak strategies from ranking.
Apr 2026	AI fallback = Tier-1 only if Claude down	No local LLM. Too complex. Tier-1 always runs independently.	Graceful degradation already designed.
Apr 2026	Bias definition per timeframe: price>EMA50 + Supertrend + VWAP	Previously undefined. Now precise algorithm.	Confluence scoring is deterministic.
Apr 2026	Timeline adjusted to 8-16 weeks	8 weeks = full-time. Part-time needs up to 16 weeks.	Realistic expectations.
Apr 2026	tests/ folder added from Sprint 1	Smoke tests per sprint. Catch issues before building on top.	Basic test coverage throughout.
 
19. Open Questions — All Resolved
Status: All 6 original open questions from v2 are now resolved and moved to Decision Log. New open questions added below as they arise.

#	Question	Answer	Status
1	Journal: local or Google Sheet?	Local SQLite. JSON export available. No Google Sheet — privacy risk.	✅ RESOLVED
2	Telegram: full analysis or VERDICT only?	VERDICT + Entry + SL + Target + Confidence. Full analysis stays on desktop.	✅ RESOLVED
3	Chart: mplfinance or TradingView?	mplfinance. Cannot overlay custom signals on TradingView widget.	✅ RESOLVED
4	Which 6 additional stocks?	User-defined via Settings panel. No pre-set list. Add any instrument.	✅ RESOLVED
5	Agent 1 vs Agent 3 contradiction?	Options chain (Agent 3) takes priority for Indian index option buying.	✅ RESOLVED
6	Morning briefing: auto or manual?	Manual click on dashboard. Simple, no Windows Task Scheduler dependency.	✅ RESOLVED

19.1 Currently Open
#	Question	Blocks	Status
1	GIFT Nifty calibration: show last-30-day accuracy of GIFT Nifty predictions vs actual opens in morning briefing?	feeds/global_fetcher.py + briefing_panel.py	OPEN
2	Telegram alerts: build in Sprint 3 or Sprint 8?	feeds/ + agents/agent_orchestrator.py	OPEN
3	Add docs/ or README.md for setup instructions?	Project hygiene	OPEN

TradeDesk BRD v4.0  |  April 2026  |  All Chat 1–18 decisions incorporated  |  DRAFT
