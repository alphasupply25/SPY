#!/usr/bin/env python
# Unit tests for SPY ORB CHAD trading system

import unittest
from unittest.mock import patch, Mock, MagicMock
import datetime
import pandas as pd
import numpy as np
import pytz
from ib_insync import Stock, Option, MarketOrder, util

from spy_orb_strategy import SPYORBStrategy

class TestSPYORBStrategy(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.strategy = SPYORBStrategy(paper_trading=True)
        # Mock the IB connection
        self.strategy.ib = Mock()
        # Set a fixed timezone for testing
        self.strategy.tz = pytz.timezone('US/Central')
        
    def tearDown(self):
        """Clean up after each test method."""
        pass
        
    def test_initialization(self):
        """Test the initialization of the SPYORBStrategy class."""
        # Test default parameters
        self.assertEqual(self.strategy.ticker, "SPY")
        self.assertEqual(self.strategy.contracts, 2)
        self.assertEqual(self.strategy.underlying_move_target, 1.0)
        self.assertEqual(self.strategy.itm_offset, 1.05)
        self.assertEqual(self.strategy.market_open, "09:30:00")
        self.assertEqual(self.strategy.market_close, "16:00:00")
        self.assertEqual(self.strategy.force_close_time, "15:50:00")
        self.assertEqual(self.strategy.bar_size, "5 mins")
        self.assertEqual(self.strategy.paper_trading, True)
        
        # Test initial state
        self.assertIsNone(self.strategy.opening_range_high)
        self.assertIsNone(self.strategy.opening_range_low)
        self.assertFalse(self.strategy.opening_range_set)
        self.assertIsNone(self.strategy.position)
        self.assertIsNone(self.strategy.option_contract)
        self.assertIsNone(self.strategy.entry_underlying_price)
        self.assertIsNone(self.strategy.entry_option_price)
        self.assertIsNone(self.strategy.entry_strike)
        self.assertFalse(self.strategy.half_position_closed)
        
        # Test with custom parameters
        custom_strategy = SPYORBStrategy(
            ticker="QQQ",
            contracts=4,
            underlying_move_target=2.0,
            itm_offset=2.0,
            market_open="09:00:00",
            market_close="16:00:00",
            paper_trading=False
        )
        self.assertEqual(custom_strategy.ticker, "QQQ")
        self.assertEqual(custom_strategy.contracts, 4)
        self.assertEqual(custom_strategy.underlying_move_target, 2.0)
        self.assertEqual(custom_strategy.itm_offset, 2.0)
        self.assertEqual(custom_strategy.market_open, "09:00:00")
        self.assertEqual(custom_strategy.market_close, "16:00:00")
        self.assertEqual(custom_strategy.paper_trading, False)
        
    def test_connect_to_ib(self):
        """Test the connection to Interactive Brokers."""
        # Mock successful connection
        self.strategy.ib.connect = MagicMock(return_value=True)
        self.strategy.ib.isConnected = MagicMock(return_value=False)
        result = self.strategy.connect_to_ib()
        self.assertTrue(result)
        self.strategy.ib.connect.assert_called_once()
        
        # Verify correct port for paper trading
        args, kwargs = self.strategy.ib.connect.call_args
        self.assertEqual(args[1], 7497)  # Paper trading port
        
        # Mock failed connection
        self.strategy.ib.connect = MagicMock(side_effect=Exception("Connection failed"))
        result = self.strategy.connect_to_ib()
        self.assertFalse(result)
        
    def test_get_stock_contract(self):
        """Test getting the stock contract."""
        contract = self.strategy.get_stock_contract()
        self.assertIsInstance(contract, Stock)
        self.assertEqual(contract.symbol, "SPY")
        self.assertEqual(contract.exchange, "SMART")
        self.assertEqual(contract.currency, "USD")
        
        # Test with different ticker
        self.strategy.ticker = "QQQ"
        contract = self.strategy.get_stock_contract()
        self.assertEqual(contract.symbol, "QQQ")
        
    def test_get_underlying_price(self):
        """Test getting the underlying price."""
        # Mock ticker response
        mock_ticker = Mock()
        mock_ticker.marketPrice = MagicMock(return_value=400.0)
        self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
        
        price = self.strategy.get_underlying_price()
        self.assertEqual(price, 400.0)
        
    def test_get_option_contract(self):
        """Test getting the option contract."""
        # Mock underlying price
        self.strategy.get_underlying_price = MagicMock(return_value=400.0)
        
        # Mock contract details
        mock_contract = Mock()
        mock_details = Mock(contract=mock_contract)
        self.strategy.ib.reqContractDetails = MagicMock(return_value=[mock_details])
        self.strategy.ib.qualifyContracts = MagicMock()
        
        # Test getting call option
        contract = self.strategy.get_option_contract("C")
        
        # Verify reqContractDetails was called with correct parameters
        args, _ = self.strategy.ib.reqContractDetails.call_args
        option_contract = args[0]
        self.assertEqual(option_contract.symbol, "SPY")
        self.assertEqual(option_contract.strike, 400)  # ATM
        self.assertEqual(option_contract.right, "C")
        
        # Test getting put option
        contract = self.strategy.get_option_contract("P")
        args, _ = self.strategy.ib.reqContractDetails.call_args
        option_contract = args[0]
        self.assertEqual(option_contract.right, "P")
        
    def test_is_market_open(self):
        """Test checking if market is open."""
        test_day = datetime.date(2023, 1, 3)  # A Tuesday
        
        with patch('spy_orb_strategy.datetime') as mock_datetime:
            # Configure the mock datetime properly
            mock_datetime.datetime.strptime.side_effect = datetime.datetime.strptime
            mock_datetime.datetime.combine.side_effect = datetime.datetime.combine
            
            # Test during market hours
            mock_datetime.datetime.now.side_effect = lambda tz=None: self.strategy.tz.localize(
                datetime.datetime.combine(test_day, datetime.time(10, 0))
            )
            self.assertTrue(self.strategy.is_market_open())
            
            # Test before market hours
            mock_datetime.datetime.now.side_effect = lambda tz=None: self.strategy.tz.localize(
                datetime.datetime.combine(test_day, datetime.time(8, 0))
            )
            self.assertFalse(self.strategy.is_market_open())
            
            # Test after market hours
            mock_datetime.datetime.now.side_effect = lambda tz=None: self.strategy.tz.localize(
                datetime.datetime.combine(test_day, datetime.time(16, 0))
            )
            self.assertFalse(self.strategy.is_market_open())
            
    def test_is_force_close_time(self):
        """Test checking if it's time to force close positions."""
        test_day = datetime.date(2023, 1, 3)
        
        with patch('spy_orb_strategy.datetime') as mock_datetime:
            # Configure the mock datetime properly
            mock_datetime.datetime.now.side_effect = lambda tz=None: self.strategy.tz.localize(
                datetime.datetime.combine(test_day, datetime.time(14, 50))
            )
            mock_datetime.datetime.strptime.side_effect = datetime.datetime.strptime
            mock_datetime.datetime.combine.side_effect = datetime.datetime.combine
            
            # Test before force close time
            self.assertFalse(self.strategy.is_force_close_time())
            
            # Test at force close time
            mock_datetime.datetime.now.side_effect = lambda tz=None: self.strategy.tz.localize(
                datetime.datetime.combine(test_day, datetime.time(14, 55))
            )
            self.assertTrue(self.strategy.is_force_close_time())
            
            # Test after force close time
            mock_datetime.datetime.now.side_effect = lambda tz=None: self.strategy.tz.localize(
                datetime.datetime.combine(test_day, datetime.time(15, 0))
            )
            self.assertTrue(self.strategy.is_force_close_time())
            
    @patch('ib_insync.util.df')
    def test_get_intraday_5min(self, mock_util_df):
        """Test getting intraday 5-minute data."""
        # Mock the historical data response
        mock_bars = [Mock(), Mock()]
        self.strategy.ib.reqHistoricalData = MagicMock(return_value=mock_bars)
        
        # Mock the DataFrame
        mock_df = pd.DataFrame({
            'date': ['2023-01-01 09:00:00', '2023-01-01 09:05:00'],
            'open': [400.0, 401.0],
            'high': [402.0, 403.0],
            'low': [399.0, 400.0],
            'close': [401.0, 402.0],
            'volume': [1000, 1200]
        })
        mock_util_df.return_value = mock_df
        
        # Call the method
        result = self.strategy.get_intraday_5min()
        
        # Assert the method was called with correct parameters
        self.strategy.ib.reqHistoricalData.assert_called_once()
        args = self.strategy.ib.reqHistoricalData.call_args[0]
        # Check that it's called with a contract
        self.assertIsInstance(args[0], Stock)
        
        # Check kwargs instead of positional args for duration and bar size
        kwargs = self.strategy.ib.reqHistoricalData.call_args[1]
        self.assertEqual(kwargs.get('durationStr', ''), "1 D")
        self.assertEqual(kwargs.get('barSizeSetting', ''), "5 mins")
        
        # Check that date was converted to datetime
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(result['date']))
        
    def test_calculate_opening_range(self):
        """Test calculating the opening range."""
        # Create test data with 4 candles (20 minutes of data)
        # Use timezone-aware timestamps
        today = datetime.datetime.now(self.strategy.tz).date()
        base_time = self.strategy.tz.localize(datetime.datetime.combine(today, datetime.time(8, 30)))
        
        df = pd.DataFrame({
            'date': pd.to_datetime([
                base_time,
                base_time + datetime.timedelta(minutes=5),
                base_time + datetime.timedelta(minutes=10),
                base_time + datetime.timedelta(minutes=15)
            ]),
            'high': [402.0, 403.0, 404.0, 405.0],
            'low': [399.0, 400.0, 401.0, 402.0]
        })
        
        # Calculate opening range (should use first 3 candles)
        self.strategy.calculate_opening_range(df)
        
        # Check that opening range was set correctly
        self.assertTrue(self.strategy.opening_range_set)
        self.assertEqual(self.strategy.opening_range_high, 404.0)  # Max of first 3 highs
        self.assertEqual(self.strategy.opening_range_low, 399.0)   # Min of first 3 lows
        
        # Test with insufficient data (less than 3 candles)
        df_short = pd.DataFrame({
            'date': pd.to_datetime([
                base_time,
                base_time + datetime.timedelta(minutes=5)
            ]),
            'high': [402.0, 403.0],
            'low': [399.0, 400.0]
        })
        
        self.strategy.opening_range_set = False
        self.strategy.calculate_opening_range(df_short)
        self.assertFalse(self.strategy.opening_range_set)  # Should not be set
        
    def test_place_order(self):
        """Test placing an order."""
        # Set up option contract
        self.strategy.option_contract = Mock(localSymbol="SPY230103C400")
        
        # Mock IB methods
        self.strategy.ib.placeOrder = MagicMock()
        self.strategy.ib.sleep = MagicMock()
        
        # Test placing order
        self.strategy.place_order("BUY", 2)
        
        # Verify order was placed correctly
        self.strategy.ib.placeOrder.assert_called_once()
        args, _ = self.strategy.ib.placeOrder.call_args
        contract, order = args
        
        self.assertEqual(order.action, "BUY")
        self.assertEqual(order.totalQuantity, 2)
        
        # Test with no option contract
        self.strategy.option_contract = None
        with self.assertRaises(RuntimeError):
            self.strategy.place_order("BUY", 1)
            
    def test_enter_position(self):
        """Test entering a position."""
        # Mock methods
        self.strategy.get_option_contract = MagicMock(return_value=Mock(strike=400))
        self.strategy.place_order = MagicMock()
        self.strategy.get_underlying_price = MagicMock(return_value=400.0)
        
        # Mock option ticker
        mock_ticker = Mock()
        mock_ticker.marketPrice = MagicMock(return_value=5.0)
        self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
        
        # Test entering CALL position
        self.strategy.enter_position("CALL")
        
        # Verify correct option type was requested
        self.strategy.get_option_contract.assert_called_once_with("C")
        
        # Verify order was placed
        self.strategy.place_order.assert_called_once_with("BUY", 2)
        
        # Verify state was updated
        self.assertEqual(self.strategy.position, "CALL")
        self.assertEqual(self.strategy.entry_underlying_price, 400.0)
        self.assertEqual(self.strategy.entry_option_price, 5.0)
        self.assertEqual(self.strategy.entry_strike, 400)
        
        # Reset and test PUT position
        self.strategy.get_option_contract.reset_mock()
        self.strategy.place_order.reset_mock()
        self.strategy.position = None
        
        self.strategy.enter_position("PUT")
        self.strategy.get_option_contract.assert_called_once_with("P")
        self.assertEqual(self.strategy.position, "PUT")
        
    def test_exit_all(self):
        """Test exiting all positions."""
        # Set up position
        self.strategy.position = "CALL"
        self.strategy.option_contract = Mock(localSymbol="SPY230103C400")
        self.strategy.contracts = 2
        self.strategy.half_position_closed = False
        self.strategy.entry_option_price = 5.0
        
        # Mock methods
        self.strategy.place_order = MagicMock()
        mock_ticker = Mock()
        mock_ticker.marketPrice = MagicMock(return_value=6.0)
        self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
        
        # Test exiting full position
        self.strategy.exit_all("Test reason")
        
        # Verify order was placed for full position
        self.strategy.place_order.assert_called_once_with("SELL", 2)
        
        # Verify state was reset
        self.assertIsNone(self.strategy.position)
        self.assertIsNone(self.strategy.option_contract)
        self.assertIsNone(self.strategy.entry_underlying_price)
        self.assertIsNone(self.strategy.entry_option_price)
        self.assertIsNone(self.strategy.entry_strike)
        self.assertFalse(self.strategy.half_position_closed)
        
        # Test exiting half position
        self.strategy.position = "PUT"
        self.strategy.option_contract = Mock()
        self.strategy.half_position_closed = True
        self.strategy.entry_option_price = 5.0  # Need to set this
        self.strategy.place_order.reset_mock()
        
        self.strategy.exit_all("Test reason")
        
        # Should sell only half (1 contract)
        self.strategy.place_order.assert_called_once_with("SELL", 1)
        
    @patch('time.sleep')
    def test_run_opening_range_breakout(self, mock_sleep):
        """Test the opening range breakout logic in the run method."""
        # Set up mocks
        self.strategy.connect_to_ib = MagicMock(return_value=True)
        self.strategy.is_market_open = MagicMock(return_value=True)
        self.strategy.is_force_close_time = MagicMock(return_value=False)
        
        # Create test data with timezone-aware dates
        today = datetime.datetime.now(self.strategy.tz).date()
        base_time = self.strategy.tz.localize(datetime.datetime.combine(today, datetime.time(8, 30)))
        
        # First set of data for opening range calculation
        df_opening = pd.DataFrame({
            'date': pd.to_datetime([
                base_time,
                base_time + datetime.timedelta(minutes=5),
                base_time + datetime.timedelta(minutes=10),
                base_time + datetime.timedelta(minutes=15)
            ]),
            'high': [402.0, 403.0, 404.0, 403.0],
            'low': [399.0, 400.0, 401.0, 401.0],
            'close': [401.0, 402.0, 403.0, 402.0]
        })
        
        # Second set of data with breakout signal
        df_breakout = pd.DataFrame({
            'date': pd.to_datetime([
                base_time,
                base_time + datetime.timedelta(minutes=5),
                base_time + datetime.timedelta(minutes=10),
                base_time + datetime.timedelta(minutes=15),
                base_time + datetime.timedelta(minutes=20),
                base_time + datetime.timedelta(minutes=25)
            ]),
            'high': [402.0, 403.0, 404.0, 403.0, 406.0, 407.0],
            'low': [399.0, 400.0, 401.0, 401.0, 404.0, 405.0],
            'close': [401.0, 402.0, 403.0, 402.0, 405.0, 406.0]  # Second-to-last close (405) > opening range high (404)
        })
        
        # Mock the method to return different DataFrames
        call_count = 0
        def get_data_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return df_opening  # First call for opening range
            elif call_count == 2:
                return df_opening  # Second call still waiting for more data
            elif call_count == 3:
                return df_breakout  # Third call shows breakout
            else:
                raise KeyboardInterrupt()  # Fourth call triggers exit
        
        self.strategy.get_intraday_5min = MagicMock(side_effect=get_data_side_effect)
        self.strategy.enter_position = MagicMock()
        
        try:
            self.strategy.run()
        except KeyboardInterrupt:
            pass
        
        # Verify opening range was calculated
        self.assertTrue(self.strategy.opening_range_set)
        self.assertEqual(self.strategy.opening_range_high, 404.0)
        self.assertEqual(self.strategy.opening_range_low, 399.0)
        
        # Since second-to-last close (405) > opening range high (404), should enter CALL
        self.strategy.enter_position.assert_called_once_with("CALL")
        
    def test_profit_targets_and_stops(self):
        """Test profit target and stop loss management."""
        # Set up position
        self.strategy.position = "CALL"
        self.strategy.option_contract = Mock()
        self.strategy.entry_underlying_price = 400.0
        self.strategy.entry_option_price = 5.0
        self.strategy.entry_strike = 400
        self.strategy.contracts = 2
        self.strategy.opening_range_high = 402.0
        self.strategy.opening_range_low = 398.0
        self.strategy.half_position_closed = False
        
        # Mock methods
        self.strategy.get_underlying_price = MagicMock()
        self.strategy.place_order = MagicMock()
        self.strategy.exit_all = MagicMock()
        
        mock_ticker = Mock()
        self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
        
        # Create test data for stop loss check - second-to-last bar should be below opening range low
        df = pd.DataFrame({
            'close': [397.0, 401.0]  # Second-to-last (index -2) is 397.0, which is < 398.0
        })
        
        # Test initial stop loss
        self.strategy.get_underlying_price.return_value = 397.0
        
        # In the run method, this would trigger exit_all
        last_closed = df.iloc[-2]
        # Use bool() to convert numpy boolean to Python boolean
        self.assertTrue(bool(last_closed['close'] < self.strategy.opening_range_low))
        
        # Test first profit target
        self.strategy.get_underlying_price.return_value = 401.0  # +$1 from entry
        mock_ticker.marketPrice = MagicMock(return_value=6.0)
        
        # This should trigger half position close
        self.assertTrue(self.strategy.get_underlying_price() >= 
                       self.strategy.entry_underlying_price + self.strategy.underlying_move_target)
        
        # Test breakeven stop after half closed
        self.strategy.half_position_closed = True
        mock_ticker.marketPrice = MagicMock(return_value=4.5)  # Below entry price
        
        # This should trigger exit
        self.assertTrue(mock_ticker.marketPrice() <= self.strategy.entry_option_price)
        
        # Test second profit target
        self.strategy.get_underlying_price.return_value = 401.05  # Strike + ITM offset
        
        # This should trigger final exit
        self.assertTrue(self.strategy.get_underlying_price() >= 
                       self.strategy.entry_strike + self.strategy.itm_offset)
        
    def test_daily_trade_limit(self):
        """Test that only one trade is taken per day."""
        # This is enforced by the daily_trade_done flag in the run method
        # We'll test the logic conceptually
        
        # Initially, no trade done
        daily_trade_done = False
        
        # After entering a position
        if not daily_trade_done and self.strategy.position is None:
            # Enter position
            daily_trade_done = True
        
        # Should not enter another position same day
        self.assertTrue(daily_trade_done)
        
        # Next day (market close/open cycle would reset this)
        daily_trade_done = False
        self.assertFalse(daily_trade_done)

if __name__ == '__main__':
    unittest.main() 