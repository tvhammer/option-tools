# Options Trading Strategy Plan for €1,000 Account
**Timeframe:** 2 days – 2 weeks (DTE: 2–14)  
**Style:** Semi-automated decision making with simple models  
**Data Sources:** Yahoo Finance, Barchart, MarketChameleon, IB/TastyTrade option chains  
**Account Type:** Hobby / learning / small account growth  
**Risk Tolerance:** Defined risk only, max 5% per trade (€50), max 15% portfolio heat (€150)

---

## 🎯 Core Philosophy

| Principle | Implementation |
|-----------|----------------|
| **Defined risk only** | Spreads, iron condors, butterflies – no naked short |
| **Liquidity filter** | Min 500 contracts OI on short strike, bid/ask < $0.05 wide |
| **IV Rank > 30%** | Prefer high IVR for premium selling; avoid IVR < 20 |
| **No earnings through trade** | Exclude any with earnings before expiration |
| **Delta-based strike selection** | Systematic, not discretionary |
| **Mechanical management rules** | Pre-defined at entry, no "hoping" |

---

## 📊 Data Pipeline (Semi-Automated)

Your existing `option-tools` already does most of this. Extend with:

```
Daily Routine (15 min):
├── 1. Run scanner (MarketChameleon IVR + Barchart volume) → candidate list
├── 2. Enrich with Yahoo: IVR, HV, skew, kurtosis, earnings, 50/200 MA trend
├── 3. Pull option chains from IB/TastyTrade for top 10 candidates
├── 4. Score each candidate with Strategy Models (below)
├── 5. Present top 3 setups → you approve/modify/reject
└── 6. Place orders manually (or via IB API if you later automate)
```

**Add to your toolkit:**
- `ib_insync` for IB option chain + greeks
- `tastytrade-sdk` (or Selenium) for TastyTrade data
- Simple SQLite log for trade journal (entry, exit, P&L, notes)

---

## 🧮 Three Detailed Strategies

---

### Strategy 1: **High-IVR Iron Condor (IC) on Liquid ETFs/Indices**
*Best for: SPY, QQQ, IWM, SPX, NDX, RUT (your "European options" list)*  
*Inspired by your IC notes (Node 160) – adapted for small account & shorter DTE*

#### 📐 Setup Rules
| Parameter | Value |
|-----------|-------|
| **Underlying** | SPY, QQQ, IWM, SPX, NDX, RUT (index options do not fit the account size, see sizing) |
| **DTE** | 7–14 days (avoid 0–2 DTE gamma risk, avoid >21 theta decay slow) |
| **IV Rank** | > 40% (prefer 50%+) |
| **Width** | $2 wide (SPY/QQQ), 50pt (SPX), 100pt (NDX) – **min $3 wide if account allows** |
| **Short Strikes** | 16Δ / 84Δ (≈ 1 SD) → ~68% PoP at entry |
| **Credit Target** | ≥ ⅓ width (e.g., $0.67 on $2 wide) |
| **Max Buying Power** | €50 per IC (cash-secured width) |

#### 📈 Entry Model (Python pseudocode)
```python
def score_ic_candidate(chain, underlying_price, dte, ivr):
    if ivr < 40: return 0
    if dte < 7 or dte > 14: return 0
    
    # Find 16Δ put / 16Δ call (the 84Δ in the table is the same put seen from the call side)
    put_strike = find_delta(chain.puts, -0.16)
    call_strike = find_delta(chain.calls, 0.16)
    width = call_strike - put_strike
    
    if width < 1.5 or width > 3.0: return 0  # $2±0.5 target
    
    credit = get_mid(put_strike) + get_mid(call_strike) - \
             get_mid(put_strike - width) - get_mid(call_strike + width)
    
    credit_ratio = credit / width
    if credit_ratio < 0.30: return 0  # need ≥ 1/3 width
    
    # Scoring components
    score = (credit_ratio * 40) + (ivr * 0.3) + (1/dte * 10)  # prefer nearer expiry
    return score, {
        'put_short': put_strike, 'put_long': put_strike - width,
        'call_short': call_strike, 'call_long': call_strike + width,
        'credit': credit, 'credit_ratio': credit_ratio,
        'pop': 1 - 2*0.16  # ≈ 68%
    }
```

#### ⚙️ Management Rules (Mechanical)
| Trigger | Action |
|---------|--------|
| **Profit 25–33%** | Close entire IC (BTC for 25–33% of credit received) |
| **Breach of short strike + >7 DTE** | Roll untested side: move to new 16Δ, collect ≥ $0.10 credit |
| **Breach of short strike + ≤7 DTE** | Close breached side, roll untested to 30Δ for credit |
| **14 DTE → 7 DTE** | If not managed, reduce size or close |
| **2 DTE** | **Force close** everything (gamma risk) |

#### 💰 Position Sizing (€1,000 Account) — **OPEN DECISION, see "Known inconsistencies"**
- Max loss per contract = (width − credit) × 100 USD. A $1-wide SPY IC with a $0.30 credit risks **$70 (≈ €64)**; a $2-wide one risks ≈ $170.
- **The €50 cap and this universe do not fit together.** Either raise the cap (e.g. €100 = 10%, max 1 position) or restrict to underlyings with $0.50 strike spacing.
- **SPX / NDX / RUT do not fit the account at all** (5-pt wide SPX spread = up to $500 max loss). Remove them from the universe unless the account grows.
- Max 1 IC at a time.

---

### Strategy 2: **Put Credit Spread (PCS) in Uptrend + High IVR**
*Best for: Liquid stocks with 50MA > 200MA, IVR > 35%*  
*Directional bullish, defined risk, benefits from theta + IV crush*

#### 📐 Setup Rules
| Parameter | Value |
|-----------|-------|
| **Universe** | Your scanner output: volume > 500k, IVR > 35%, trend = "up" |
| **DTE** | 10–14 days (enough theta, avoid earnings) |
| **Trend Filter** | 50 SMA > 200 SMA (from your `strategy_helper.find_momentum`) |
| **Short Strike** | 30Δ put (≈ 70% PoP) |
| **Long Strike** | 30Δ – $1 (SPY/QQQ) or – 5pt (SPX) / – 10pt (NDX) |
| **Credit Target** | ≥ 30% of width |
| **Max Loss/Trade** | €50 |

#### 📈 Entry Model
```python
def score_pcs_candidate(chain, underlying_price, dte, ivr, trend):
    if trend != "up": return 0
    if ivr < 35: return 0
    if dte < 10 or dte > 14: return 0
    
    short_strike = find_delta(chain.puts, -0.30)
    long_strike = short_strike - 1.0  # $1 wide for SPY/QQQ
    
    credit = get_mid(short_strike) - get_mid(long_strike)
    width = short_strike - long_strike
    credit_ratio = credit / width
    
    if credit_ratio < 0.30: return 0
    
    # Probability of profit ≈ 1 - 0.30 = 70% (delta approx)
    # But real PoP higher due to IV crush + theta
    score = (credit_ratio * 35) + (ivr * 0.4) + (1/dte * 15)
    return score, {
        'short': short_strike, 'long': long_strike,
        'credit': credit, 'credit_ratio': credit_ratio,
        'pop_est': 0.70, 'max_loss': width - credit
    }
```

#### ⚙️ Management Rules
| Trigger | Action |
|---------|--------|
| **Profit 50%** | Close (BTC for 50% of credit) |
| **Underlying touches short strike** | If >7 DTE: roll down 1 strike, collect credit. If ≤7 DTE: close. |
| **Trend breaks (50MA < 200MA)** | Close immediately |
| **IVR drops < 20%** | Close (IV crush benefit realized) |
| **2 DTE** | Force close |

#### 💰 Position Sizing
- $1 wide PCS, credit ≈ $0.30 → max loss ≈ $70 ≈ €64 per contract (see inconsistency #1)
- **Max 1 PCS at a time** (~6.5% of account)
- Can run **1 IC + 1 PCS** simultaneously if uncorrelated underlyings (max ~15% portfolio heat)

---

### Strategy 3: **Earnings Volatility Crush – Short Straddle/Strangle (Defined Risk)**
*Best for: High-IVR stocks with earnings **after** your expiration*  
*Actually: **Avoid earnings**. Instead, trade **post-earnings IV crush** or **pre-earnings IV ramp** with defined risk.*

**Wait – you said no earnings through trade. Let's pivot to a better Strategy 3:**

---

### Strategy 3 (Revised): **Iron Butterfly (IBF) on High-IVR Range-Bound Candidates**
*Best for: Stocks/ETFs with high IVR, low HV/IV ratio (IV > HV), near support/resistance*  
*Collects max premium at center strike; defined risk both sides*

#### 📐 Setup Rules
| Parameter | Value |
|-----------|-------|
| **Universe** | Scanner: IVR > 50%, HV/IV < 0.8, volume > 500k |
| **DTE** | 7–10 days |
| **Center Strike** | ATM (closest to current price) |
| **Wings** | $2 wide (SPY/QQQ) / 50pt (SPX) / 100pt (NDX) |
| **Credit Target** | ≥ 40% of wing width |
| **Range Expectation** | Price stays within ±1.5 SD (≈ short strikes of 30Δ IC) |

#### 📈 Entry Model
```python
def score_ibf_candidate(chain, underlying_price, dte, ivr, hv_iv_ratio):
    if ivr < 50: return 0
    if hv_iv_ratio > 0.8: return 0  # IV not rich vs realized
    if dte < 7 or dte > 10: return 0
    
    atm_strike = round(underlying_price)  # nearest strike
    wing_width = 2.0  # $2 for SPY
    
    credit = (get_mid(atm_strike, 'put') + get_mid(atm_strike, 'call')) - \
             (get_mid(atm_strike - wing_width, 'put') + get_mid(atm_strike + wing_width, 'call'))
    
    credit_ratio = credit / wing_width
    if credit_ratio < 0.40: return 0  # need more premium at center
    
    # Max profit at center, max loss at wings
    score = (credit_ratio * 30) + (ivr * 0.3) + ((1 - hv_iv_ratio) * 20)
    return score, {
        'center': atm_strike,
        'put_long': atm_strike - wing_width, 'call_long': atm_strike + wing_width,
        'credit': credit, 'credit_ratio': credit_ratio,
        'max_profit': credit, 'max_loss': wing_width - credit
    }
```

#### ⚙️ Management Rules
| Trigger | Action |
|---------|--------|
| **Profit 25%** | Close (IBF decays fast near center) |
| **Price moves > 0.5× wing width from center** | Close or roll center to new ATM (collect credit) |
| **5 DTE** | Close regardless (gamma risk near center). Per-strategy `close_dte`: IC 2, PCS 2, **IBF 5** |
| **IVR drops < 30%** | Close (vol crush captured) |

#### 💰 Position Sizing
- $2 wide IBF → max loss ≈ $120 ≈ €110 (11%) — above the €50 cap; $1 wide ≈ $60 ≈ €55
- **Max 1 IBF at a time**
- SPX variants are not viable for this account size.

---

### Strategy 4 (candidate): **Calendar Spread (long back-month / short front-month, same strike)**
*Status: idea under evaluation, not yet specified or scored. Needs real per-expiry IV data (Phase 0).*

**Why it is attractive here:** debit trade, max loss = debit paid (typically $30–100 per contract, i.e. fits the €50 cap better than the credit spreads), no margin beyond the debit, and the "roll" is native: when the short front leg expires or is tested, sell a new front leg against the same long.

**Differences that matter for the watchdog:**
- Two expirations per position, so DTE is tracked **per leg**. `close_dte` applies to the **front** leg; the back leg may be 2–5 weeks out.
- Long-vega: profits from rising IV and from price staying near the strike, loses on large moves and on IV falling. Opposite of the IC/IBF exposure; it diversifies, but IVR filters invert (prefer low-to-mid IVR at entry, front IV ≥ back IV only slightly).
- Max profit is not fixed (depends on back-leg value at front expiry), so profit targets are expressed as **% of debit** (e.g. 15–25%).
- Early assignment risk on the short front leg for American-style options (ITM, ex-dividend). Prefer ETFs/indices with low dividend risk; close or roll before the front leg goes deep ITM.
- No-earnings rule: back leg should not span earnings in v1 (earnings calendars are a different, riskier strategy).
- Commissions: 4 legs per round trip; make sure debit is large enough to justify them.

**Proposed rule spec (`rules_json`, to be validated):**
```json
{
  "profit_target_pct_of_debit": 0.20,
  "stop_loss_pct_of_debit": 0.50,
  "front_close_dte": 2,          // close whole position or roll the front leg
  "tent_break_pct": 0.03,        // |underlying - strike| / strike beyond this -> close
  "min_back_dte": 14             // never hold a back leg with less than this
}
```

**Entry sketch:** strike = ATM or ±1 strike; front DTE 7–10, back DTE 21–35; debit ≤ per-trade cap; liquidity as for other strategies; score on (back IV − front IV) term structure, expected move vs tent width, debit/width ratio.

**Decided:** same-strike calendars only (no diagonals). **Still open:** whether the front-leg roll is automated; in the watchdog it is the first roll candidate because risk does not increase.

---

## 🔄 Weekly Workflow

| Day | Action |
|-----|--------|
| **Mon 08:00 CET** | Run scanner → update candidate list |
| **Mon 09:00** | Pull chains for top 10 → score all 3 models |
| **Mon 09:30** | Review top 3 setups → place orders (limit, mid-price) |
| **Tue–Fri** | Positions are monitored by the watchdog (see "Automated Position Management"); until it exists, check 2×/day and apply the rules mechanically. |
| **Fri 15:00** | Force-close any ≤2 DTE |
| **Weekend** | Journal review: P&L, rule adherence, model accuracy |

---

## 📈 Model Validation & Improvement

Track these metrics per strategy:
| Metric | Target |
|--------|--------|
| **Win Rate** | > 65% (IC/IBF), > 60% (PCS) |
| **Avg Winner / Avg Loser** | > 0.8 (IC/IBF), > 1.0 (PCS) |
| **Expectancy** | > €2/trade |
| **Sharpe (monthly)** | > 1.0 |
| **Max Drawdown** | < 15% |

**Simple backtest framework** (add to `options.ipynb`):
```python
# Walk-forward: for each Friday, simulate entries for next week
# Apply management rules mechanically
# Output: equity curve, drawdown, win rate per strategy
```

---

## 🛠️ Implementation Checklist

> **Status (2026-11):** the scanner/scoring runs on **mock chains** (`daily_runner.fetch_mock_chain`, random IVR/OI). The `--live` path only swaps in a real underlying price. `ib_chain.get_option_chain()` returns `{}`. No management rule is executable yet; they are text strings.

- [ ] **Scanner**: Extend `marketchameleon.py` + `barchart.py` → unified candidate DF
- [ ] **Enrichment**: Add `yfinance` earnings, SMA50/200, HV(20), IV/HV ratio
- [ ] **IB/TastyTrade Chain Pull**: `ib_insync` for greeks, bid/ask, OI
- [ ] **Scoring Module**: `strategy_models.py` with 3 scoring functions above
- [ ] **Dashboard**: Simple CLI or Streamlit showing top 5 per strategy
- [ ] **Trade Log**: SQLite with entry/exit, strategy, P&L, notes
- [ ] **Alerts**: Telegram/email when score > threshold or management trigger hit

---

## ⚠️ Risk Rules (Non-Negotiable)

1. **No trade without defined max loss ≤ €50**
2. **No trade through earnings**
3. **No naked short options ever**
4. **Max 2 concurrent positions (uncorrelated underlyings)**
5. **Force close at 2 DTE**
6. **Stop trading if weekly loss > €100 (10%)**
7. **No "adjusting" outside pre-defined rules**

---

## 🤖 Automated Position Management (Watchdog)

**Principle:** entries stay manual. Once a trade is open, a watchdog applies the rules that were fixed at entry. Closing is automated first; rolling stays a human decision until the journal proves otherwise.

### Design decisions

1. **Rules are code, not LLM judgement.** Profit target, stop, DTE, breach and trend checks are deterministic Python (`manage.py`) with unit tests. An LLM may *describe* a situation (alert text, daily summary); it never decides an order.
2. **Resting orders for price-triggered exits.** At entry, place a GTC closing limit at the profit target (e.g. BTC at 25–50% of credit). IB fills it in real time; no polling latency. The watchdog covers what orders cannot: DTE force-close, trend break, IV-rank drop, breach, reconciliation.
3. **Rules are snapshotted per trade at entry** (`rules_json` on the journal row). Later config edits must not change the rules of an open trade.
4. **Reconcile with IB every run.** Journal vs `reqPositions()` grouped into strategies. Any mismatch (unknown position, missing leg, wrong qty) → alert and **take no action**.
5. **Cheap by default.** The first workflow step is a plain shell evaluation. Everything else is skipped unless it signals "action needed".
6. **Rolls are new risk.** A roll for credit extends duration and can exceed max loss per trade. Phase 4 only.

### Rule specification (stored in `rules_json`)

```json
{
  "profit_target_pct": 0.25,          // close when debit-to-close <= (1 - pct) * credit
  "stop_loss_multiple": 1.5,          // close when debit-to-close >= multiple * credit   (OPEN DECISION)
  "close_dte": 2,                     // force close at or below this DTE
  "breach": {"action": "close"},      // short strike touched: close (Phase 1-3); roll only in Phase 4
  "trend_break": true,                // PCS: 50 SMA < 200 SMA -> close
  "min_iv_rank": 20                   // PCS/IBF: IVR below -> close
}
```

| Trigger | Evaluated by | Phase 2-3 action |
|---|---|---|
| Profit target | resting GTC order + watchdog check | close (combo limit at mid, walk to natural within limits) |
| Stop loss | watchdog | close |
| DTE ≤ `close_dte` | watchdog | close |
| Short strike breached | watchdog | close (rolls: proposal only) |
| Trend break / IVR drop | watchdog | close |
| Position/journal mismatch | watchdog | **alert only** |

### Architecture

```
systemd timer / cron (every 10-30 min, US market hours only, with lock file)
  └─ stepflow run options/manage
       ├─ evaluate   (shell: manage.py evaluate)  exit 0 = nothing to do, 2 = action, 1 = error
       ├─ report     (LLM, when evaluate.exitCode == 2): human-readable explanation + notification
       ├─ approve    (gate: human, Phase 2; removed for pure close rules in Phase 3)
       └─ execute    (shell: manage.py execute --run <id>)  places combo order, logs to journal
```

Sketch (`ai-workflows/options/manage.yaml`):

```yaml
name: options/manage
steps:
  - id: evaluate
    run: "python ~/option-tools/manage.py evaluate"
  - id: report
    when: "evaluate.exitCode == 2"
    prompt: "Explain these management actions in plain language:\n{{evaluate.output}}"
  - id: approve
    when: "evaluate.exitCode == 2"
    gate: human
    show: report
  - id: execute
    when: "evaluate.exitCode == 2"
    run: "python ~/option-tools/manage.py execute"
```

### What stepflow still needs

- Overlap lock so two runs never manage the same position at once
- Scheduling and market-hours check (systemd timer + `manage.py` returns 0 when closed)
- Push notification when paused at a gate (Telegram/email) containing the resume command
- Optional: parse JSON out of shell step output (today only `exitCode` and raw `output`)

### Execution safety

- **Kill switch:** file `~/option-tools/STOP` (or env var) → evaluate only, never send orders.
- **Limits in code:** only closing orders in Phase 2-3; max 1 contract per order; refuse symbols not in the journal.
- **Combo (BAG) orders** for spreads; never leg out manually. Limit orders only, bounded price walk; no market orders.
- **Idempotency:** one pending closing order per trade; check open orders before sending.
- **Everything logged:** each decision (inputs, rule hit, order id) is written to the journal and the stepflow run artifacts.
- **Assignment:** the force-close at `close_dte` is the protection against early assignment/pin risk on American-style stock options.

### Phases

| Phase | Scope | Orders |
|---|---|---|
| 0 | Fix prerequisites (below) | none |
| 1 | Read-only watchdog: positions from IB, reconcile, rule evaluation, alerts, daily summary | none |
| 2 | Paper account: closing orders with approval gate; GTC profit-target orders at entry | paper only |
| 3 | Live, close-only, 1 contract, kill switch, no gate for pure close rules (maybe keep gate for stop-loss) | live |
| 4 | Roll proposals with approval; full automation only if journal data justifies it | live |

Exit criteria per phase: ≥ 2 weeks without false actions, every alert explained by a logged rule hit.

### Phase 0 prerequisites

- [x] **IB ports** (confirmed: **4001 = LIVE**, 4002 = paper; docs/comments corrected). Note: the current code defaults to 4001, i.e. the live account (read-only so far). [x] Paper account is running on **4002** (verified read-only: account `DUR359396`); `config.yaml` and `ib_chain.py` now default to 4002. [ ] Still to do: explicit `ib_mode: live` switch for live (any order-capable code must refuse 4001 unless set).
- [ ] Real-time US options data subscription (OPRA) in IB. Checked on the paper session: real-time stock data is not subscribed (IB error 354, delayed SPY price works); delayed option bid/ask/greeks are unconfirmed, a probe of them was inconclusive. Re-test after subscribing.
- [ ] Replace unmaintained `ib_insync==0.9.86` with `ib_async`.
- [ ] Unattended Gateway login/restart (IBC) and a heartbeat alert if the Gateway is down.
- [ ] Unit convention: store **per-contract USD** for credit, max_loss, max_profit, buying power (journal sample mixes per-share 0.70 with per-contract 70).
- [ ] Journal: add `rules_json`, `ib_con_ids` (leg conIds), `ib_order_ids`, `quantity`, `currency`.
- [ ] `manage.py` rule evaluator with unit tests (pure functions: position + quotes + rules → actions).
- [ ] Remove hard-coded `/home/vermes/option-tools` paths; make mock vs live explicit (the current `--live` path is misleading).
- [ ] Resolve the "Known inconsistencies" below.

### Known inconsistencies in this plan (decide, then update the text)

1. **Risk cap vs universe.** €50 max loss per trade does not fit $1-wide spreads (≈ €64) or any index option. Decide: raise cap (suggest €100, 1 position) or narrow the universe to $0.50-wide spreads.
2. **Profit target.** IC says 25–33%, `config.yaml` says 30%, IBF says 25%. `config.yaml` is canonical; the plan text should quote it.
3. **Force-close DTE.** Global `force_close_dte: 2` vs IBF "5 DTE". Make `close_dte` per strategy.
4. **Stop loss.** None is defined for IC/IBF besides breach handling. Define `stop_loss_multiple` explicitly.
5. **Commissions.** A $1-wide IC credit of ~$30 minus 8 option-leg commissions per round trip is a large share of profit; include costs in the profit target and in model validation.
6. ~~0–2 DTE~~ **Decided: no 0DTE.** Minimum DTE for any entry stays ≥ 5.

---

## 🎓 Learning Path (Next 3 Months)

| Month | Focus |
|-------|-------|
| **1** | Paper trade all 3 strategies. Build scanner + scoring. Journal every decision. |
| **2** | Go live with 1 contract each. Refine management rules. Track model accuracy. |
| **3** | Add backtest framework. Optimize strike/DTE selection. (0DTE is out of scope.) |

---

## 📎 Your Existing Assets to Leverage

| Asset | Use For |
|-------|---------|
| `marketchameleon.py` | IVR rankings → primary universe filter |
| `barchart.py` | Volume/PCR filter → liquidity confirmation |
| `yahoo_tools.py` | HV, skew, kurtosis, earnings, SMA trend |
| `strategy_helper.py` | Assembly pipeline, stats calc |
| CherryTree Node 160 | IC management rules (adapt to shorter DTE) |
| CherryTree Node 88 | Technical filters (RSI, RVOL, VWAP) |
| IB + TastyTrade | Real-time chains, greeks, execution |

---

*Generated from your option-tools + CherryTree research. Update as models evolve.*