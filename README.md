# SPY EMA CHAD 3

**SPY EMA Crossover Highly Automated Dealer** - An automated trading system for options trading based on EMA and VWAP indicators.

## Strategy Overview

This strategy trades SPY (or other tickers) based on the relationship between price, 9 EMA, 20 EMA, and VWAP:

1. At 9:00 AM Central Time, check if the price is above or below all three indicators (9 EMA, 20 EMA, VWAP)
2. If price is above all three, wait for price to touch the 9 EMA for a long entry
3. If price is below all three, wait for price to touch the 9 EMA for a short entry
4. Exit when:
   - Take profit: $1.00 gain per contract
   - Stop loss: Candle body closes opposite to all three indicators
   - Force close: Any open position is closed at 2:55 PM

The strategy is designed for 5-minute bars and trades only once per day.

## Requirements

- Python 3.7+
- Interactive Brokers account
- Interactive Brokers Trader Workstation (TWS) or IB Gateway

## Installation

1. Clone this repository
2. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

## Setup

1. Make sure Interactive Brokers TWS or IB Gateway is running
2. Enable API connections in TWS/Gateway (File -> Global Configuration -> API -> Settings)
   - Enable ActiveX and Socket Clients
   - Set port (default: 7497 for paper trading, 7496 for live trading)
   - Allow connections from localhost

## Usage

Run the strategy:

```
python spy_ema_chad.py
```

## Configuration

You can modify the following parameters in the script:

- `ticker`: Symbol to trade (default: "SPY")
- `profit_target`: Target profit in dollars (default: 1.0)
- `market_open`: Market open time (default: "08:30:00")
- `market_close`: Market close time (default: "15:00:00")
- `signal_time`: Time to check initial conditions (default: "09:00:00")
- `force_close_time`: Time to force close positions (default: "15:50:00")
- `ema_short`: Short EMA period (default: 9)
- `ema_long`: Long EMA period (default: 20)
- `paper_trading`: Whether to use paper trading (default: True)

## Disclaimer

This software is for educational purposes only. Use at your own risk. Trading financial instruments involves substantial risk of loss and is not suitable for every investor.

## License

MIT 
