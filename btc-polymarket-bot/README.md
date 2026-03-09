# BTC Polymarket Bot

Automated signal-based trading bot for Polymarket's BTC Up/Down 15-minute markets.

## How it works

Every 60 seconds within a 15-minute market window, the bot:

1. **Finds the active market** on Polymarket (BTC Up/Down)
2. **Collects 9 signals** simultaneously:

| Signal | Source | What it measures |
|---|---|---|
| `price_momentum` | Binance public REST | Short-term BTC returns (1/3/5/15 candles) |
| `rsi` | Computed locally (ta lib) | Overbought / oversold |
| `macd` | Computed locally | Trend momentum |
| `ema_stack` | Computed locally | EMA 9/21/55 alignment |
| `fear_greed` | alternative.me | Market sentiment |
| `funding_rate` | Binance futures | Perp longs vs shorts pressure |
| `open_interest` | Binance futures | OI change direction |
| `liquidations` | Binance futures | Long vs short liquidations |
| `polymarket_flow` | Polymarket CLOB | Smart money order flow |

3. **Aggregates signals** via weighted average → P(BTC UP in next 15 min)
4. **Computes edge**: `edge = my_prob - market_implied_prob`
5. **Bets if `edge >= 5%`** using **half-Kelly criterion** for sizing
6. **Caps bet** at 20% of bankroll per trade

## Quick start

```bash
cd btc-polymarket-bot

# Install dependencies
pip install -r requirements.txt

# Run in dry-run (paper trading) mode
python main.py

# Check your P&L anytime
python main.py --summary
```

No API keys needed — all data sources are public.

## Configuration

All parameters are in `config.py`:

```python
INITIAL_BANKROLL   = 1_000.0   # starting balance (USD)
KELLY_FRACTION     = 0.5       # half-Kelly
MAX_BET_FRACTION   = 0.20      # max 20% of bankroll per bet
MIN_EDGE           = 0.05      # only bet if edge >= 5%
SIGNAL_WEIGHTS     = { ... }   # tune per-signal weights
```

## Project structure

```
btc-polymarket-bot/
├── config.py               # All parameters
├── main.py                 # Orchestrator loop
├── requirements.txt
├── signals/
│   ├── price.py            # Real-time price + momentum
│   ├── technicals.py       # RSI, MACD, EMA stack
│   ├── sentiment.py        # Fear & Greed index
│   ├── derivatives.py      # Funding, OI, liquidations
│   └── polymarket.py       # Market discovery + order flow
├── engine/
│   ├── probability.py      # Signal aggregation
│   └── kelly.py            # Bet sizing
├── trading/
│   └── paper.py            # Dry-run simulation + P&L tracking
└── state/
    └── portfolio.json      # Persistent state (auto-created)
```

## Going live

When you're satisfied with dry-run performance:

1. Get Polymarket CLOB API credentials (private key + proxy wallet)
2. Implement `trading/live.py` using the [Polymarket CLOB client](https://github.com/Polymarket/py-clob-client)
3. Set `MODE=live` in config and run

> **Never risk money you can't afford to lose. Past dry-run performance does not guarantee live performance.**
