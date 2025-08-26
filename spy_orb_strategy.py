#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPY ORB CHAD: SPY Opening Range Breakout Highly Automated Dealer
# An automated trading system that implements a 5-minute opening range breakout strategy for SPY options.

import pandas as pd
import numpy as np
import datetime
import time
import pytz
from ib_insync import *


class SPYORBStrategy:
    """Opening Range Breakout strategy for SPY 0-DTE options.

    The logic follows the specification supplied by the user.  It is intentionally
    written in a similar style to `spy_ema_chad.py` so that maintainers can easily
    jump between the two files.
    """

    def __init__(
        self,
        ticker: str = "SPY",
        contracts: int = 2,
        underlying_move_target: float = None,
        itm_offset: float = 1.05,
        market_open: str = "09:30:00",
        market_close: str = "16:00:00",
        force_close_time: str = "15:50:00",
        bar_size: str = "5 mins",
        paper_trading: bool = True,
        port: int = 7498,
    ):
        self.ticker = ticker
        self.contracts = contracts
        
        # Set ticker-specific parameters
        self.use_keltner_stop = ticker in ["NVDA", "TSLA", "AMZN", "IWM"]
        
        if underlying_move_target is None:
            # Set defaults based on ticker
            ticker_targets = {
                "SPY": (1.0, 1.05),  # (first_target, itm_offset)
                "QQQ": (1.0, 1.05),
                "NVDA": (0.5, 0.5),  # (first_target, second_target_additional)
                "TSLA": (2.0, 2.0),
                "AMZN": (0.75, 0.75),
                "IWM": (0.5, 0.5),
            }
            if ticker in ticker_targets:
                self.underlying_move_target, self.second_target = ticker_targets[ticker]
                if ticker in ["SPY", "QQQ"]:
                    self.itm_offset = self.second_target
                    self.use_itm_target = True
                else:
                    self.use_itm_target = False
            else:
                self.underlying_move_target = 1.0
                self.second_target = 1.05
                self.itm_offset = 1.05
                self.use_itm_target = True
        else:
            self.underlying_move_target = underlying_move_target
            self.itm_offset = itm_offset
            self.second_target = itm_offset
            self.use_itm_target = ticker in ["SPY", "QQQ"]
        
        self.market_open = market_open
        self.market_close = market_close
        self.force_close_time = force_close_time
        self.bar_size = bar_size
        self.paper_trading = paper_trading
        self.port = port
        # Trading state
        self.opening_range_high = None
        self.opening_range_low = None
        self.opening_range_set = False

        self.position = None  # "CALL" or "PUT"
        self.option_contract = None
        self.entry_underlying_price = None
        self.entry_option_price = None
        self.entry_strike = None
        self.half_position_closed = False

        # IB & timezone
        self.tz = pytz.timezone("US/Eastern")
        self.ib = IB()

        # Dynamic Client Id
        self.client_id = hash(f"{self.ticker}_{id(self)}") % 100 + 1

    # ---------------------------------------------------------------------
    # Interactive Brokers helpers
    # ---------------------------------------------------------------------
    def connect_to_ib(self, host: str = "127.0.0.1", client_id: int = 9, max_retries: int = 3) -> bool:
        """Connect to TWS / IB Gateway. Re-tries a few times for resiliency."""
        port = self.port
        for attempt in range(1, max_retries + 1):
            try:
                if self.ib.isConnected():
                    self.ib.disconnect()
                    time.sleep(1)
                self.ib.connect(host, port, clientId=self.client_id)
                print(
                    f"Connected to Interactive Brokers {'Paper' if self.paper_trading else 'Live'} trading"
                )
                return True
            except Exception as exc:
                print(f"Connection attempt {attempt}/{max_retries} failed: {exc}")
                time.sleep(2)
        print("Unable to connect after maximum retries – exiting.")
        return False

    def get_stock_contract(self):
        return Stock(self.ticker, "SMART", "USD")

    def get_underlying_price(self) -> float:
        ticker = self.ib.reqTickers(self.get_stock_contract())[0]
        return ticker.marketPrice()

    def get_option_contract(self, right: str) -> Option:
        """Return the ATM 0-DTE option contract for SPY (right="C" or "P")."""
        today = datetime.datetime.now(self.tz).date()
        expiry_str = today.strftime("%Y%m%d")  # 0-DTE (same-day) expiry for SPY

        spot = self.get_underlying_price()
        strike = round(spot) if not np.isnan(spot) else 0

        contract = Option(
            symbol=self.ticker,
            lastTradeDateOrContractMonth=expiry_str,
            strike=strike,
            right=right,
            exchange="SMART",
            multiplier="100",
            currency="USD",
        )
        details = self.ib.reqContractDetails(contract)
        if details:
            contract = details[0].contract
            self.ib.qualifyContracts(contract)
        return contract

    # ---------------------------------------------------------------------
    # Market & timing helpers
    # ---------------------------------------------------------------------
    def is_market_open(self) -> bool:
        now = datetime.datetime.now(self.tz)
        today = now.date()
        mo = self.tz.localize(
            datetime.datetime.combine(today, datetime.datetime.strptime(self.market_open, "%H:%M:%S").time())
        )
        mc = self.tz.localize(
            datetime.datetime.combine(today, datetime.datetime.strptime(self.market_close, "%H:%M:%S").time())
        )
        return mo <= now <= mc

    def is_force_close_time(self) -> bool:
        now = datetime.datetime.now(self.tz)
        today = now.date()
        fct = self.tz.localize(
            datetime.datetime.combine(today, datetime.datetime.strptime(self.force_close_time, "%H:%M:%S").time())
        )
        return now >= fct

    # ---------------------------------------------------------------------
    # Data helpers
    # ---------------------------------------------------------------------
    def get_intraday_5min(self, duration: str = "1 D") -> pd.DataFrame | None:
        """Fetch 5-minute historical data for the underlying."""
        contract = self.get_stock_contract()
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=self.bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        if not bars:
            return None
        df = util.df(bars)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def calculate_opening_range(self, df: pd.DataFrame):
        today = datetime.datetime.now(self.tz).date()
        # Filter today's data
        today_df = df[df["date"].dt.date == today].copy()
        if today_df.empty:
            return  # Wait until we have today's data

        market_open_dt = self.tz.localize(
            datetime.datetime.combine(today, datetime.datetime.strptime(self.market_open, "%H:%M:%S").time())
        )
        range_end = market_open_dt + datetime.timedelta(minutes=15)
        
        # Only use candles that start at or after market open and before range end
        opening_df = today_df[
            (today_df["date"] >= market_open_dt) & 
            (today_df["date"] < range_end)
        ]
        
        if len(opening_df) < 3:
            return  # Need exactly 3 complete candles (8:30, 8:35, 8:40)

        self.opening_range_high = opening_df["high"].max()
        self.opening_range_low = opening_df["low"].min()
        self.opening_range_set = True
        print(
            f"Opening range set - High: {self.opening_range_high:.2f}, Low: {self.opening_range_low:.2f}"
        )

    def calculate_keltner_channels(self, df: pd.DataFrame, period: int = 20, multiplier: float = 2.0):
        """Calculate Keltner Channels for the given dataframe."""
        df['ema'] = df['close'].ewm(span=period, adjust=False).mean()
        df['atr'] = self.calculate_atr(df, period)
        df['kc_upper'] = df['ema'] + (multiplier * df['atr'])
        df['kc_lower'] = df['ema'] - (multiplier * df['atr'])
        return df

    def calculate_atr(self, df: pd.DataFrame, period: int = 20):
        """Calculate Average True Range."""
        df['h_l'] = df['high'] - df['low']
        df['h_pc'] = abs(df['high'] - df['close'].shift())
        df['l_pc'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['h_l', 'h_pc', 'l_pc']].max(axis=1)
        return df['tr'].rolling(window=period).mean()

    # ---------------------------------------------------------------------
    # Order helpers
    # ---------------------------------------------------------------------
    def place_order(self, action: str, quantity: int):
        if self.option_contract is None:
            raise RuntimeError("Option contract not initialised before order placement.")
        order = MarketOrder(action, quantity)
        trade = self.ib.placeOrder(self.option_contract, order)
        self.ib.sleep(1)
        print(f"{datetime.datetime.now(self.tz)} - {action} {quantity} {self.option_contract.localSymbol}")
        return trade

    def enter_position(self, position_type: str):
        """Enter CALL or PUT position (always buying options)."""
        right = "C" if position_type == "CALL" else "P"
        self.option_contract = self.get_option_contract(right)
        self.place_order("BUY", self.contracts)

        # Record entry stats
        self.position = position_type
        self.entry_underlying_price = self.get_underlying_price()
        opt_ticker = self.ib.reqTickers(self.option_contract)[0]
        self.entry_option_price = opt_ticker.marketPrice()
        self.entry_strike = self.option_contract.strike
        print(
            f"Entered {position_type} - Underlying: {self.entry_underlying_price:.2f}, Option: {self.entry_option_price:.2f}, Strike: {self.entry_strike}"
        )

    def exit_all(self, reason: str):
        if self.position is None or self.option_contract is None:
            return
        remaining = self.contracts // 2 if self.half_position_closed else self.contracts
        self.place_order("SELL", remaining)
        # Log P/L (rough, per option contract)
        opt_price = self.ib.reqTickers(self.option_contract)[0].marketPrice()
        pnl_per_contract = (opt_price - self.entry_option_price) * 100
        direction = "CALL" if self.position == "CALL" else "PUT"
        # Reset
        self.position = None
        self.option_contract = None
        self.entry_underlying_price = None
        self.entry_option_price = None
        self.entry_strike = None
        self.half_position_closed = False

    # ---------------------------------------------------------------------
    # Core loop
    # ---------------------------------------------------------------------
    def run(self):
        if not self.connect_to_ib():
            return

        try:
            daily_trade_done = False
            print("Starting SPY ORB strategy ...")
            while True:
                now = datetime.datetime.now(self.tz)

                # Handle market hours
                if not self.is_market_open():
                    if self.position is not None:
                        print("Market closed - force exiting open position.")
                        self.exit_all("Market closed")
                    daily_trade_done = False  # Reset for next day
                    time.sleep(60)
                    continue

                # Force-close time
                if self.is_force_close_time() and self.position is not None:
                    print("Force-close time reached - closing position.")
                    self.exit_all("15:50 force close")

                # Historical bars - used for signals
                df = self.get_intraday_5min()
                if df is None or df.empty:
                    print("No historical data - waiting...")
                    time.sleep(30)
                    continue

                # Ensure opening range captured
                if not self.opening_range_set:
                    self.calculate_opening_range(df)
                    time.sleep(5)
                    continue  # Need the range before anything else

                # Entry check (one trade per day)
                if not daily_trade_done and self.position is None:
                    last_closed = df.iloc[-2]  # Last *completed* 5-minute bar
                    if last_closed["close"] > self.opening_range_high:
                        self.enter_position("CALL")
                        daily_trade_done = True
                    elif last_closed["close"] < self.opening_range_low:
                        self.enter_position("PUT")
                        daily_trade_done = True

                # Manage open position
                if self.position is not None:
                    underlying_price = self.get_underlying_price()
                    option_price = self.ib.reqTickers(self.option_contract)[0].marketPrice()

                    # Stop loss logic based on ticker
                    if self.use_keltner_stop:
                        # Calculate Keltner Channels
                        df_with_kc = self.calculate_keltner_channels(df.copy())
                        latest_kc = df_with_kc.iloc[-1]
                        
                        # Keltner stop loss (real-time, not waiting for candle close)
                        if self.position == "CALL" and underlying_price <= latest_kc['kc_lower']:
                            self.exit_all("Keltner stop loss (CALL)")
                            time.sleep(5)
                            continue
                        if self.position == "PUT" and underlying_price >= latest_kc['kc_upper']:
                            self.exit_all("Keltner stop loss (PUT)")
                            time.sleep(5)
                            continue
                    else:
                        # Opening range stop loss (SPY/QQQ)
                        last_closed = df.iloc[-2]
                        if self.position == "CALL" and last_closed["close"] < self.opening_range_low:
                            self.exit_all("Initial stop loss (CALL)")
                            time.sleep(5)
                            continue
                        if self.position == "PUT" and last_closed["close"] > self.opening_range_high:
                            self.exit_all("Initial stop loss (PUT)")
                            time.sleep(5)
                            continue

                    # Profit target 1
                    if not self.half_position_closed:
                        if self.position == "CALL" and underlying_price >= self.entry_underlying_price + self.underlying_move_target:
                            self.place_order("SELL", self.contracts // 2)
                            self.half_position_closed = True
                            print(f"First profit target hit (${self.underlying_move_target} move) - sold half, stop moved to breakeven.")
                        elif self.position == "PUT" and underlying_price <= self.entry_underlying_price - self.underlying_move_target:
                            self.place_order("SELL", self.contracts // 2)
                            self.half_position_closed = True
                            print(f"First profit target hit (${self.underlying_move_target} move) - sold half, stop moved to breakeven.")

                    # Breakeven stop on remaining half (adjusted stop loss)
                    if self.half_position_closed:
                        # Use option price minus 0.01 as the threshold
                        if option_price <= self.entry_option_price + 0.01:
                            self.exit_all("Adjusted stop loss (breakeven)")
                            time.sleep(5)
                            continue

                    # Profit target 2
                    if self.use_itm_target:
                        # SPY/QQQ: ITM-based target
                        if self.position == "CALL" and underlying_price >= self.entry_strike + self.itm_offset:
                            self.exit_all("Second profit target - ITM (CALL)")
                            time.sleep(5)
                            continue
                        if self.position == "PUT" and underlying_price <= self.entry_strike - self.itm_offset:
                            self.exit_all("Second profit target - ITM (PUT)")
                            time.sleep(5)
                            continue
                    else:
                        # Other tickers: additional underlying movement
                        total_target = self.underlying_move_target + self.second_target
                        if self.position == "CALL" and underlying_price >= self.entry_underlying_price + total_target:
                            self.exit_all(f"Second profit target (${total_target} total move)")
                            time.sleep(5)
                            continue
                        if self.position == "PUT" and underlying_price <= self.entry_underlying_price - total_target:
                            self.exit_all(f"Second profit target (${total_target} total move)")
                            time.sleep(5)
                            continue

                # Loop nap - 5-sec granularity is more than enough for 5-min bars
                time.sleep(5)
        except KeyboardInterrupt:
            print("User interrupted - shutting down.")
        except Exception as exc:
            print(f"Unhandled error: {exc}")
        finally:
            if self.position is not None:
                self.exit_all("Shutdown")
            self.ib.disconnect()
            print("Disconnected from Interactive Brokers.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SPY Opening Range Breakout strategy")
    parser.add_argument("--ticker", type=str, default="SPY", help="Underlying ticker symbol")
    parser.add_argument("--contracts", type=int, default=2, help="Number of option contracts to trade")
    parser.add_argument("--underlying_move_target", type=float, default=None, help="First profit target (underlying $ move, auto-set if not specified)")
    parser.add_argument("--itm_offset", type=float, default=None, help="ITM offset for SPY/QQQ or second target for others (auto-set if not specified)")
    parser.add_argument("--paper_trading", action="store_true", help="Use paper trading account (7498)")
    parser.add_argument("--port", type=int, default=7498, help="Port number")

    args = parser.parse_args()

    # Handle None values for auto-configuration
    itm_offset = args.itm_offset if args.itm_offset is not None else 1.05

    strategy = SPYORBStrategy(
        ticker=args.ticker,
        contracts=args.contracts,
        underlying_move_target=args.underlying_move_target,  # Can be None for auto-config
        itm_offset=itm_offset,
        paper_trading=args.paper_trading,
        port=args.port,
    )
    strategy.run() 
