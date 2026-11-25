

TRADEDESK
AI-Powered Personal Trading Terminal
Business Requirements Document  v6.0
April 2026  |  Single source of truth  |  Supersedes v4, v5.0, v5.1 and all patch documents

Capital	Agents	Market Hours	Strategy	Screens
₹1,00,000	8 Agents	9:15–3:30 IST	Option Buying Only	Screen 1 (TA) + Screen 2 (SMC)

v6.0 IS THE ONLY DOCUMENT YOU NEED. It incorporates everything from BRD v4 (foundation), v5.0 (SMC + strategy builder + auto paper trading), v5.1 (behavior-based pipeline architecture), and all patch decisions. Do not reference any earlier version.

 
1. Executive Summary
TradeDesk is a local Windows desktop application for personal option buying in Indian equity derivatives. It uses Upstox API for live data, an 8-agent Claude AI architecture for analysis, and presents everything across two screens in a single Tkinter desktop window. No browser, no cloud, no paid dashboard subscriptions.

Core Philosophy: The AI is the analyst. The human is the trader. Strategy signals originate from price behavior observations — not indicator combinations. Indicators confirm. Options chain gates. Regime permits. Math sizes. Agent produces verdict. BUILD → LEARN → OPTIMIZE.

1.1 The 5-Component Strategy Foundation
Every strategy in TradeDesk is built on exactly 5 components:

Component	What It Means	How TradeDesk Implements It
Entry Logic	When and why to enter	4 Core Behaviors (primary signal source) + Indicator confirmation (secondary) + Options chain gate
Exit Logic	When and how to exit	3-phase exit: fixed SL → T1 partial exit → trail to T2. Hard exit: 3:10 PM always.
Position Sizing	How much to risk	Rolling Kelly formula. Quarter-Kelly. 10% hard cap. 2% floor. Per-behavior independent sizing.
Market Context Filter	Which conditions permit trading	Market regime + Kill zones + VIX filter + News filter
Edge Measurement	Proof the strategy works	Behavior backtest + walk-forward 40-day validation + live paper trading comparison

1.2 Problem Statement
•	Retail option buyers lose money due to emotional decisions and no real-time multi-indicator analysis.
•	Existing platforms give raw data but no integrated AI reasoning or WHY explanations.
•	SMC concepts (FVG, Order Blocks, CHoCH) are difficult to identify manually in real time.
•	No platform combines classical TA + SMC + behavior-based strategy with unified journal and backtest.

1.3 Success Criteria
Paper Trading Gate: Minimum 8 weeks OR 100 trades per screen (whichever comes first) before any real money. Both screens independently.

Kill Criterion (per behavior): win rate < 48% after 30+ signals → behavior moves to WATCH. After 4 more weeks < 48% → RETIRED. Aggregate win rate is never used.

Metric	Working	Watch Zone	Failing	Action
Win rate per behavior	≥ 55%	48–54%	< 48%	< 48%: move behavior to WATCH status
Profit Factor per behavior	> 1.5	1.2–1.5	< 1.2	< 1.2: reduce size 50% on that behavior
Expectancy	Positive	Near zero	Negative	Kelly naturally outputs floor — self-correcting
Overall system	≥ 52% win, PF > 1.4	Near breakeven	Losing money	Full architecture review before live

 
2. Architecture — All Decisions Locked
2.1 Technology Stack
Layer	Technology	Decision
Desktop UI	Python Tkinter + ttkbootstrap	Native Python. No CORS. No server. ttkbootstrap for Bootstrap-style look. Sprint 7.
Chart Engine	mplfinance + FigureCanvasTkAgg	Full control: S&R overlays, SMC zone overlays, agent signals. TradingView rejected — cannot overlay custom data.
Primary Data	Upstox API — upstox-python-sdk==2.21.0	WebSocket live ticks (MarketDataStreamerV3), 2yr historical OHLCV, full options chain. Free. OLD upstox-client DEPRECATED.
Fallback Data	Kite Connect — kiteconnect==5.1.0	Adapter pattern. One-line switch. ₹500/month. Only if Upstox fails.
Indicators	pandas-ta library	130+ indicators computed locally. No API call. Milliseconds per run.
SMC Engine	smart-money-concepts>=0.0.6	joshyattridge Python library. FVG, OB, BOS/CHoCH, Liquidity detection base for Screen 2.
Database	SQLite — market_data.db	WAL mode. 13 tables. No server. Built into Python.
AI Engine	Claude Sonnet 4.6 (claude-sonnet-4-6)	All 8 agents. Phase 1-2. Cost review Phase 3 only.
Prompt Caching	Anthropic Prompt Caching	50-80% cost reduction on repeated system prompts. In agent_base.py from day one.
News	Claude web_search + NewsAPI	Agent 4 uses Claude web search. NewsAPI 100 free calls/day.
Storage	Local SQLite + JSON exports	trades.json and smc_trades.json. No cloud.
Threading	4-thread architecture	Main(UI), Data(WebSocket), Network(APIs), Agent(Claude). Queue-based. Zero UI freeze.
OS	Windows 10/11 only Phase 1	winsound for alerts. Windows paths. No cross-platform until Phase 4.
Python	3.13.13	pandas-ta 4.71b supports 3.13. Stable + security until 2029.
AI Fallback	Tier-1 monitor only	Claude API down → rule-based monitor continues. Screen 2 SMC engine continues (zero AI cost).
Security	.env for API keys	Never committed to git. SQLite unencrypted — acceptable for personal use.

2.2 Data Architecture
Upstox REST called ONCE at startup for gap fill. WebSocket carries only live ticks. All 6 timeframes derived locally from 5-min base candles. Zero extra API calls during market hours.

2.3 Package Versions (Verified April 2026)
Install: pip install -r requirements.txt  (inside venv with Python 3.13.13)

Package	Version	Notes
pandas	3.0.2	Python 3.13 support.
pandas-ta	0.4.71b0	Python 3.13 official support.
upstox-python-sdk	2.21.0	NEW name. Old upstox-client DEPRECATED.
smart-money-concepts	>=0.0.6	SMC library for Screen 2.
kiteconnect	5.1.0	Fallback only.
mplfinance	0.12.10b0	Embedded charts. SMC overlays Sprint 11.
ttkbootstrap	1.20.2	Bootstrap-style Tkinter UI.
anthropic	>=0.40.0	Claude Sonnet 4.6 + prompt caching.
scipy	>=1.13.0	Black-Scholes Phase 2 backtest.
numpy	>=1.26.4	Required by pandas + pandas-ta.
python-dotenv	>=1.0.1	API key security via .env
beautifulsoup4	>=4.12.3	NSE FII/DII scraping.
newsapi-python	>=0.2.7	100 free calls/day.
schedule	>=1.2.1	Daily/weekly report triggers.
colorama	>=0.4.6	Colored console for simulator.
pytz	>=2024.1	IST timezone.
openpyxl	>=3.1.2	Journal CSV/Excel export.

2.4 Windows Setup
•	1. Download Python 3.13.13 from python.org — CHECK 'Add Python to PATH'
•	2. cd C:\TradeDesk
•	3. py -3.13 -m venv venv
•	4. venv\Scripts\activate
•	5. pip install -r requirements.txt
•	6. python -c "import pandas_ta; import smart_money_concepts; import ttkbootstrap; print('OK')"

 
3. Instrument Lot Sizes (Verified April 2026)
Lot sizes changed January 2026 per NSE circular. These are the correct current values.

Instrument	Lot Size 2026	Old Size	Changed	At ₹80 ATM
NIFTY 50	65	75	Jan 2026	65 × ₹80 = ₹5,200
BANKNIFTY	30	35	Jan 2026	30 × ₹200 = ₹6,000
FINNIFTY	60	65	Jan 2026	60 × ₹80 = ₹4,800
SENSEX	20	10	Mid 2025	20 × ₹250 = ₹5,000

3.1 Lot Size History (Pre-Populated in DB)
Instrument	Lot Size	From	To	Source
NIFTY 50	25	2010-01-01	2024-10-27	Pre-SEBI
NIFTY 50	75	2024-10-28	2025-12-30	SEBI ₹15L
NIFTY 50	65	2026-01-06	present	NSE Oct 2025
BANKNIFTY	25	2010-01-01	2024-10-27	Pre-SEBI
BANKNIFTY	35	2024-10-28	2025-12-30	SEBI ₹15L
BANKNIFTY	30	2026-01-06	present	NSE Oct 2025
FINNIFTY	40	2021-01-01	2024-10-27	Launch
FINNIFTY	65	2024-10-28	2025-12-30	SEBI ₹15L
FINNIFTY	60	2026-01-06	present	NSE Oct 2025
SENSEX	10	2010-01-01	2024-12-31	BSE original
SENSEX	20	2025-01-01	present	BSE review

 
4. Multi-Agent AI System (8 Agents)
4.1 Agent Overview
Agent	Role	Screen	Key Output
Agent 1 — Indicator	Selects best 3 aligned indicators. Composite signal.	Screen 1	signal, confidence, top_3, reasoning
Agent 2 — Confluence + S&R	4-TF alignment + key S&R zones.	Screen 1	confluence_score, bias per TF, nearest S&R
Agent 3 — Options	PCR, Max Pain, IV rank, OI buildup. Hard veto on pipeline Layer 3.	Screen 1	oi_bias, pcr, max_pain, iv_rank, theta_warning
Agent 4 — News + FII	Live news. FII/DII. Global cues. Pre-filter for HIGH impact news.	Screen 1	overall_sentiment, fii_action, key_news
Agent 5 — Chart Pattern	Chart patterns + breakout levels. Detects Behavior 4 squeeze.	Screen 1	primary_pattern, breakout_level, squeeze_alert
Agent 6 — Journal	Trade history analysis. Per-behavior win rates. Behavioral flags.	Screen 1	behavior_breakdown, weakest/strongest behavior
Agent 7 — Orchestrator + Risk	Pipeline orchestrator. Interprets pipeline result. Final verdict.	Screen 1	verdict, pipeline_score, position_size_rupees, why
Agent 8 — SMC Orchestrator	Interprets SMC engine output. SMC-based trade verdict.	Screen 2	verdict, smc_score, kill_zone, why

4.2 Screen 1 Execution Flow
•	Agents 1-6 run in PARALLEL. Total wait: 8-15 seconds.
•	Pipeline (Layers 1-6) runs every 5-min candle close — zero AI cost.
•	User clicks Analyze when pipeline_score >= 70.
•	Agent 7 receives pipeline result + all 6 agent JSONs → final verdict.
•	Contradiction rule: Agent 1 vs Agent 3 disagree → Agent 3 (Options) takes priority.

4.3 Screen 2 Execution Flow
•	SMC engine runs every 5-min candle. Zero AI cost.
•	SMC score (0-100) displayed live. Analyze button enabled when score >= 70.
•	Agent 8 triggered only when user clicks Analyze manually.

4.4 Cost Per Analysis Run
Item	Per Run	Per Month (5/day × 22 days)
Screen 1: Agents 1-6 parallel (prompt caching)	~₹10-15	₹1,100-1,650
Screen 1: Agent 7	~₹5	₹550
Screen 1: Daily reports × 22	—	₹110
Screen 1: Weekly reports × 4	—	₹32
Screen 2: Agent 8 (~5/day)	~₹8-12	₹880-1,320
Screen 2: SMC reports	—	₹114
TOTAL	~₹23-32/run	~₹2,786-3,776/month

 
5. Agent Prompts (Production — All 8)
These are the definitive prompts. Actual files live in agents/ folder. Update BRD when prompts change. Structure: ROLE → RULES → INPUT → OUTPUT.

5.1 Agent 1 — Indicator Agent
SYSTEM PROMPT — agents/agent_indicator.py
You are Agent 1 (Indicator Analyst) in TradeDesk.
Your ONLY job: analyze technical indicators for one Indian index instrument.
You are a specialist — do not analyze options, news, or chart patterns.

RULES:
- Select the 3 indicators most aligned with each other.
- Do not force a signal. Mixed indicators = NEUTRAL with low confidence.
- Never hallucinate values. Only use what is provided in INPUT.
- Confidence range: 40-85%. Never 90%+.
- Reasoning: 2-3 sentences. Plain English. No jargon.

INPUT JSON keys: symbol, timeframe, rsi_14, macd_line, macd_signal,
  macd_hist, ema_20, ema_50, ema_200, supertrend_dir, adx_14,
  bb_upper, bb_lower, bb_width, atr_14, volume_ratio, vwap,
  current_price, market_mode

OUTPUT: Valid JSON only. No markdown.
{  "signal": "BULLISH"|"BEARISH"|"NEUTRAL",
   "confidence": integer 40-85,
   "top_3_indicators": ["name: value and meaning", ...],
   "supporting": ["other confirming signals"],
   "conflicting": ["signals that disagree — be honest"],
   "reasoning": "2-3 sentences plain English",
   "avoid_reason": null | "reason if signal is weak" }

5.2 Agent 2 — Confluence + S&R Agent
SYSTEM PROMPT — agents/agent_confluence.py
You are Agent 2 (Confluence + S&R Analyst) in TradeDesk.
Your job: assess multi-timeframe alignment AND identify key S&R levels.

TIMEFRAME HIERARCHY:
  1-Day   = General (weight 4) — never trade against this
  1-Hour  = Colonel (weight 3) — session direction
  15-Min  = Captain (weight 2) — trade direction
  5-Min   = Soldier (weight 1) — entry timing only

BIAS DEFINITION (exact — do not simplify):
  BULLISH = price > EMA50 AND Supertrend green AND last 3 closes above VWAP
  BEARISH = price < EMA50 AND Supertrend red AND last 3 closes below VWAP
  NEUTRAL = anything mixed

CONFLUENCE RULES:
  4/4 = STRONG (100% position)   3/4 = MODERATE (75%)
  2/4 Daily+1Hr = WEAK (25%)     2/4 other = WAIT (0%)
  1/4 or 0/4 = IGNORE

S&R: Report nearest support below + nearest resistance above.
Only zones with strength MODERATE or higher. Add GAP_FILL if gap today.

INPUT JSON keys: symbol, current_price, bias_1d, bias_1hr, bias_15m,
  bias_5m, sr_zones (list), gap_open_today (bool), gap_fill_level

OUTPUT: Valid JSON only.
{  "confluence_score": "4/4"|"3/4"|"2/4"|"1/4",
   "confluence_label": "STRONG"|"MODERATE"|"WEAK"|"IGNORE",
   "bias_1d": "BULLISH"|"BEARISH"|"NEUTRAL",
   "bias_1hr": "BULLISH"|"BEARISH"|"NEUTRAL",
   "bias_15m": "BULLISH"|"BEARISH"|"NEUTRAL",
   "bias_5m": "BULLISH"|"BEARISH"|"NEUTRAL",
   "nearest_support": float, "support_strength": "VERY STRONG"|"STRONG"|"MODERATE",
   "nearest_resistance": float, "resistance_strength": "VERY STRONG"|"STRONG"|"MODERATE",
   "distance_from_support_pct": float,
   "reasoning": "2-3 sentences on alignment and key levels" }

5.3 Agent 3 — Options Chain Agent
SYSTEM PROMPT — agents/agent_options.py
You are Agent 3 (Options Chain Analyst) in TradeDesk.
Your job: interpret options chain data and determine institutional bias.
Agent 3 output feeds pipeline Layer 3 — you have VETO POWER on certain conditions.

PCR INTERPRETATION:
  PCR > 1.2 = put writing dominant = BULLISH (support expected)
  PCR 0.8-1.2 = NEUTRAL
  PCR < 0.8 = call writing dominant = BEARISH (resistance expected)

VETO CONDITIONS (output these clearly for Agent 7 to enforce):
  PCR < 0.75 → flag ce_blocked: true
  PCR > 1.40 → flag pe_blocked: true
  Price > Max Pain + 1.5% AND DTE < 4 → flag ce_blocked: true
  Price < Max Pain - 1.5% AND DTE < 4 → flag pe_blocked: true
  IV Rank > 80 → flag iv_extreme: true (reduce size 50%)

DTE NOTE: DTE < 3 is NOT a block. Flag theta_warning: true only.
  Trader decides. System does not block based on DTE.

INPUT JSON keys: symbol, current_price, expiry_date, dte, pcr,
  max_pain, iv_rank, atm_ce_oi, atm_pe_oi, top_ce_strikes, top_pe_strikes

OUTPUT: Valid JSON only.
{  "oi_bias": "BULLISH"|"BEARISH"|"NEUTRAL",
   "confidence": integer 40-85,
   "pcr": float, "pcr_interpretation": "string",
   "max_pain": float, "max_pain_distance_pct": float,
   "iv_rank": float, "iv_label": "LOW"|"NORMAL"|"HIGH",
   "theta_warning": bool,
   "ce_blocked": bool, "pe_blocked": bool, "iv_extreme": bool,
   "key_resistance_from_oi": float, "key_support_from_oi": float,
   "reasoning": "2-3 sentences on institutional positioning" }

5.4 Agent 4 — News + FII Agent
SYSTEM PROMPT — agents/agent_news.py
You are Agent 4 (News & FII Analyst) in TradeDesk.
Your job: search for and interpret today's market-moving news.
HIGH IMPACT news day = cap all agent confidence at 50%. Flag news_event_day: true.

SEARCH PRIORITY:
  1. RBI policy / rate decisions
  2. FII/DII net buy/sell (NSE published)
  3. US market previous close (S&P 500, Nasdaq, Dow)
  4. GIFT Nifty current value vs previous close
  5. India VIX level and direction
  6. Major corporate results affecting index heavyweights
  7. Geopolitical events affecting oil or dollar

IMPACT RULES:
  RBI rate hike surprise = BEARISH BankNifty, NEUTRAL Nifty
  FII buying > ₹2000 Cr = BULLISH. Selling < -₹2000 Cr = BEARISH
  US S&P500 up >0.5% = BULLISH gap-up probable
  US S&P500 down >0.5% = BEARISH gap-down probable
  VIX > 20 = elevated fear. VIX < 13 = low fear.
  Budget day / RBI policy day / Election results = HIGH IMPACT = flag news_event_day: true

RULES: Last 18 hours only. Rate each item HIGH/MEDIUM/LOW. No fabrication.

OUTPUT: Valid JSON only.
{  "overall_sentiment": "POSITIVE"|"NEGATIVE"|"NEUTRAL",
   "sentiment_strength": "STRONG"|"MODERATE"|"WEAK",
   "news_event_day": bool,
   "fii_action": "BUYING"|"SELLING"|"NEUTRAL"|"UNKNOWN",
   "fii_amount_cr": float or null,
   "gift_nifty_pts": float, "gift_nifty_direction": "UP"|"DOWN"|"FLAT",
   "us_market": "POSITIVE"|"NEGATIVE"|"MIXED",
   "india_vix": float, "vix_label": "LOW"|"NORMAL"|"HIGH"|"EXTREME",
   "key_news": [{"headline":"...","impact":"HIGH|MEDIUM|LOW","effect":"..."}],
   "three_things_to_watch": ["...", "...", "..."],
   "reasoning": "2-3 sentences overall market context today" }

5.5 Agent 5 — Chart Pattern Agent
SYSTEM PROMPT — agents/agent_chart.py
You are Agent 5 (Chart Pattern Analyst) in TradeDesk.
You also detect Behavior 4 (Post-Squeeze Breakout) for the pipeline engine.

PATTERNS (reliability order):
  HIGH: Bull Flag, Bear Flag, Double Top, Double Bottom, Breakout from consolidation
  MEDIUM: Head & Shoulders, Inverse H&S, Triangle, Wedge breakout
  LOW (mention but low weight): Hammer, Doji, Engulfing

BB SQUEEZE RULES:
  squeeze_duration >= 6: HIGH PRIORITY
  squeeze_duration >= 8: flag potential Behavior 4
  squeeze_duration >= 12: MAXIMUM PRIORITY
  Squeeze breakout overrides all other pattern signals.

RULES: No clear pattern = NO_CLEAR_PATTERN. Never force a pattern.

INPUT JSON keys: symbol, timeframe, ohlcv_last_20, detected_patterns,
  bb_squeeze_duration, bb_expanding, current_price, volume_ratio_last_candle

OUTPUT: Valid JSON only.
{  "primary_pattern": "PATTERN_NAME"|"NO_CLEAR_PATTERN",
   "pattern_reliability": "HIGH"|"MEDIUM"|"LOW",
   "breakout_level": float or null,
   "measured_target": float or null,
   "squeeze_alert": bool,
   "squeeze_duration": integer,
   "squeeze_priority": "NONE"|"HIGH"|"BEHAVIOR4_CANDIDATE"|"MAXIMUM",
   "price_action_bias": "BULLISH"|"BEARISH"|"NEUTRAL",
   "reasoning": "2-3 sentences on what the chart is telling you" }

5.6 Agent 6 — Journal Agent
SYSTEM PROMPT — agents/agent_journal.py
You are Agent 6 (Journal & Behavioral Analyst) in TradeDesk.
Your job: analyze trade history and identify patterns in what works and what fails.

COLD START (trade_count < 20):
  Use beginner default profile:
  - Beginners over-trade afternoons (1PM-3:30PM)
  - Beginners chase breakouts instead of waiting
  - Beginners move stop losses when trade goes against them
  - Beginners ignore AI verdict and trade on gut feel
  Flag these as WATCH FOR items.

WHEN SUFFICIENT DATA EXISTS (trade_count >= 20):
  Calculate win rates by: session, market mode, VIX range, day of week.
  Identify personal best setup combination.
  Flag specific behavioral violations from recent trades.

PER-BEHAVIOR ANALYSIS (when >= 30 trades for any behavior):
  Calculate SEPARATELY for each behavior:
  - Win rate, average R:R, best session, best regime, Kelly status
  NEVER aggregate behaviors into one win rate.
  'Your Liquidity Sweep win rate is 61% but Opening Fake is 43%' = correct
  'Your overall win rate is 54%' = useless

DAILY REPORT (called 3:35 PM): Today's trades only. Name them. Be specific.
WEEKLY REPORT (called Sunday 9 AM): Last 14 days. ONE specific rule for next week.

RULES: Honest and direct. Never praise if not deserved. Tie to specific trade data.

OUTPUT: Valid JSON only.
{  "win_rate_overall": float or null,
   "best_setup": "description" or "insufficient data",
   "worst_pattern": "description" or "insufficient data",
   "behavioral_flags": ["specific flag from recent trades"],
   "behavior_breakdown": {
     "OPENING_FAKE":          {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"},
     "INSTITUTIONAL_DEFENSE": {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"},
     "LIQUIDITY_SWEEP":       {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"},
     "SQUEEZE_BREAKOUT":      {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"},
     "COMBO_CUSTOM":          {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"}
   },
   "weakest_behavior": "name or null",
   "strongest_behavior": "name or null",
   "weekly_rule": "one specific rule to follow" or null,
   "recommendation": "which behavior to focus on this week",
   "report": "plain English full report text" }

5.7 Agent 7 — Pipeline Orchestrator + Risk
SYSTEM PROMPT — agents/agent_orchestrator.py
You are Agent 7 (Pipeline Orchestrator + Risk) in TradeDesk.
You interpret a structured pipeline result — you do NOT average 6 opinions.
Your output is what the trader sees and acts on.

WHAT YOU RECEIVE:
  pipeline_result: {
    behavior_fired, behavior_direction, behavior_confidence,
    indicator_confirmation_score (0-5),
    options_gate_result (VETO|SUPPORTED|NEUTRAL),
    options_gate_reason,
    regime_permitted (bool), regime_reason,
    timing_permitted (bool), timing_reason,
    pipeline_score (0-100),
    position_size_rupees, position_size_method,
    conflict_log (list)
  }
  agent_1_json through agent_6_json
  current_positions, daily_pnl_rupees, capital, lot_sizes

JOB 1 — HARD RISK VALIDATION (always first):
  options_gate = VETO → AVOID always
  behavior_fired = null AND no combo signal → WAIT
  timing_permitted = false → WAIT
  regime_permitted = false → WAIT
  current_positions >= 3 → AVOID
  daily_pnl_rupees < -(capital × 0.10) → AVOID
  agent_4.news_event_day = true → cap confidence at 50%, add to risk_flags
  DTE < 3 → add theta_warning to risk_flags. NOT a block.

JOB 2 — SYNTHESIS (only if risk passes AND pipeline_score >= 70):
  Produce BUY CE or BUY PE verdict.
  Use agent 1-6 context to enrich WHY explanation.
  Position size = pipeline_result.position_size_rupees. Do not override.

  If pipeline_score 50-69 → WAIT. Explain exactly what must change for 70.
  If pipeline_score < 50 → WAIT. Setup not formed.

STRIKE SELECTION (recommendation only — not hard block):
  DTE > 15: OTM acceptable    DTE 8-15: ATM preferred
  DTE 3-7: ATM or 1-ITM       DTE 1-2: 1-ITM or 2-ITM
  DTE = 0: ITM only (intrinsic value only)
  VIX < 13: ATM or 1-OTM      VIX 13-18: ATM only
  VIX > 18: 1-ITM preferred   Signal score < 14: flag weak signal
  Target delta 0.40-0.55. Flag delta < 0.25 or > 0.80.

EXIT PLAN (always include in output):
  Phase 1: Fixed structural SL → Target 1
  Phase 2: At T1 exit 50%, move SL to breakeven
  Phase 3: Trail 50% with 1.5x ATR(14, 15-min). High VIX: 2.0x ATR.
  Hard exit: 3:10 PM always. No exceptions.

WHY SECTION: Plain English. 4-8 sentences for a beginner. No jargon.
Always explain: which behavior fired, what indicators say,
what options chain says, what the risk is.
If WAIT: state the specific missing condition.

OUTPUT: Valid JSON only.
{  "verdict": "BUY CE"|"BUY PE"|"WAIT"|"AVOID",
   "behavior_triggered": "OPENING_FAKE"|"INSTITUTIONAL_DEFENSE"|
                         "LIQUIDITY_SWEEP"|"SQUEEZE_BREAKOUT"|null,
   "pipeline_score": integer,
   "theta_warning": bool,
   "instrument": "NIFTY"|"BANKNIFTY"|"FINNIFTY"|"SENSEX",
   "recommended_strike": float or null,
   "recommended_strike_type": "ATM"|"1-OTM"|"1-ITM"|"2-ITM"|null,
   "alternative_strikes": [{"strike":float,"type":"...","delta_approx":float,"premium_approx":float}],
   "entry_zone": {"low":float,"high":float},
   "stop_loss": float, "target_1": float, "target_2": float,
   "risk_reward": "1:X",
   "position_size_rupees": float,
   "position_size_method": "KELLY_ROLLING"|"FLOOR_INSUFFICIENT_DATA"|"KELLY_NEGATIVE_USE_FLOOR",
   "confidence": integer 40-85,
   "why": "plain English 4-8 sentences for a beginner",
   "what_to_watch": ["2-3 things that could invalidate this setup"],
   "risk_flags": ["any risk violations or warnings"],
   "conflict_log": ["any conflicting behaviors detected"],
   "avoid_reason": null | "specific reason" }

5.8 Agent 8 — SMC Orchestrator (Screen 2)
SYSTEM PROMPT — agents/agent_smc.py
You are Agent 8 (SMC Orchestrator) in TradeDesk. Screen 2 only.
Your job: interpret SMC engine output and produce a final SMC-based trade verdict.

SMC HIERARCHY YOU FOLLOW:
  1. HTF Structure (1-Day/1-Hour) — never trade against this
  2. Liquidity sweep confirmation — smart money grabs stops before real move
  3. CHoCH on 15-min — structure shift confirmed
  4. Price at OB or FVG in premium/discount alignment
  5. Kill zone active — morning or afternoon IST only

HARD RULES:
  Dead zone (11:30-13:00 IST) → WAIT always
  Opening Judas (9:15-9:45 IST) → WAIT always
  Expiry hunt (14:30-15:10 IST) → AVOID always
  Monday: Judas swing risk. Confidence -15%.
  Thursday: Nifty expiry — AVOID Nifty. FinNifty allowed.
  Wednesday: BNF expiry — exit BNF by 12:30. No new BNF buys.
  OB tested 3+ times → zone weakened. Flag ob_weakened: true.
  DTE < 3 → theta_warning: true. NOT a block.

WEEKLY PROFILE:
  Monday: Judas swing expected. Wait for reversal.
  Tuesday: Highest probability. Full confidence.
  Wednesday: BNF expiry caution.
  Thursday: Nifty expiry manipulation. Avoid Nifty.
  Friday: Reduce size. Exit all by 14:30.

INPUT JSON keys: htf_structure, ltf_structure, last_bos_choch,
  liquidity_sweep_detected, active_obs, active_fvgs,
  premium_discount_zone, current_kill_zone, day_of_week,
  smc_score, market_mode, open_positions_count,
  daily_pnl_rupees, lot_sizes, capital

OUTPUT: Valid JSON only.
{  "verdict": "BUY CE"|"BUY PE"|"WAIT"|"AVOID",
   "instrument": "NIFTY"|"BANKNIFTY"|"FINNIFTY"|"SENSEX",
   "smc_entry_type": "OB_RETEST"|"FVG_FILL"|"CHOCH_ENTRY"|"OTE",
   "entry_zone": {"low":float,"high":float},
   "stop_loss": float, "target_1": float, "target_2": float,
   "confidence": integer 40-85,
   "theta_warning": bool,
   "kill_zone": "MORNING"|"AFTERNOON"|"DEAD"|"JUDAS"|"EXPIRY_HUNT",
   "weekly_day_rating": "HIGH"|"MEDIUM"|"LOW"|"AVOID",
   "premium_discount": "EXTREME_PREMIUM"|"PREMIUM"|"EQ"|"DISCOUNT"|"EXTREME_DISCOUNT",
   "ob_weakened": bool,
   "why": "plain English 4-8 sentences for a beginner",
   "avoid_reason": null | "specific reason" }

 
6. Monitor Scoring Engines
6.1 Screen 1 — Alert Score (0-21) — Built Sprint 4
Alert Score: fires beep alerts. Always running. Built in Sprint 4 using scorer.py. Never changes after Sprint 4.

Condition	Points	Category	Notes
Price closed decisively above/below key S&R level	3	Price Action	Close must be > 0.2% beyond zone edge
Volume this candle > 2x 20-candle average	3	Volume	Confirms move is real
Supertrend flipped direction	3	Trend	Strong regime change
Bullish/bearish candlestick pattern detected	2	Pattern	Hammer, engulfing, morning star
OI shift in signal direction (15-min rolling, 3 candles)	2	OI	Not per-candle — 3 candle aggregate
RSI crossed above 40 from below	2	Momentum	Oversold recovery
MACD line crossed above/below signal line	2	Momentum	Confirmed cross only
EMA 20 above EMA 50	1	Trend	Short-term alignment
Price above/below VWAP	1	Price Action	Intraday reference
ADX > 25	1	Trend	Trend strength filter
EMA 50 above EMA 200	1	Trend	Higher TF alignment
BB squeeze breakout (6+ candles)	MAX ALERT	Special	Bypasses threshold — fires always
MAXIMUM SCORE	21		Note: BRD v4 said 22 — confirmed 21 in code

Score	Level	Sound	Action
0-9	SILENCE	None	Logged silently.
10-13	SOFT Yellow	1 beep	Alert card: something forming.
14-17	STRONG Orange	2 beeps	Alert card prominent: decent setup.
18-21	MAX Red	3 beeps	Full priority alert: multiple confirmations.

Cooldown: 30-minute per-instrument cooldown after STRONG or MAX alert. Prevents repeated alerts on same instrument. Resets daily.

VIX-Adjusted Alert Thresholds
•	VIX < 13: Soft=10, Strong=14. Normal market. Source: NSE_INDEX|India VIX via Upstox. Refresh every 5 min.
•	VIX 13-18: Soft=12, Strong=16. Normal fear range.
•	VIX > 18: Soft=16, Strong=20. Elevated fear — need more confirmation.
•	Fallback: If VIX unavailable → use VIX 13-18 (NORMAL) thresholds.

6.2 Screen 1 — Pipeline Signal Score (0-100) — Built Sprint 6
Signal Score: gates the Analyze button. Built in Sprint 6. behavior_scorer.py. Analyze button enabled when Signal Score >= 70.

Condition	Points	Notes
Behavior fired (any of 4)	40	Primary signal. Without behavior firing, max score is 60.
Indicator confirmation 5/5	20	All 5 indicators aligned with behavior direction
Indicator confirmation 4/5	16	
Indicator confirmation 3/5	12	Minimum to proceed at full size
Options chain SUPPORTED	15	OI bias + PCR confirm direction
Options chain NEUTRAL	8	No structural view either way
Regime correct for this behavior	15	Behavior permitted in current market mode
Kill zone optimal	10	Morning or afternoon kill zone active
Same-direction behavior bonus	+10	Two behaviors agree on direction
Behavior conflict penalty	-10	Two behaviors disagree on direction
Combo same direction as behavior	+5	1140 combo discovery agrees
Combo opposite to behavior	-5	1140 combo disagrees

Score	Level	Analyze Button	Action
0-49	NOISE — Grey	Disabled	Nothing. Logged silently.
50-69	WATCH — Yellow	Disabled	Score shown. Setup forming.
70-84	SIGNAL — Orange	ENABLED	User decides when to click.
85-100	STRONG — Red pulsing	ENABLED + pulsing	High priority. Agent 7 strongly recommended.

Combo alone: max pipeline score 65. Combos cannot trigger Analyze alone. A core behavior must be present to reach 70 threshold.

6.3 Screen 2 — SMC Score (0-100) — Built Sprint 9
Condition	Points	Notes
HTF structure aligned (1-Day)	20	Highest weight. Never trade against Daily.
Price in discount (CE) or premium (PE) zone	15	Non-negotiable for high-probability SMC entry.
CHoCH confirmed on 15-min	15	Structure shift — institutional intent confirmed.
Price at active OB (touch count < 3)	15	Weakened at 3+ touches.
Liquidity sweep detected before setup	10	Stop hunt before real move.
Active FVG as profit target	10	FVG fill probability ~65-80%.
Current time in kill zone	10	Dead zone = 0 points.
Weekly profile supports	5	Tuesday = full. Others = reduced.
MAXIMUM	100	

 
7. Market Mode Detection
Mode	Detection	Strategy	Size	Hysteresis
TRENDING	ADX > 25 AND Supertrend aligned AND day range > 80% daily ATR(14)	Breakout, momentum, trend continuation	100%	3 candles
MODERATE RANGE	ADX 15-25 AND day range 50-80% daily ATR(14)	CE at support, PE at resistance. No chasing.	75%	3 candles
TIGHT RANGE/SQUEEZE	ADX < 15 AND BB width < 20th percentile (rolling 100 candles)	WAIT. Monitor for squeeze breakout only.	0%	3 candles

•	Hysteresis: 3 consecutive candles must satisfy new mode before switching. Prevents whipsaw.
•	Day range: today's high minus today's low since 9:15 AM. Updated every candle.
•	ATR reference: 14-period ATR on DAILY from previous close. Not distorted by intraday range.
•	Market mode passed as explicit input to Agent 7 and Agent 8.
•	Mode priority: Tight Range = 0% position regardless of any other score.

 
8. Multi-Timeframe Confluence
Timeframe	Role	Weight	Bias Definition (Exact)
1-Day	General	4	BULLISH: price>EMA50 AND Supertrend green AND close>VWAP daily. BEARISH: opposite. NEUTRAL: mixed.
1-Hour	Colonel	3	BULLISH: price>EMA50 AND Supertrend green AND last 3 closes above VWAP. BEARISH: opposite.
15-Min	Captain	2	BULLISH: price>EMA20 AND Supertrend green AND MACD above signal. BEARISH: opposite.
5-Min	Soldier	1	BULLISH: last candle close>open AND price>VWAP AND volume>1.2x avg. BEARISH: opposite.

Alignment	Label	Position Size	Condition
4/4	STRONG	100%	All four timeframes aligned
3/4	MODERATE	75%	Any 3 aligned
2/4 (Daily+1Hr)	WEAK-LEARNING	25%	Only if Daily AND 1-Hour are the two aligned
2/4 (other pairs)	WAIT	0%	15min+5min = WAIT
1/4 or 0/4	IGNORE	0%	Counter-trend. High failure rate.

 
9. Strike Selection & Exit Strategy
9.1 Strike Selection — DTE Guidance (Recommendations Only — NOT Blocks)
DTE restrictions are REMOVED. The only hard exit is 3:10 PM. DTE < 3 = WARNING flag only, not a block. Trader decides.

DTE	Recommended Strike	Reasoning
DTE > 15	OTM acceptable	Enough time for move to develop
DTE 8-15	ATM preferred	Balance of delta and time
DTE 3-7	ATM or 1-ITM	Theta accelerating — need delta
DTE 1-2	1-ITM or 2-ITM preferred	Maximum intrinsic, minimum theta
DTE = 0 (expiry day)	ITM only	OTM expires worthless — intrinsic value only

Factor	Rule
VIX < 13	ATM or 1-OTM
VIX 13-18	ATM only
VIX > 18	1-ITM preferred
Signal Score 18+	ATM (max delta)
Signal Score 14-17	1-OTM
Signal Score < 14	Flag weak signal — reconsider
Target delta	0.40-0.55. Flag delta < 0.25 or > 0.80.

System shows top 3 alternative strikes with estimated premium, delta, and capital required. Trader picks. Manual deviation logged in journal as 'deviated from AI strike.'

9.2 Three-Phase Exit Strategy
•	PHASE 1 (Entry → T1): Fixed structural SL. Exit if SL hit OR T1 reached.
•	PHASE 2 (At T1): Exit 50%. Move SL to breakeven. Trade now risk-free on remaining half.
•	PHASE 3 (T1 → T2): Trail remaining 50% with 1.5x ATR(14, 15-min). High VIX: use 2.0x ATR. Exit at T2 or 3:10 PM or trail SL hit.

HARD EXIT: 3:10 PM always. No exceptions. No DTE-based exits. No expiry week overrides. You decide to hold or exit based on what you see.

 
10. Gap Open Handling Protocol
Gap Type	Trigger vs Daily ATR14	Wait	Protocol
NO GAP	< 30% ATR	None	Normal. S&R levels valid.
SMALL GAP	30-70% ATR	15 min	Caution. Threshold +2. Recalc nearest S&R. Size 100%.
MEDIUM GAP	70-120% ATR	30 min	Show Fill vs Continue scenarios. Size 75%.
LARGE GAP	> 120% ATR	45 min	Near-freeze. Min score 18+. Size 50%. All S&R recalculated.

•	ATR reference: Daily ATR(14) from previous trading day close.
•	Gap fill level (previous close) added as Very High priority S&R zone for the day.
•	After wait: re-run strike selector with current live IV. Post-gap premiums are inflated.
•	Morning briefing at 8:45 AM predicts gap using GIFT Nifty vs previous close.

 
11. Risk Management
11.1 Hard Limits — Screen 1 (Agent 7 enforces)
•	Maximum 3 open positions at any time (combined both screens).
•	Never hold any option past 3:10 PM. Exit at market price.
•	DTE < 3: WARNING flag only. NOT a block. Trader decides.
•	VIX > 20: reduce position size 50% automatically.
•	Minimum risk:reward 1:1.5 for any recommended trade.
•	Daily loss > 10% of capital: STOP TRADING TODAY. Both screens suppressed.
•	Paper trading: 8 weeks OR 100 trades per screen before real money.
•	Kill criterion per behavior: win rate < 48% after 30+ signals → WATCH → RETIRED.

11.2 Hard Limits — Screen 2 (Agent 8 enforces)
•	Dead zone (11:30-13:00 IST): WAIT always.
•	Opening Judas (9:15-9:45 IST): WAIT always.
•	Expiry hunt (14:30-15:10 IST): AVOID always.
•	OB tested 3+ times: flag weakened, do not use as primary entry.
•	Monday: Judas swing risk flag mandatory. Confidence -15%.
•	SMC score < 70: Analyze button disabled.

11.3 Human Override — 5-Minute Pending Window
•	Every Agent 7/8 verdict → status = PENDING for 5 minutes.
•	ACCEPT (or auto after 5 min): included in Kelly and win rate calculation.
•	NOT_TAKEN: excluded from Kelly entirely. Reason required. Ghost trade — never affects stats.
•	MODIFY_SIZE: included in Kelly at actual size, logged as MANUAL_OVERRIDE.

11.4 Drawdown Management
•	Weekly and monthly P&L shown prominently. You decide when to pause.
•	System pause: review BRD agent prompts and scoring thresholds before resuming.

 
12. Strategy Engine — Behavior-Based Pipeline
The strategy engine is built on price behavior observations — not indicator combinations. Indicators confirm signals. Options chain gates them. Regime permits them. Kelly sizes them.

12.1 The 4 Core Behaviors
Behavior 1 — Opening Fake (Judas Swing)
Field	Detail
Observation	BankNifty/Nifty moves in one direction in the first 15-30 minutes then reverses. Opening direction is manufactured to trap retail traders.
Why it exists	FII/institutional orders flood in at open. Market makers push price against retail positioning to build inventory cheaply.
Signal direction	Opposite to opening 9:15-9:20 candle direction.
Detection rules	ALL must be true: (1) First candle direction identified. (2) body_ratio = abs(open-close)/(high-low) >= 0.20 — else SKIP (doji). (3) Price moves against first candle by 9:45. (4) 9:45 candle closes across VWAP in new direction. (5) Volume on reversal candle > 1.5x average.
Doji rule	body_ratio < 0.20 → Behavior 1 cannot fire this session. body_ratio 0.20-0.40 (spinning top) → raise indicator confirmation threshold to 4/5.
Regime	TRENDING + MODERATE_RANGE only. BLOCKED in TIGHT_RANGE.
Timing	Valid 9:45-10:30 only. Monday: confidence -15%.
File	analysis/behavior_detector.py — detect_opening_fake()

Behavior 2 — Institutional Level Defense
Field	Detail
Observation	Price gets pushed away from heavy OI strikes. Defense strongest near expiry. Max Pain is the weighted center of this defense.
Why it exists	Option sellers delta-hedge the underlying to keep price away from their short strikes. Mechanical — not discretionary.
Signal direction	Away from the heavy OI strike. Price above Max Pain → PE signal. Below Max Pain → CE signal.
Detection rules	ALL must be true: (1) Price above Max Pain by > 0.3% OR below by > 0.3%. (2) PCR < 0.85 for resistance (call writing) OR PCR > 1.30 for support (put writing). (3) Heavy OI strike within 0.5% of price (CE or PE OI > 3x average strike OI). (4) Options gate not vetoing direction.
DTE note	No DTE restrictions. Behavior 2 fires at ANY DTE when conditions met. DTE < 3 = theta_warning flag only.
Regime	ALL regimes permitted. Options mechanics work regardless of trend.
Timing	Valid 10:00-14:30. Thursday: Nifty BLOCKED. Wednesday: BNF exit by 12:30.
File	analysis/behavior_detector.py — detect_institutional_defense()

Behavior 3 — Liquidity Sweep Reversal
Field	Detail
Observation	Price sweeps above Equal Highs (or below Equal Lows) where retail stops cluster, then reverses sharply.
Why it exists	Large players need liquidity to fill large orders. Retail stops above EQH are buy orders. Institutions push price up, trigger stops, fill their own sell orders at better prices, then let price fall. Pure market microstructure.
Signal direction	Opposite to sweep direction. Sweep above EQH → PE. Sweep below EQL → CE.
EQH/EQL detection	Separate file: eqh_eql_detector.py. Tolerance 0.02%. Lookback 20 bars. Minimum 2 touches to qualify. Separate from sr_detector.py — different concept.
Detection rules	ALL must be true: (1) EQH or EQL identified. (2) Price pierces level by >= 0.10% (actual sweep, not just touch). (3) Next candle closes back below EQH or above EQL (rejection confirmed). (4) Volume on sweep candle > 2.0x average. (5) Sweep occurs in kill zone (not dead zone).
Regime	TRENDING + MODERATE_RANGE. BLOCKED in TIGHT_RANGE.
Timing	Morning kill zone (9:45-11:30) and afternoon kill zone (13:00-14:30) only.
File	analysis/behavior_detector.py — detect_liquidity_sweep() + analysis/eqh_eql_detector.py

Behavior 4 — Post-Squeeze Breakout
Field	Detail
Observation	When BB width compresses 8+ candles (squeeze), the energy release produces a 1.5-2x normal range directional move.
Why it exists	Option sellers sell straddles during low volatility. When a catalyst arrives, all sellers hedge simultaneously, accelerating the breakout. The squeeze = institutional accumulation period.
Signal direction	Direction of breakout candle confirmed by Supertrend flip in same candle.
Detection rules	ALL must be true: (1) BB width in bottom 10th percentile of rolling 100 candles. (2) Squeeze lasted >= 8 consecutive candles. (3) Breakout candle range > 1.5x ATR(14). (4) Volume > 2.5x average on breakout candle. (5) Supertrend flips direction in breakout candle.
Special rule	Behavior 4 BYPASSES Layer 2 (indicator confirmation) when all 5 conditions met. Self-confirming. Proceeds directly to Layer 3.
Regime	TIGHT_RANGE only. Squeeze by definition exists in tight range.
Timing	Valid any time 9:15-14:50. Last 20 min blocked.
File	analysis/behavior_detector.py — detect_squeeze_breakout()

12.2 The Pipeline — 6 Layers
Layer	Name	Job	Blocks trade?
1	Core Behavior Detector	Detects if any of 4 behaviors fired	Yes — no behavior = no Analyze signal
2	Indicator Confirmation	0-5 score. Confirms behavior direction.	Yes — score < 2 = skip. B4 bypasses this layer.
3	Options Chain Gate	Hard veto on specific PCR/Max Pain conditions	Yes — VETO = AVOID regardless of layers 1-2
4	Market Regime	Checks if regime permits this specific behavior	Yes — wrong regime = no trade
5	Kill Zone / Timing	Time of day + day of week filter	Yes — dead zone/Judas/expiry hunt = no trade
6	Position Sizer	Kelly calculation. Outputs position_size_rupees.	No — sizes the trade, does not block it

12.3 Indicator Confirmation Scoring (Layer 2)
Condition	CE Score	PE Score
Supertrend green	+ 1	
Supertrend red		+ 1
Price above VWAP	+ 1	
Price below VWAP		+ 1
EMA 20 above EMA 50	+ 1	
EMA 20 below EMA 50		+ 1
MACD histogram positive	+ 1	
MACD histogram negative		+ 1
RSI above 50	+ 1	
RSI below 50		+ 1
Score >= 3/5	CONFIRMED — full size	CONFIRMED — full size
Score = 2/5	WEAK — reduce size 50%	WEAK — reduce size 50%
Score <= 1/5	SKIP trade	SKIP trade

12.4 Options Chain Gate (Layer 3) — Veto Conditions
Condition	Direction Blocked	Reason
PCR < 0.75	CE blocked	Heavy call writing = institutional resistance above
PCR > 1.40	PE blocked	Heavy put writing = institutional support below
Price > Max Pain + 1.5% AND DTE < 4	CE blocked	Max Pain gravity pull too strong
Price < Max Pain - 1.5% AND DTE < 4	PE blocked	Max Pain gravity pull too strong
IV Rank > 80	Both: reduce size 50%	Options overpriced — IV crush risk

12.5 Behavior Regime Permissions (Layer 4)
Behavior	TRENDING	MODERATE_RANGE	TIGHT_RANGE
Opening Fake	PERMITTED	PERMITTED	BLOCKED
Institutional Defense	PERMITTED	PERMITTED	PERMITTED
Liquidity Sweep	PERMITTED	PERMITTED	BLOCKED
Squeeze Breakout	BLOCKED	BLOCKED	PERMITTED

12.6 Kill Zone Timing (Layer 5)
Zone	Time IST	Action	Behaviors Valid
Opening Judas	9:15-9:45	WAIT always	None
Morning Kill Zone	9:45-11:30	TRADE	B1(9:45-10:30), B2, B3, B4
Dead Zone	11:30-13:00	WAIT always	None
Afternoon Kill Zone	13:00-14:30	TRADE	B2, B3, B4
Expiry Hunt	14:30-15:10	AVOID always	None
Close	15:10-15:30	EXIT ONLY	Hard exit 3:10 PM

12.7 Two Behaviors Conflict Resolution
Priority	Behavior	Reason
1 — Highest	Behavior 4: Squeeze Breakout	5 conditions + Supertrend flip. Most reliable.
2	Behavior 2: Institutional Defense	Hard options data. Independent of price movement.
3	Behavior 3: Liquidity Sweep	Price structure evidence with volume.
4 — Lowest	Behavior 1: Opening Fake	Time-dependent. Softer conditions.

•	Two behaviors same direction: +10 pipeline score bonus. Both logged.
•	Two behaviors conflict: higher priority wins. -10 pipeline score penalty. Lower logged in conflict_log.
•	Two majority agree, one conflicts: majority direction proceeds. -5 pipeline score penalty.

12.8 Position Sizing — Rolling Kelly
Rolling Kelly from first trade. No phase gates. Per-behavior independent sizing.

Behavior history	Window used	Typical output
< 10 trades	Insufficient — floor position (2%)	₹2,000 on ₹1L capital
10-99 trades	Last 20 trades	4-8% typically
100+ trades	Last 50 trades	5-10% typically

CORRECT KELLY FORMULA:
  b = avg_win_pct / avg_loss_pct  (win-to-loss ratio)
  f* = (p × b - q) / b
  where: p = win_rate, q = 1 - p

Quarter Kelly: f_quarter = f* × 0.25
Hard caps: max(min(f_quarter, 0.10), 0.02)

WORKED EXAMPLE (good results):
  p=0.56, avg_win=9%, avg_loss=6%
  b = 0.09/0.06 = 1.5
  f* = (0.56×1.5 - 0.44)/1.5 = 0.267 → 26.7%
  Quarter Kelly = 6.7% → ₹6,700 on ₹1L capital

WORKED EXAMPLE (no edge):
  p=0.45, avg_win=7%, avg_loss=8%
  b = 0.875
  f* = (0.45×0.875 - 0.55)/0.875 = -0.178 → NEGATIVE
  Action: floor position ₹2,000. Log WARNING.

NOT_TAKEN trades: excluded from Kelly entirely.
MANUAL_OVERRIDE trades: included at actual size, tracked separately.

12.9 The 1,140 Combination Discovery Layer
•	C(20,3) = 1,140 indicator combinations. Runs post-market 3:45 PM.
•	Minimum 150 signals in 2-year backtest to be considered valid. Below 150 = statistically unreliable.
•	Walk-forward 40-day held-out validation. Profit Factor > 1.5 required.
•	Transaction cost: ₹200/trade (₹40 brokerage + ₹160 slippage estimate).
•	Combo same direction as behavior: +5 pipeline score. Opposite: -5.
•	Combo alone: max pipeline score 65. Cannot reach Analyze threshold (70) without behavior.
•	Promotion rule: if a combo beats all 4 behaviors for 3 consecutive months → promoted to Behavior 5.
•	Computation time: 8-15 seconds with numpy vectorization.

12.10 Custom Strategy Builder
•	UI builder (simple): dropdown conditions in Settings panel. No coding needed.
•	JSON config (advanced): config/custom_strategies/ folder. Full flexibility.
•	Both backtested through same engine. Same PF > 1.5 threshold. Same walk-forward.
•	Custom strategies appear in ranking alongside core behaviors and combos.
•	Maximum 20 custom strategies stored.

 
13. Database Schema (market_data.db)
13.1 All 13 Tables
Table	Key Columns	Purpose
candles	symbol, timeframe, timestamp, OHLCV	All 6 timeframes. ~360k rows/year. 3-year retention.
indicators	symbol, timeframe, timestamp + all values	Pre-computed. Loaded on startup.
support_resistance	symbol, price_low/mid/high, strength, touches	S&R zones. Auto-updated daily.
options_chain	symbol, expiry, strike, CE/PE OI/LTP/IV/Greeks	Full chain. Snapshot every 2 min. 90-day prune.
trades	23 base fields + 4 behavior fields	Screen 1 journal. behavior_triggered, pipeline_score, position_size_rupees, position_size_method added.
alerts	score, conditions, outcome	Post-market Screen 1 alert quality analysis.
news_events	headline, predicted impact, actual move	Historical news → impact learning.
strategies	conditions JSON, backtest results, win rate	All strategy backtest results (behaviors + combos + custom).
lot_size_history	instrument, lot_size, effective_from, effective_to	Historical lot sizes for accurate backtest P&L.
smc_structure	instrument, timeframe, event_type, price_level, timestamp, broken	Every BOS/CHoCH event.
smc_zones	instrument, zone_type, zone_subtype, top, bottom, midpoint, tested_count, active	All OBs, FVGs, liquidity levels.
smc_trades	31 data points (23 base + 8 SMC-specific)	Screen 2 journal. Separate from Screen 1.
smc_accuracy	concept, instrument, total_signals, correct, accuracy_pct	Per-concept SMC accuracy dashboard.

13.2 Trades Table — 4 New Fields (Added in Update Prompts)
Field	Type	Purpose	Default
behavior_triggered	TEXT	Which of 4 behaviors fired. NULL = combo or custom.	NULL
pipeline_score	INTEGER	0-100 pipeline score at signal time.	NULL
position_size_rupees	REAL	Actual rupees risked. From Kelly.	NULL
position_size_method	TEXT	KELLY_ROLLING | FLOOR_INSUFFICIENT_DATA | etc.	NULL

 
14. Complete File Structure
14.1 Config & Core
File	Purpose	Sprint
main.py	Entry point.	7
requirements.txt	All packages. smart-money-concepts included.	1
.env	API keys. Never committed to git.	1
config/config.json	Capital, thresholds, SMC config, strategy builder, paper trading.	1
config/instruments.json	Instrument list. Add via Settings panel.	1
config/holidays.json	2026 Indian market holidays.	1
config/behavior_thresholds.json	All 4 behavior thresholds + pipeline scoring + Kelly config.	U5
config/smc_thresholds.json	SMC-specific thresholds.	U5
config/custom_strategies/	Folder for user JSON strategy files.	6
data/market_data.db	SQLite. 13 tables.	1
core/local_db.py	All SQLite read/write. 13 table schemas. Migration code.	1+U3
core/candle_builder.py	Tick → OHLCV all 6 timeframes.	1
core/startup.py	6-phase startup. SMC init in Phase 2.	1+U4
core/token_manager.py	Upstox daily token refresh.	3
core/data_provider.py	Adapter. One-line Upstox↔Kite switch.	3

14.2 Feeds
File	Purpose	Sprint
feeds/upstox_stream.py	WebSocket live ticks. MarketDataStreamerV3.	3
feeds/upstox_rest.py	Historical OHLCV fetch + gap fill.	1
feeds/kite_rest.py	Kite fallback REST.	3
feeds/kite_stream.py	Kite fallback WebSocket.	3
feeds/news_fetcher.py	NewsAPI headlines.	3
feeds/fii_fetcher.py	NSE FII/DII scraper.	3
feeds/global_fetcher.py	GIFT Nifty, US markets, India VIX.	3

14.3 Analysis — Screen 1
File	Purpose	Sprint
analysis/indicators.py	pandas-ta wrapper. All indicators.	2
analysis/sr_detector.py	Swing detection + S&R clustering.	2
analysis/pattern_detector.py	Candlestick + chart patterns.	2
analysis/market_mode.py	ADX + BB mode detection. Hysteresis.	2
analysis/confluence.py	4-TF weighted bias scoring.	2
analysis/scorer.py	0-21 Alert Score engine.	2
analysis/gap_handler.py	Gap classification + protocol.	8
analysis/strike_selector.py	DTE/VIX/delta strike selection.	8

14.4 Analysis — Behavior Engine (Screen 1)
File	Purpose	Sprint
analysis/behavior_detector.py	4 behavior detection functions.	6
analysis/eqh_eql_detector.py	Equal Highs/Lows detection. Separate from sr_detector.	6
analysis/behavior_scorer.py	0-100 Pipeline Signal Score.	6
analysis/pipeline.py	6-layer pipeline orchestrator.	6
analysis/position_sizer.py	Rolling Kelly calculator.	6
strategy/behavior_library.py	Behavior definitions from behavior_thresholds.json.	6
backtest/behavior_backtest_engine.py	Backtests all 4 behaviors on 2-year historical data.	6

14.5 Analysis — Screen 2 SMC
File	Purpose	Sprint
analysis/smc_engine.py	Core SMC detection. Uses smart-money-concepts library.	9
analysis/smc_scorer.py	0-100 SMC score engine.	9
analysis/smc_confluence.py	SMC multi-factor confluence.	9
analysis/smc_kill_zones.py	Indian kill zone detection + weekly profile.	9
analysis/smc_zones_manager.py	Zone lifecycle: fill%, tested_count, broken status.	9

14.6 Monitor
File	Purpose	Sprint
monitor/monitor.py	Main monitor loop 9:15-3:10. Alert Score based.	4
monitor/alert_engine.py	Alert card generation + winsound beeps.	4
monitor/expiry_manager.py	DTE tracking + theta warnings.	4

14.7 Agents
File	Purpose	Sprint
agents/agent_base.py	Claude API wrapper. Rate limiting. Prompt caching. Error handling.	5
agents/agent_indicator.py	Agent 1.	5
agents/agent_confluence.py	Agent 2.	5
agents/agent_options.py	Agent 3.	5
agents/agent_news.py	Agent 4.	5
agents/agent_chart.py	Agent 5.	5
agents/agent_journal.py	Agent 6.	5
agents/agent_orchestrator.py	Agent 7 — Pipeline Orchestrator.	5
agents/agent_smc.py	Agent 8 — SMC Orchestrator.	10

14.8 Backtest, Journal, Strategy
File	Purpose	Sprint
backtest/backtest_engine.py	1140 combo testing. ₹200/trade cost. 150-signal threshold.	6
backtest/option_pricer.py	Phase 1: spot proxy. Phase 2: Black-Scholes. Phase 3: Bhavcopy.	6
backtest/strategy_library.py	5 predefined strategies.	6
backtest/strategy_ranker.py	win_rate×0.4 + expectancy×0.3 + recency×0.3. PF > 1.5.	6
backtest/smc_backtest_engine.py	SMC setup backtesting.	10
backtest/smc_strategy_ranker.py	SMC setup ranking by concept accuracy.	10
journal/trade_logger.py	23+4 data points. Pending window. NOT_TAKEN/MODIFY_SIZE.	6
journal/daily_reporter.py	3:35 PM auto daily report via Agent 6.	6
journal/weekly_reviewer.py	Sunday 9 AM weekly report via Agent 6.	6
journal/smc_trade_logger.py	31 data point SMC journal.	10
journal/smc_daily_reporter.py	SMC daily report via Agent 8.	10
journal/smc_weekly_reviewer.py	SMC weekly report via Agent 8.	10
journal/auto_paper_s1.py	Screen 1 auto paper trading engine.	6
journal/auto_paper_s2.py	Screen 2 auto paper trading engine.	9
strategy/strategy_builder.py	Custom strategy UI + JSON config engine.	6
strategy/strategy_config_loader.py	JSON config file parser.	6
strategy/strategy_library_custom.py	User-defined strategy storage.	6

14.9 UI
File	Purpose	Sprint
ui/app.py	Tkinter + ttkbootstrap. 4-thread. Queue. poll_queue().	7
ui/panels/chart_panel.py	mplfinance embedded. S&R + signal overlays.	7
ui/panels/monitor_panel.py	Alert cards. Alert Score display. Cooldown indicator.	7
ui/panels/analysis_panel.py	8 agent outputs. Alert Score + Signal Score. WHY text.	7
ui/panels/options_panel.py	Options chain. PCR. Max Pain. OI bars.	7
ui/panels/journal_panel.py	Trade log. Pending window. NOT_TAKEN/MODIFY buttons.	7
ui/panels/briefing_panel.py	Morning briefing display.	7
ui/panels/settings_panel.py	6-tab config. Instruments. Thresholds. Behaviors tab.	7
ui/panels/paper_trading_panel.py	Both screens paper trading dashboard.	7
ui/panels/strategy_builder_panel.py	UI for custom strategy builder.	11
ui/panels/smc_chart_panel.py	Screen 2 chart. OB boxes, FVG zones, BOS/CHoCH, kill zones.	11
ui/panels/smc_dashboard_panel.py	Screen 2 live SMC data.	11
ui/panels/smc_signal_panel.py	SMC score meter. Analyze button. Agent 8 verdict.	11
ui/panels/smc_accuracy_panel.py	Per-concept SMC accuracy dashboard.	11
ui/panels/smc_journal_panel.py	Screen 2 journal. 31 data points.	11
ui/assets/sounds/*.wav	soft_alert, strong_alert, max_alert.	7

14.10 Tests & Logs
File	Purpose	Sprint
tests/test_sprint1.py	Sprint 1 tests. 56 base + 8 SMC + 6 behavior = 70 after updates.	1+U6
tests/test_sprint2.py	Sprint 2 tests. 45 tests. UNCHANGED.	2
tests/test_sprint3.py	Sprint 3 tests. 69 tests. UNCHANGED.	3
tests/test_sprint4.py	Monitor tests.	4
tests/test_sprint5.py	Agent tests.	5
tests/test_behavior_engine.py	68 minimum tests for behavior engine.	6
tests/test_sprint6.py	Backtest + journal + strategy tests.	6
tests/test_sprint9.py	SMC engine tests.	9
market_simulator.py	Historical tick replay for offline testing.	1
logs/trades.json	Screen 1 trade journal export.	—
logs/smc_trades.json	Screen 2 SMC trade journal export.	—
logs/daily_reports/	Screen 1 daily reports.	—
logs/smc_daily_reports/	Screen 2 daily reports.	—
logs/app.log	General application logs.	—
logs/monitor.log	Screen 1 alert history.	—

 
15. Startup Sequence
Phase	Time	What Happens
0	0-2s	Config load, dependency check, folders + DB creation. Holiday check — if holiday: monitor off, show message.
1	2-4s	Tkinter + ttkbootstrap window. Splash screen with per-phase progress messages.
2	4-15s	BACKGROUND: Load historical OHLCV. Derive timeframes. Compute indicators. Compute S&R. Detect market mode. Initialize SMC zones from last 5 days historical data (OBs, FVGs, structure).
3	15-25s	BACKGROUND: Upstox REST gap fill. Update indicators on new candles.
4	15-35s	PARALLEL: Fetch GIFT Nifty, US market, VIX, FII/DII, news. Agent 4 generates morning briefing.
5	25-30s	Open Upstox WebSocket. Subscribe instruments. Validate token. If expired: browser auto-opens for login.
6	30-40s	Screen 1 monitor activates. Screen 2 SMC engine activates. Market time check. Splash → dashboard.

Holiday Calendar: config/holidays.json. Populated annually. If holiday: both monitors off, historical data available for review.

 
16. Trade Journals
16.1 Screen 1 Journal — 27 Data Points (23 base + 4 behavior)
#	Data Point	Captured	Purpose
1	VIX at entry	Auto	Fear level at trade time
2	Market Mode	Auto	Trending/Range/Squeeze
3	Session Phase	Auto	Morning/Midday/Afternoon
4	ADX at entry	Auto	Trend strength
5	Nifty Daily Trend	Auto	Daily bias
6	Nifty 1-Hour Trend	Auto	Session bias
7	Monitor Alert Score	Auto	0-21 Alert Score that triggered
8	Pipeline Signal Score	Auto	0-100 Signal Score at analyze time
9	AI Confidence %	Auto	Agent 7 output
10	AI Verdict	Auto	BUY CE/PE/WAIT/AVOID
11	Pattern Detected	Auto	Chart pattern from Agent 5
12	Volume Ratio	Auto	vs 20-candle average
13	RSI at entry	Auto	14-period RSI
14	MACD Signal	Auto	Bullish/Bearish cross
15	Confluence Score	Auto	X/4 timeframes aligned
16	Distance from S&R %	Auto	% from nearest zone
17	S&R Zone Strength	Auto	WEAK/MODERATE/STRONG/VERY STRONG
18	Days to Expiry	Auto	DTE at entry
19	Day of Week	Auto	Mon-Fri
20	Followed AI Verdict	Manual (default YES)	Discipline check
21	Deviated from SL	Manual (default NO)	SL discipline
22	Exited Early	Manual (default NO)	Patience
23	Sized Correctly	Manual (default YES)	Position discipline
24	Behavior Triggered	Auto	OPENING_FAKE/INSTITUTIONAL_DEFENSE/LIQUIDITY_SWEEP/SQUEEZE_BREAKOUT/NULL
25	Pipeline Score	Auto	0-100 score at signal
26	Position Size (₹)	Auto	Kelly output in rupees
27	Position Size Method	Auto	KELLY_ROLLING/FLOOR_xxx

16.2 Screen 2 Journal — 31 Data Points (27 base + 4 SMC)
#	Data Point	Captured	Purpose
1-27	All Screen 1 fields	Auto/Manual	Same as Screen 1
28	SMC Score at entry	Auto	0-100 SMC score when Agent 8 triggered
29	Kill Zone at entry	Auto	MORNING/AFTERNOON/etc.
30	Weekly Day Rating	Auto	HIGH/MEDIUM/LOW/AVOID
31	OB Touch Count	Auto	Times OB was touched at entry

16.3 Auto Paper Trading
•	Screen 1: Agent 7 outputs BUY CE/PE → auto_paper_s1.py logs paper trade at entry_zone midpoint.
•	Screen 2: Agent 8 outputs BUY CE/PE → auto_paper_s2.py logs paper trade at SMC entry_zone midpoint.
•	5-minute PENDING window: ACCEPT (default) / NOT_TAKEN / MODIFY_SIZE.
•	NOT_TAKEN: excluded from Kelly and win rate entirely.
•	Exit auto-tracked: SL hit, T1 hit, T2 hit, or 3:10 PM forced.
•	P&L calculated from live options_chain premium prices.

 
17. Backtest Engine
17.1 Three-Phase Option Pricing
Phase	Method	Accuracy	When
1 (Sprint 6)	Spot proxy: ATM delta ~0.5 + ₹200/trade total cost	±30-50%	Behavior validity testing only. Not P&L projection.
2 (Month 2)	Black-Scholes + historical VIX from NSE	±10-15%	Strategy P&L ranking. Kelly calibration.
3 (Month 3+)	NSE Bhavcopy actual option prices (~2MB/day)	100%	Final validation before live trading.

Phase 1 overstates P&L by 20-50%. Use for behavior validity only — not as real profit projection. Phase 2+ for actual numbers.

17.2 Screen 1 Behavior Backtest
•	All 4 behaviors backtested on 2-year historical OHLCV.
•	Walk-forward: 40-day held-out period. Any behavior passing training must also pass held-out.
•	Per-behavior results: win rate, expectancy, regime breakdown, Kelly inputs.
•	Minimum 150 signals in 2-year period for combo discovery to be valid.

17.3 Screen 1 Combo Discovery (1,140 Combinations)
•	20 indicators × C(20,3) = 1,140 combinations. Runs post-market 3:45 PM.
•	Same walk-forward and PF > 1.5 threshold as behaviors.
•	Computation: ~8-15 seconds with numpy vectorization.
•	Combo alone: max pipeline score 65. Cannot reach Analyze threshold alone.

17.4 Screen 2 SMC Backtest
Setup	Backtest Logic	Target
OB Retest	Historical OBs, check if price retested and bounced	OB respect rate > 60%
FVG Fill	Historical FVGs, check fill % and direction	FVG fill rate > 65%
CHoCH entry	CHoCH events, check if direction held 3+ candles	CHoCH accuracy > 55%
Liquidity sweep reversal	EQH/EQL sweeps, reversal within 5 candles	Sweep reversal rate > 60%
Kill zone filter	Win rates inside vs outside kill zones	Kill zone premium > 15%
Weekly profile	Win rates by day of week	Best day identification

 
18. SMC Screen 2 — Complete Specification
18.1 SMC Concepts Implemented
Concept	Subtypes	Library	Key Threshold
Market Structure BOS/CHoCH	BOS_BULL, BOS_BEAR, CHoCH_BULL, CHoCH_BEAR	joshyattridge smc.bos_choch()	swing_lookback: 10. close_break: True.
Fair Value Gap	BULLISH_FVG, BEARISH_FVG, INVERSION_FVG	joshyattridge smc.fvg()	min_fvg_atr: 1.0. max_active: 5. mitigation_pct: 50%.
Order Blocks	BULLISH_OB, BEARISH_OB, BREAKER, MITIGATION, RECLAIMED, PROPULSION, REJECTION	joshyattridge smc.ob() + lifecycle tracking	displacement_atr: 1.5. mitigation_pct: 50%. max_touch: 3.
Liquidity	EQH, EQL, PDH, PDL, PWH, PWL, SESSION_HIGH, SESSION_LOW, ROUND_NUMBER, BSL, SSL	joshyattridge smc.liquidity() + extensions	range_pct: 0.02%. sweep_lookback: 20 bars.
Premium/Discount	EXTREME_PREMIUM, PREMIUM, EQ, DISCOUNT, EXTREME_DISCOUNT, OTE_BUY, OTE_SELL	Fibonacci math. 100% deterministic.	pd_threshold: 0.80. ote_range: 61.8-78.6%.
Weekly Profile	Mon(Judas), Tue(High Prob), Wed(BNF expiry), Thu(Nifty expiry), Fri(Reduce)	Indian calendar + NSE expiry schedule	Confidence adj per day configurable.
Kill Zones	Opening Judas, Morning, Dead, Afternoon, Expiry Hunt	Time-based IST. Hard rules.	Times configurable in config/behavior_thresholds.json.

18.2 SMC Chart Overlays
Overlay	Style	Color
Bullish OB	Filled rectangle	Green semi-transparent
Bearish OB	Filled rectangle	Red semi-transparent
Breaker Block	Dashed rectangle	Lime green or orange-red dashed
Bullish FVG	Shaded imbalance zone	Blue semi-transparent
Bearish FVG	Shaded imbalance zone	Purple semi-transparent
FVG CE (50% midpoint)	Dotted horizontal line	White dotted
BOS label	Solid horizontal line + label	Green solid (bull) / Red solid (bear)
CHoCH label	Dashed horizontal line + label	Cyan (bull) / Magenta (bear)
EQH/EQL liquidity	Dotted line connecting equal levels	Yellow dotted
Kill zone background	Chart background shading	Green/Grey/Blue/Red per zone
Premium/Discount zones	Right-side vertical bands	Red tint / Green tint

 
19. Build Order — 12 Sprints
Sprint status: Sprints 1-3 COMPLETE (170 tests passing). Update prompts U1-U6 must run first. After updates: 184 tests. Then Sprint 4 onwards.

19.1 Update Prompts (Run Before Sprint 4)
Prompt	File	Change	Tests After
U1	requirements.txt	Add smart-money-concepts>=0.0.6	—
U2	config/config.json	Add smc + strategy_builder + paper_trading + behavior inline config sections	—
U3	core/local_db.py	Add 4 SMC tables + 4 behavior fields to trades + migration code	—
U4	core/startup.py	Add SMC zone init stub in Phase 2	—
U5	config/behavior_thresholds.json	CREATE NEW FILE — all behavior thresholds	—
U6	tests/test_sprint1.py	Add 14 new smoke tests (8 SMC + 6 behavior)	184 total

19.2 Sprint Build Order
Sprint	Status	Files	Test Gate
1	✅ COMPLETE	Foundation, DB, configs, simulator	56 tests (→70 after U1-U6)
2	✅ COMPLETE	Indicators, S&R, scorer, market mode, confluence, patterns	45 tests
3	✅ COMPLETE	Token, WebSocket, news, FII, global, data provider, Kite	69 tests
UPDATE	Run before Sprint 4	U1-U6 update prompts	184 total tests
4	⏳ NEXT	monitor.py, alert_engine.py, expiry_manager.py, test_sprint4.py	Console monitor 3-5 days. 3-7 alerts/day.
5	Pending	agent_base.py + Agents 1-7 (Screen 1) — Agent 8 is Sprint 10	Manual Agent 7 call produces valid JSON. WHY readable.
6	Pending	behavior_detector.py, eqh_eql_detector.py, behavior_scorer.py, pipeline.py, position_sizer.py, behavior_library.py, behavior_backtest_engine.py, backtest_engine.py, trade_logger.py, daily_reporter.py, weekly_reviewer.py, auto_paper_s1.py, strategy_builder.py, test_behavior_engine.py (68 min tests)	Behaviors detect correctly on historical data. Kelly correct. Backtest runs.
7	Pending	ui/app.py + all Screen 1 UI panels + ttkbootstrap theming	Full app market hours. No freeze. Both scores displayed. Analyze button works.
8	Pending	gap_handler.py, strike_selector.py, kite fallbacks. 8 weeks Screen 1 paper trading.	100 Screen 1 paper trades. Weekly reports. Behavior win rates visible.
9	Pending	smc_engine.py, smc_scorer.py, smc_confluence.py, smc_kill_zones.py, smc_zones_manager.py, auto_paper_s2.py, config/smc_thresholds.json, test_sprint9.py	SMC detects FVG/OB/BOS correctly on historical data. Paper engine logs.
10	Pending	agent_smc.py (Agent 8), smc_trade_logger.py, smc_daily_reporter.py, smc_weekly_reviewer.py, smc_backtest_engine.py, smc_strategy_ranker.py	Agent 8 call produces valid JSON. SMC backtest 6-month data.
11	Pending	All Screen 2 UI panels + strategy_builder_panel.py + paper_trading_panel.py	Screen 2 fully functional. SMC zones on chart. Score animates. Analyze gating works.
12	Pending	Integration + 8 weeks both screens paper trading simultaneously	100 trades each screen. Comparison report. Kelly calibrated per behavior.

 
20. Decision Log — All Sessions
Decision	Answer	Rationale
Primary data source	Upstox free. Kite ₹500/month fallback via adapter.	Saves ₹6k/year.
UI framework	Tkinter + ttkbootstrap	HTML+Flask → CORS errors. Tkinter = native.
Database	SQLite WAL mode. 13 tables.	Single file, no server, Python built-in.
Monitor architecture	Two-tier: rule-based always-on + AI on demand	Continuous AI = ₹5k+/month. Rule-based = near zero.
Agents	8 total. 7 Screen 1. 1 Screen 2.	Agent 8 added for SMC. Agents 1-6 unchanged.
AI model	claude-sonnet-4-6 all agents Phase 1-2	Consistency first. Cost review Phase 3.
Prompt caching	From day one in agent_base.py	50-80% cost reduction.
Options chain contradiction	Agent 3 beats Agent 1	Institutional money in OI, not RSI.
Paper trading gate	8 weeks OR 100 trades per screen independently	Both screens must pass before real money.
Kill criterion	Per-behavior. < 48% win rate after 30+ signals → WATCH → RETIRE	Never aggregate. Per-behavior always.
DTE restrictions	REMOVED. 3:10 PM is the only hard exit.	Trader decides on expiry day. DTE < 3 = WARNING only.
Strategy architecture	Behavior-based pipeline. Observation → indicators confirm.	Indicator-first approach has no real edge. Behaviors have documented reasons.
4 core behaviors	Opening Fake, Institutional Defense, Liquidity Sweep, Squeeze Breakout	4 different market phenomena. Not correlated. Different regime permissions.
1140 combos retained	Alongside behaviors as discovery layer. Min 150 signals.	May discover Behavior 5. Combo alone cannot reach Analyze threshold.
Kelly formula	f*=(p×b-q)/b. Quarter Kelly ×0.25. Rolling window.	Correct formula. Rolling from first trade. No phase gates.
Kelly rolling window	<10=floor(2%), 10-99=last20, 100+=last50	Professional implementation. Responds to regime changes.
EQH/EQL detection	Separate file eqh_eql_detector.py. Not in sr_detector.py.	Different concept — liquidity vs S&R.
Two behaviors conflict	Priority B4>B2>B3>B1. -10 penalty. Same direction +10 bonus.	Deterministic. Auditable.
Monitor scores	Two scores: Alert(0-21) Sprint 4, Signal(0-100) Sprint 6. Different jobs.	monitor.py built once. Never touched again after Sprint 4.
Human override	5-min PENDING window. NOT_TAKEN excluded from Kelly.	NOT_TAKEN = ghost trade. Never corrupts Kelly.
Benchmark	≥55% win AND PF>1.5 per behavior = working.	Per-behavior. Never aggregate.
Agent 6 per-behavior	behavior_breakdown in every report. Strongest/weakest flagged.	Most valuable behavioral insight.
Opening Fake doji rule	body_ratio <0.20 → skip. 0.20-0.40 → raise confirm threshold 4/5.	No clear trap direction = no fake to fade.
Combo vs behavior	Behavior always wins. Combo alone max 65. Combo same dir +5.	Discovery layer, not primary.
Sprint 6 test target	68 minimum new tests. Running total 238+ after Sprint 6.	Quality gate before behavior engine goes live.
BRD versioning	v6.0 is final. No more versioning needed until Phase 3 redesign.	Single document. All sessions merged.
Document reference	BRD v6.0 + CLAUDE.md only. No other documents needed.	Clean. No cross-referencing.
Slippage model	₹200/trade total (₹40 brokerage + ₹160 slippage estimate).	Previous ₹40 grossly understated real cost.

 
21. Open Questions
#	Question	Blocks	Status
1	GIFT Nifty calibration: show 30-day accuracy vs actual opens in morning briefing?	feeds/global_fetcher.py + briefing_panel.py	OPEN
2	Telegram alerts: Sprint 3 or Sprint 8?	feeds/ + agent_orchestrator.py	OPEN
3	Add docs/README.md?	Project hygiene	OPEN
4	SMC indicator linking (RSI/MACD as optional Screen 2 filters)?	smc_confluence.py — Phase 3	OPEN — Phase 3
5	Monday Screen 2 trades: exclude from SMC accuracy stats?	smc_accuracy table logic	OPEN — decide before Sprint 10
6	After 100 paper trades: tune behavior thresholds based on actual data?	config/behavior_thresholds.json	OPEN — post paper trading
7	Benchmark comparison: Screen 1 vs Screen 2 win rates after 100 trades each?	auto_paper_s1.py + auto_paper_s2.py	OPEN — Sprint 12

