#!/usr/bin/env python
# SPY EMA CHAD: SPY EMA Crossover Highly Automated Dealer
# An automated trading system for options trading based on EMA and VWAP

import pandas as pd
import numpy as np
import datetime
import time
import pytz
from ib_insync import *

class SPYEMAChad:
    def __init__(self, ticker="SPY", profit_target=1.0, market_open="08:30:00", 
                 market_close="15:00:00", signal_time="09:00:00", force_close_time="15:50:00",
                 timeframe="5 mins", ema_short=9, ema_long=20, paper_trading=True, threshold=0.0003, trading_time=5):
        """
        Initialize the trading strategy with parameters
        
        Args:
            ticker (str): The ticker symbol to trade
            profit_target (float): Target profit in dollars per contract
            market_open (str): Market open time in HH:MM:SS format
            market_close (str): Market close time in HH:MM:SS format
            signal_time (str): Time to check initial conditions (9:00 AM)
            force_close_time (str): Time to force close any open positions
            timeframe (str): Chart timeframe
            ema_short (int): Short EMA period
            ema_long (int): Long EMA period
            paper_trading (bool): Whether to use paper trading
            threshold (float): Threshold for entry conditions
            trading_time (int): Time in minutes to trade
        """
        self.ticker = ticker
        self.profit_target = profit_target
        self.market_open = market_open
        self.market_close = market_close
        self.signal_time = signal_time
        self.force_close_time = force_close_time
        self.timeframe = timeframe
        self.ema_short = ema_short
        self.ema_long = ema_long
        self.paper_trading = paper_trading
        self.threshold = threshold
        self.trading_time = trading_time
        # Initialize trading state
        self.position = None  # None, "LONG", or "SHORT"
        self.entry_price = 0
        self.stop_loss = 0
        self.today_trade_taken = False
        self.waiting_for_entry = False
        self.initial_condition = None  # "ABOVE", "BELOW", or None
        self.option = None
        
        # Time zone for US Eastern Time
        self.tz = pytz.timezone('US/Eastern')
        
        # Connect to Interactive Brokers
        self.ib = IB()
    
    def connect_to_ib(self, host='127.0.0.1', port=7497, client_id=1, max_retries=3):
        """Connect to Interactive Brokers TWS or Gateway"""
        port_to_use = 7498 if self.paper_trading else 7496
        
        for attempt in range(max_retries):
            try:
                # Disconnect if already connected
                if self.ib.isConnected():
                    self.ib.disconnect()
                    time.sleep(1)
                
                self.ib.connect(host, port_to_use, clientId=client_id)
                print(f"Connected to Interactive Brokers {'Paper' if self.paper_trading else 'Live'} Trading")
                return True
            except Exception as e:
                print(f"Failed to connect to Interactive Brokers (attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(2)
                
        print("Failed to connect after maximum retries")
        return False
    
    def get_contract(self):
        """Get the contract for the specified ticker"""
        print("Getting contract for", self.ticker)
        contract = Stock(self.ticker, 'SMART', 'USD')
        return contract

    def get_spy_option_contract(self, option_type='C'):
        """Get the 0DTE option contract for SPY
        Args:
            option_type (str): 'C' for Call, 'P' for Put
        """
        print("Getting 0DTE option contract for SPY")
        # Get current date
        today = datetime.datetime.now(self.tz).date()
        
        # Get next Friday (0DTE)
        days_until_friday = (4 - today.weekday()) % 7
        expiry = today + datetime.timedelta(days=days_until_friday)
        
        # Format expiry as YYYYMMDD
        expiry_str = expiry.strftime("%Y%m%d")
        
        # Determine ATM strike price
        stock_contract = self.get_contract()
        ticker = self.ib.reqTickers(stock_contract)[0]
        spot_price = ticker.marketPrice()
        strike_price = round(spot_price)

        # Create option contract at ATM strike
        option_contract = Option(symbol=self.ticker,
                                 lastTradeDateOrContractMonth=expiry_str,
                                 strike=strike_price,
                                 right=option_type,
                                 exchange='SMART',
                                 multiplier='100',
                                 currency='USD')
        details = self.ib.reqContractDetails(option_contract)
        if details:
            option_contract = details[0].contract
            self.ib.qualifyContracts(option_contract)
        print(option_contract)
        return option_contract
    
    def get_historical_data(self, duration='1 D', bar_size='5 mins', max_retries=3):
        """Get historical data for calculations"""
        contract = self.get_contract()
        
        for attempt in range(max_retries):
            try:
                bars = self.ib.reqHistoricalData(
                    contract,
                    endDateTime='',
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow='TRADES',
                    useRTH=True,
                    formatDate=1
                )
                
                if bars and len(bars) > 0:
                    df = util.df(bars)
                    return df
                else:
                    print(f"Warning: No historical data received (attempt {attempt+1}/{max_retries})")
                    time.sleep(2)  # Wait before retry
            except Exception as e:
                print(f"Error getting historical data (attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(2)  # Wait before retry
                
        print("Failed to get historical data after maximum retries")
        return None
    
    def calculate_indicators(self, df):
        print("Calculating indicators")
        """Calculate EMA and VWAP indicators"""
        # Calculate EMAs
        df['ema_short'] = df['close'].ewm(span=self.ema_short, adjust=False).mean()
        df['ema_long'] = df['close'].ewm(span=self.ema_long, adjust=False).mean()
        
        # Calculate VWAP
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        df['volume_x_price'] = df['typical_price'] * df['volume']
        
        # Group by day and calculate VWAP
        df['date'] = pd.to_datetime(df['date'])
        df['day'] = df['date'].dt.date
        
        # Calculate cumulative values for each day
        df['cum_vol'] = df.groupby('day')['volume'].cumsum()
        df['cum_vol_price'] = df.groupby('day')['volume_x_price'].cumsum()
        
        # Calculate VWAP
        df['vwap'] = df['cum_vol_price'] / df['cum_vol']
        
        return df
    
    def check_initial_condition(self, df, df_5):
        """
        Check the initial condition at 9:00 AM
        Returns:
            str: "ABOVE", "BELOW", or None
        """    
        try:
            price = df
            ema_short = df_5.iloc[-1]['ema_short']
            ema_long = df_5.iloc[-1]['ema_long']
            vwap = df_5.iloc[-1]['vwap']
            print(df_5.iloc[-1])
            
            # Check conditions
            if price > ema_short and price > ema_long and price > vwap:
                return "ABOVE"
            elif price < ema_short and price < ema_long and price < vwap:
                return "BELOW"
            else:
                return None
                
        except Exception as e:
            print(f"Error calculating initial conditions: {str(e)}")
            return None
    
    def check_for_entry(self, current_price, ema_short_price):
        """
        Check if entry conditions are met
        Args:
            current_price (float): Current price
            ema_short_price (float): Current 9 EMA price
        Returns:
            bool: True if entry conditions are met
        """
        # If we're waiting for entry and price touches the 9 EMA
        if not self.waiting_for_entry:
            return False
        
        # Allow for some small difference (0.01% of price)
        touch_threshold = current_price * self.threshold
        
        if (self.initial_condition == "ABOVE" and 
            abs(current_price - ema_short_price) < touch_threshold):
            return True
        elif (self.initial_condition == "BELOW" and 
              abs(current_price - ema_short_price) < touch_threshold):
            return True
        return False
    
    def check_stop_loss(self, cp, df):
        """
        Check if stop loss conditions are met
        Returns:
            bool: True if stop loss should be triggered
        """
        if self.position is None:
            return False
        
        # Get the last completed candle
        last_candle = df.iloc[-2]
        
        price = cp
        ema_short = last_candle['ema_short']
        ema_long = last_candle['ema_long']
        vwap = last_candle['vwap']
        
        # Check stop loss conditions
        if (self.position == "LONG" and 
            price < ema_short and price < ema_long and price < vwap):
            return True
        elif (self.position == "SHORT" and 
              price > ema_short and price > ema_long and price > vwap):
            return True
        return False
    
    def place_order(self, action, quantity=1):
        """
        Place an order with Interactive Brokers
        Args:
            action (str): "BUY" or "SELL"
            quantity (int): Number of contracts
        """
        option_type = 'C' if self.position == "LONG" else 'P'
        if self.option is None:
            self.option = self.get_spy_option_contract(option_type)
        contract = self.option
        order = MarketOrder(action, quantity)
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(1)  # Give IB time to process the order
        
        print(f"{datetime.datetime.now(self.tz)}: {action} order placed for {quantity} {self.ticker}")
        return trade
    
    def enter_position(self, direction):
        """
        Enter a position (long or short)
        Args:
            direction (str): "LONG" or "SHORT"
        """
        if direction == "LONG":
            self.position = "LONG"
            self.place_order("BUY")
        else:
            self.position = "SHORT"
            self.place_order("SELL")
        
        # Get current price for tracking profit/loss
        ticker = self.ib.reqTickers(self.get_contract())[0]
        self.entry_price = ticker.marketPrice()
        
        self.today_trade_taken = True
        self.waiting_for_entry = False
        
        print(f"{datetime.datetime.now(self.tz)}: Entered {direction} position at ${self.entry_price:.2f}")
    
    def exit_position(self, reason=""):
        """Exit current position"""
        if self.position == "LONG":
            self.place_order("SELL")
        elif self.position == "SHORT":
            self.place_order("BUY")
        
        ticker = self.ib.reqTickers(self.get_contract())[0]
        exit_price = ticker.marketPrice()
        profit = exit_price - self.entry_price if self.position == "LONG" else self.entry_price - exit_price
        
        print(f"{datetime.datetime.now(self.tz)}: Exited {self.position} position at ${exit_price:.2f}, "
              f"P/L: ${profit:.2f} ({reason})")
        
        self.position = None
        self.entry_price = 0
    
    def check_profit_target(self):
        """Check if profit target has been reached"""
        if self.position is None:
            return False
        
        ticker = self.ib.reqTickers(self.get_contract())[0]
        current_price = ticker.marketPrice()
        
        if (self.position == "LONG" and 
            current_price - self.entry_price >= self.profit_target):
            return True
        elif (self.position == "SHORT" and 
              self.entry_price - current_price >= self.profit_target):
            return True
        return False
    
    def reset_daily_state(self):
        """Reset daily trading state"""
        self.today_trade_taken = False
        self.waiting_for_entry = False
        self.initial_condition = None
    
    def is_market_open(self):
        """Check if the market is currently open"""
        now = datetime.datetime.now(self.tz)
        
        # Convert times to datetime objects for today
        today = now.date()
        market_open_time = datetime.datetime.combine(today, 
                                                    datetime.datetime.strptime(self.market_open, "%H:%M:%S").time())
        
        market_close_time = datetime.datetime.combine(today, 
                                                     datetime.datetime.strptime(self.market_close, "%H:%M:%S").time())
        
        # Localize times if needed
        if market_open_time.tzinfo is None:
            market_open_time = self.tz.localize(market_open_time)
            
        if market_close_time.tzinfo is None:
            market_close_time = self.tz.localize(market_close_time)
        
        # Check if current time is within market hours
        return market_open_time <= now <= market_close_time
    
    def is_force_close_time(self):
        """Check if it's time to force close any open positions"""
        now = datetime.datetime.now(self.tz)
        
        # Convert force close time to datetime object for today
        today = now.date()
        force_close_time = datetime.datetime.combine(today, 
                                                    datetime.datetime.strptime(self.force_close_time, "%H:%M:%S").time())
        # Only localize if not already localized
        if force_close_time.tzinfo is None:
            force_close_time = self.tz.localize(force_close_time)
        
        # Check if current time is at or past force close time
        return now >= force_close_time
    
    def run(self):
        """Main trading loop"""
        # Connect to Interactive Brokers
        if not self.connect_to_ib():
            return
        
        try:
            print(f"Starting SPY EMA CHAD trading strategy for {self.ticker}")
            while True:
                # Check if market is open
                if not self.is_market_open():
                    if self.position is not None:
                        print("Market closed with position still open. Closing position.")
                        self.exit_position("Market closed")
                    
                    # Reset daily state at the end of the day
                    if datetime.datetime.now(self.tz).time() > datetime.datetime.strptime(self.market_close, "%H:%M:%S").time():
                        self.reset_daily_state()
                        
                    print("Market closed. Waiting for next market open.")
                    time.sleep(60)  # Check again in 1 minute
                    continue
                
                # Get current data
                df = self.get_historical_data()
                print(f"Historical data here: {df}")
                if df is None or len(df) == 0:
                    print("Unable to retrieve market data. Waiting before retry...")
                    time.sleep(60)  # Wait a minute before trying again
                    continue
                df = self.calculate_indicators(df)
                print(f"Indicators here: {df}")

                # Check for force close time
                if self.is_force_close_time() and self.position is not None:
                    print("Force close time reached. Closing position.")
                    self.exit_position("Force close time")
                    time.sleep(60)  # Wait until next day
                    continue
                
                # New day check
                now = datetime.datetime.now(self.tz)
                current_time = now.time()
                signal_time = datetime.datetime.strptime(self.signal_time, "%H:%M:%S").time()
                tickers = self.ib.reqTickers(self.get_contract())
                print(tickers)
                if not tickers:
                    print("No market data available. Delayed data or no subscription.")
                    return None  # or raise a custom error, or use a fallback
                current_price = tickers[0].marketPrice()

                
                
                # Around 9:00 AM, check initial conditions if we haven't done so today
                if (abs((current_time.hour * 60 + current_time.minute) - 
                        (signal_time.hour * 60 + signal_time.minute)) < self.trading_time and 
                    not self.today_trade_taken and not self.waiting_for_entry):
                    
                    self.initial_condition = self.check_initial_condition(df=current_price, df_5=df)
                    
                    if self.initial_condition == "ABOVE":
                        print(f"{now}: Initial condition: Price ABOVE all indicators. Waiting for price to touch 9 EMA for LONG entry.")
                        self.waiting_for_entry = True
                    elif self.initial_condition == "BELOW":
                        print(f"{now}: Initial condition: Price BELOW all indicators. Waiting for price to touch 9 EMA for SHORT entry.")
                        self.waiting_for_entry = True
                    else:
                        print(f"{now}: Initial condition: Price between indicators. No trade today.")
                        self.today_trade_taken = True  # No trade today
                
                # Check for entry if we're waiting
                if self.waiting_for_entry:
                    # Determine option type based on initial condition
                    # Get current 9 EMA value
                    latest_data = df.iloc[-1]
                    ema_short_price = latest_data['ema_short']
                    
                    if self.check_for_entry(current_price, ema_short_price):
                        # Enter position
                        entry_direction = "LONG" if self.initial_condition == "ABOVE" else "SHORT"
                        self.enter_position(entry_direction)
                
                # Check for exit conditions if in a position
                if self.position is not None:
                    # Check profit target
                    if self.check_profit_target():
                        self.exit_position("Profit target reached")
                        continue
                    
                    # Check stop loss
                    if self.check_stop_loss(current_price, df):
                        self.exit_position("Stop loss triggered")
                        continue
                
                # Sleep for a short time before checking again
                time.sleep(1)  # Check every 1 seconds
                
        except KeyboardInterrupt:
            print("Strategy stopped by user.")
        except Exception as e:
            print(f"Error in trading strategy: {e}")
        finally:
            if self.position is not None:
                self.exit_position("Strategy shutdown")
            self.ib.disconnect()
            print("Disconnected from Interactive Brokers")

if __name__ == "__main__":
    import argparse
    
    # Create argument parser
    parser = argparse.ArgumentParser(description='SPY EMA CHAD Trading Strategy')
    
    # Add arguments
    parser.add_argument('--ticker', type=str, default='SPY', help='Ticker symbol to trade')
    parser.add_argument('--profit_target', type=float, default=1.0, help='Target profit in dollars per contract')
    parser.add_argument('--market_open', type=str, default='08:30:00', help='Market open time (HH:MM:SS)')
    parser.add_argument('--market_close', type=str, default='15:00:00', help='Market close time (HH:MM:SS)')
    parser.add_argument('--signal_time', type=str, default='09:00:00', help='Time to check initial conditions (HH:MM:SS)')
    parser.add_argument('--force_close_time', type=str, default='15:50:00', help='Time to force close positions (HH:MM:SS)')
    parser.add_argument('--timeframe', type=str, default='5 mins', help='Chart timeframe')
    parser.add_argument('--ema_short', type=int, default=9, help='Short EMA period')
    parser.add_argument('--ema_long', type=int, default=20, help='Long EMA period')
    parser.add_argument('--paper_trading', action='store_true', help='Use paper trading')
    parser.add_argument('--threshold', type=float, default=0.0003, help='Threshold for entry conditions')
    parser.add_argument('--trading_time', type=int, default=500, help='Time in minutes to trade')
    
    # Parse arguments
    args = parser.parse_args()
    
    # Create and run the trading strategy
    strategy = SPYEMAChad(
        ticker=args.ticker,
        profit_target=args.profit_target,
        market_open=args.market_open,
        market_close=args.market_close,
        signal_time=args.signal_time,
        force_close_time=args.force_close_time,
        timeframe=args.timeframe,
        ema_short=args.ema_short,
        ema_long=args.ema_long,
        paper_trading=True,
        threshold=args.threshold,
        trading_time=args.trading_time
    )
    strategy.run()