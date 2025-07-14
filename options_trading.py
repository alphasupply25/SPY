#!/usr/bin/env python
# SPY EMA CHAD Options Trading Module
# Extension to trade options instead of stocks

import datetime
from ib_insync import *
from spy_ema_chad import SPYEMAChad

class OptionsTrader(SPYEMAChad):
    def __init__(self, ticker="SPY", profit_target=1.0, market_open="08:30:00",
                 market_close="16:00:00", signal_time="09:00:00", force_close_time="15:50:00",
                 timeframe="5 mins", ema_short=9, ema_long=20, paper_trading=True,
                 option_type="call", dte_target=14, strike_offset=0, contracts=1):
        """
        Initialize the options trading strategy
        
        Args:
            ticker (str): The underlying ticker symbol
            profit_target (float): Target profit in dollars per contract
            market_open (str): Market open time in HH:MM:SS format
            market_close (str): Market close time in HH:MM:SS format
            signal_time (str): Time to check initial conditions (9:00 AM)
            force_close_time (str): Time to force close any open positions
            timeframe (str): Chart timeframe
            ema_short (int): Short EMA period
            ema_long (int): Long EMA period
            paper_trading (bool): Whether to use paper trading
            option_type (str): "call" or "put"
            dte_target (int): Target days to expiration
            strike_offset (int): Offset from ATM in strike prices
            contracts (int): Number of contracts to trade
        """
        # Initialize parent class
        super().__init__(ticker, profit_target, market_open, market_close, signal_time,
                         force_close_time, timeframe, ema_short, ema_long, paper_trading)
        
        # Options-specific parameters
        self.option_type = option_type
        self.dte_target = dte_target
        self.strike_offset = strike_offset
        self.contracts = contracts
        self.current_option_contract = None
    
    def get_stock_price(self):
        """Get current price of underlying stock"""
        contract = Stock(self.ticker, 'SMART', 'USD')
        ticker = self.ib.reqTickers(contract)[0]
        return ticker.marketPrice()
    
    def find_option_contract(self, direction):
        """
        Find appropriate option contract based on trading direction
        
        Args:
            direction (str): "LONG" or "SHORT" indicating trading direction
        
        Returns:
            Option: IB option contract
        """
        # Get current stock price
        stock_price = self.get_stock_price()
        
        # Get appropriate option type based on direction
        # For LONG position, we want to buy calls
        # For SHORT position, we want to buy puts
        option_right = "C" if (direction == "LONG" and self.option_type == "call") or \
                               (direction == "SHORT" and self.option_type == "put") else "P"
        
        # Find the closest strike price to the current stock price
        atm_strike = round(stock_price / 0.5) * 0.5  # Round to nearest 0.5
        
        # Apply strike offset (can be positive or negative)
        if (direction == "LONG" and option_right == "C") or (direction == "SHORT" and option_right == "P"):
            # For long calls or short puts, we might want slightly OTM options
            target_strike = atm_strike + self.strike_offset * 0.5
        else:
            # For long puts or short calls, we might want slightly ITM options
            target_strike = atm_strike - self.strike_offset * 0.5
        
        # Find appropriate expiration date (closest to target DTE)
        today = datetime.date.today()
        target_date = today + datetime.timedelta(days=self.dte_target)
        
        # Get list of available option chains
        chains = self.ib.reqSecDefOptParams(self.ticker, '', "STK", 8314)  # 8314 is the exchange code for SMART
        
        # Find appropriate expiration and strike
        best_expiration = None
        smallest_dte_diff = float('inf')
        
        for chain in chains:
            for expiration in chain.expirations:
                exp_date = datetime.datetime.strptime(expiration, '%Y%m%d').date()
                dte_diff = abs((exp_date - today).days - self.dte_target)
                
                if dte_diff < smallest_dte_diff:
                    smallest_dte_diff = dte_diff
                    best_expiration = expiration
        
        if not best_expiration:
            raise ValueError(f"No suitable options found for {self.ticker} with DTE ~{self.dte_target}")
        
        # Find the closest strike to our target
        contract = Option(self.ticker, best_expiration, target_strike, option_right, 'SMART')
        
        print(f"Selected option: {self.ticker} {best_expiration} {target_strike} {option_right}")
        return contract
    
    def get_contract(self):
        """Override get_contract to return the current option contract"""
        if self.current_option_contract:
            return self.current_option_contract
        else:
            # Return the stock contract for initial data analysis
            return Stock(self.ticker, 'SMART', 'USD')
    
    def enter_position(self, direction):
        """
        Enter an options position based on the specified direction
        
        Args:
            direction (str): "LONG" or "SHORT" indicating trading direction
        """
        # Find appropriate option contract
        self.current_option_contract = self.find_option_contract(direction)
        
        # Place order
        action = "BUY"  # We're always buying options (calls for LONG, puts for SHORT)
        order = MarketOrder(action, self.contracts)
        trade = self.ib.placeOrder(self.current_option_contract, order)
        self.ib.sleep(1)  # Give IB time to process the order
        
        # Get current option price for tracking profit/loss
        ticker = self.ib.reqTickers(self.current_option_contract)[0]
        self.entry_price = ticker.marketPrice()
        
        self.position = direction
        self.today_trade_taken = True
        self.waiting_for_entry = False
        
        print(f"{datetime.datetime.now(self.tz)}: Entered {direction} position via options at ${self.entry_price:.2f}")
    
    def exit_position(self, reason=""):
        """Exit current options position"""
        if not self.current_option_contract:
            print("No position to exit")
            return
        
        # Place order to sell the option
        action = "SELL"  # We're always selling our options to exit
        order = MarketOrder(action, self.contracts)
        trade = self.ib.placeOrder(self.current_option_contract, order)
        self.ib.sleep(1)
        
        # Get exit price
        ticker = self.ib.reqTickers(self.current_option_contract)[0]
        exit_price = ticker.marketPrice()
        profit = (exit_price - self.entry_price) * 100 * self.contracts  # Each option is for 100 shares
        
        print(f"{datetime.datetime.now(self.tz)}: Exited {self.position} position at ${exit_price:.2f}, "
              f"P/L: ${profit:.2f} ({reason})")
        
        self.position = None
        self.entry_price = 0
        self.current_option_contract = None
    
    def check_profit_target(self):
        """Check if profit target has been reached for options"""
        if self.position is None or self.current_option_contract is None:
            return False
        
        ticker = self.ib.reqTickers(self.current_option_contract)[0]
        current_price = ticker.marketPrice()
        
        # Calculate the dollar profit per contract (not per share)
        profit_per_contract = (current_price - self.entry_price) * 100
        
        if profit_per_contract >= self.profit_target:
            return True
        return False

if __name__ == "__main__":
    # Create and run the options trading strategy
    strategy = OptionsTrader(
        ticker="SPY",
        profit_target=100.0,  # $100 profit target per contract
        paper_trading=True,
        option_type="call",   # "call" or "put" based on strategy preference
        dte_target=14,        # Target 14 days to expiration
        strike_offset=0,      # ATM options (0 offset)
        contracts=1           # Trade 1 contract
    )
    strategy.run() 