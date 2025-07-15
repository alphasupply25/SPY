#!/usr/bin/env python
# SPY EMA CHAD Multi-Ticker Trading Script
# Runs the strategy on multiple tickers simultaneously

import sys
import threading
import time
from ib_insync import *
from spy_ema_chad import SPYEMAChad
from options_trading import OptionsTrader

class MultiTickerTrader:
    def __init__(self, tickers=None, use_options=False, paper_trading=True):
        """
        Initialize the multi-ticker trading system
        
        Args:
            tickers (list): List of ticker symbols to trade
            use_options (bool): Whether to trade options or stock
            paper_trading (bool): Whether to use paper trading
        """
        if tickers is None:
            tickers = ["SPY"]  # Default to SPY
        
        self.tickers = tickers
        self.use_options = use_options
        self.paper_trading = paper_trading
        self.strategies = {}
        self.threads = {}
        self.ib = IB()
    
    def connect_to_ib(self, host='127.0.0.1', port=None, client_id=1):
        """Connect to Interactive Brokers TWS or Gateway"""
        if port is None:
            port = 7498 if self.paper_trading else 7498
            
        try:
            self.ib.connect(host, port, clientId=client_id)
            print(f"Connected to Interactive Brokers {'Paper' if self.paper_trading else 'Live'} Trading")
            return True
        except Exception as e:
            print(f"Failed to connect to Interactive Brokers: {e}")
            return False
    
    def create_strategies(self):
        """Create strategy instances for each ticker"""
        for idx, ticker in enumerate(self.tickers):
            # Use different client IDs for each strategy
            client_id = 1 + idx
            
            # Create the appropriate strategy object
            if self.use_options:
                strategy = OptionsTrader(
                    ticker=ticker,
                    profit_target=100.0,  # Adjust as needed
                    paper_trading=self.paper_trading,
                    option_type="call",   # Default to calls
                    dte_target=14,        # 2 weeks out
                    strike_offset=0,      # ATM
                    contracts=1           # 1 contract
                )
            else:
                strategy = SPYEMAChad(
                    ticker=ticker,
                    profit_target=1.0,    # $1 for stocks
                    paper_trading=self.paper_trading
                )
            
            self.strategies[ticker] = strategy
    
    def run_strategy(self, ticker):
        """Run a single strategy in its own thread"""
        strategy = self.strategies[ticker]
        
        try:
            print(f"Starting strategy for {ticker}")
            strategy.run()
        except Exception as e:
            print(f"Error in {ticker} strategy: {e}")
        finally:
            print(f"Strategy for {ticker} has stopped")
    
    def start_all(self):
        """Start all strategies in separate threads"""
        if not self.connect_to_ib():
            print("Failed to connect to IB. Exiting.")
            return
        
        # Create strategies if not already created
        if not self.strategies:
            self.create_strategies()
        
        # Start a thread for each strategy
        for ticker in self.tickers:
            thread = threading.Thread(target=self.run_strategy, args=(ticker,))
            thread.daemon = True
            thread.start()
            self.threads[ticker] = thread
            
            # Small delay to avoid overwhelming IB connection
            time.sleep(1)
        
        print(f"Started trading for {len(self.tickers)} tickers: {', '.join(self.tickers)}")
        
        # Wait for all threads to complete (which they won't unless there's an error)
        try:
            while True:
                active_threads = [ticker for ticker, thread in self.threads.items() if thread.is_alive()]
                if not active_threads:
                    break
                print(f"Active strategies: {', '.join(active_threads)}")
                time.sleep(60)
        except KeyboardInterrupt:
            print("Stopping all strategies...")
        finally:
            self.ib.disconnect()
            print("Disconnected from Interactive Brokers")
    
    def stop_all(self):
        """Stop all running strategies"""
        # There's no clean way to stop the threads, but we can disconnect IB
        # which will cause the strategies to exit
        if self.ib.isConnected():
            self.ib.disconnect()
            print("Disconnected from Interactive Brokers")

def main():
    # Example tickers - users can modify this list
    tickers = ["SPY", "QQQ", "IWM", "DIA"]
    
    # Parse command line arguments
    use_options = "--options" in sys.argv
    paper_trading = not ("--live" in sys.argv)
    
    if "--tickers" in sys.argv:
        idx = sys.argv.index("--tickers")
        if idx + 1 < len(sys.argv):
            tickers = sys.argv[idx + 1].split(",")
    
    print(f"Trading {'options' if use_options else 'stocks'} for: {', '.join(tickers)}")
    print(f"Mode: {'Paper Trading' if paper_trading else 'LIVE TRADING'}")
    
    if not paper_trading:
        confirm = input("WARNING: You are about to start LIVE trading. Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            print("Live trading not confirmed. Exiting.")
            return
    
    # Create and start the multi-ticker trader
    trader = MultiTickerTrader(
        tickers=tickers,
        use_options=use_options,
        paper_trading=paper_trading
    )
    trader.start_all()

if __name__ == "__main__":
    main() 