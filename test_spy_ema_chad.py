#!/usr/bin/env python
# Unit tests for SPY EMA CHAD trading system

import unittest
from unittest.mock import patch, Mock, MagicMock
import datetime
import pandas as pd
import numpy as np
import pytz
from ib_insync import Stock, MarketOrder, util

from spy_ema_chad import SPYEMAChad

class TestSPYEMAChad(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.strategy = SPYEMAChad(paper_trading=True)
        # Mock the IB connection
        self.strategy.ib = Mock()
        # Set a fixed timezone for testing
        self.strategy.tz = pytz.timezone('US/Eastern')
        
    def tearDown(self):
        """Clean up after each test method."""
        pass
        
    def test_initialization(self):
        """Test the initialization of the SPYEMAChad class."""
        # Test default parameters
        self.assertEqual(self.strategy.ticker, "SPY")
        self.assertEqual(self.strategy.profit_target, 1.0)
        self.assertEqual(self.strategy.market_open, "09:30:00")
        self.assertEqual(self.strategy.market_close, "16:00:00")
        self.assertEqual(self.strategy.signal_time, "09:00:00")
        self.assertEqual(self.strategy.force_close_time, "15:50:00")
        self.assertEqual(self.strategy.ema_short, 9)
        self.assertEqual(self.strategy.ema_long, 20)
        self.assertEqual(self.strategy.paper_trading, True)
        
        # Test initial state
        self.assertIsNone(self.strategy.position)
        self.assertEqual(self.strategy.entry_price, 0)
        self.assertEqual(self.strategy.stop_loss, 0)
        self.assertFalse(self.strategy.today_trade_taken)
        self.assertFalse(self.strategy.waiting_for_entry)
        self.assertIsNone(self.strategy.initial_condition)
        
        # Test with custom parameters
        custom_strategy = SPYEMAChad(
            ticker="QQQ", 
            profit_target=2.0, 
            market_open="09:00:00",
            market_close="16:00:00",
            ema_short=5,
            ema_long=15,
            paper_trading=False
        )
        self.assertEqual(custom_strategy.ticker, "QQQ")
        self.assertEqual(custom_strategy.profit_target, 2.0)
        self.assertEqual(custom_strategy.market_open, "09:00:00")
        self.assertEqual(custom_strategy.market_close, "16:00:00")
        self.assertEqual(custom_strategy.ema_short, 5)
        self.assertEqual(custom_strategy.ema_long, 15)
        self.assertEqual(custom_strategy.paper_trading, False)
        
    def test_connect_to_ib(self):
        """Test the connection to Interactive Brokers."""
        # Mock successful connection
        self.strategy.ib.connect = MagicMock(return_value=True)
        self.strategy.ib.isConnected = MagicMock(return_value=False)
        result = self.strategy.connect_to_ib()
        self.assertTrue(result)
        self.strategy.ib.connect.assert_called_once()
        
        # Mock failed connection
        self.strategy.ib.connect = MagicMock(side_effect=Exception("Connection failed"))
        result = self.strategy.connect_to_ib()
        self.assertFalse(result)
        
    def test_get_contract(self):
        """Test getting the contract for the specified ticker."""
        contract = self.strategy.get_contract()
        self.assertIsInstance(contract, Stock)
        self.assertEqual(contract.symbol, "SPY")
        self.assertEqual(contract.exchange, "SMART")
        self.assertEqual(contract.currency, "USD")
        
        # Test with different ticker
        self.strategy.ticker = "QQQ"
        contract = self.strategy.get_contract()
        self.assertEqual(contract.symbol, "QQQ")
        
    @patch('ib_insync.util.df')
    def test_get_historical_data(self, mock_util_df):
        """Test getting historical data."""
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
        result = self.strategy.get_historical_data()
        
        # Assert the method was called with correct parameters
        self.strategy.ib.reqHistoricalData.assert_called_once()
        self.assertEqual(result.equals(mock_df), True)
        
    def test_calculate_indicators(self):
        """Test the calculation of EMA and VWAP indicators."""
        # Create test data
        df = pd.DataFrame({
            'date': pd.to_datetime(['2023-01-01 09:00:00', '2023-01-01 09:05:00', 
                                    '2023-01-01 09:10:00', '2023-01-01 09:15:00']),
            'open': [400.0, 401.0, 402.0, 403.0],
            'high': [402.0, 403.0, 404.0, 405.0],
            'low': [399.0, 400.0, 401.0, 402.0],
            'close': [401.0, 402.0, 403.0, 404.0],
            'volume': [1000, 1200, 1100, 1300]
        })
        
        # Calculate the indicators
        result = self.strategy.calculate_indicators(df)
        
        # Check that the indicators were calculated
        self.assertIn('ema_short', result.columns)
        self.assertIn('ema_long', result.columns)
        self.assertIn('vwap', result.columns)
        
        # The EMA and VWAP values should be numeric
        self.assertTrue(pd.api.types.is_numeric_dtype(result['ema_short']))
        self.assertTrue(pd.api.types.is_numeric_dtype(result['ema_long']))
        self.assertTrue(pd.api.types.is_numeric_dtype(result['vwap']))
        
        # For this simple dataset the EMA should be close to the moving average
        # (not exact since EMA uses different weighting)
        self.assertAlmostEqual(result['ema_short'].iloc[-1], 402.5, delta=3)
        
        # VWAP calculation check - with weighted volume
        typical_prices = (df['high'] + df['low'] + df['close']) / 3
        weighted_prices = typical_prices * df['volume']
        expected_vwap = weighted_prices.sum() / df['volume'].sum()
        self.assertAlmostEqual(result['vwap'].iloc[-1], expected_vwap, delta=0.01)
        
    def test_check_initial_condition(self):
        """Test checking the initial condition at 9:00 AM."""
        # Create test data
        df = pd.DataFrame({
            'date': pd.to_datetime(['2023-01-01 09:00:00', '2023-01-01 09:05:00', '2023-01-01 09:10:00']),
            'close': [401.0, 402.0, 403.0],
            'high': [402.0, 403.0, 404.0],
            'low': [400.0, 401.0, 402.0],
            'ema_short': [399.0, 399.5, 400.0],
            'ema_long': [398.0, 398.5, 399.0],
            'vwap': [397.0, 398.0, 399.0],
            'volume': [1000, 1200, 1100],
            'day': [datetime.date(2023, 1, 1), datetime.date(2023, 1, 1), datetime.date(2023, 1, 1)]
        })
        
        # Convert date to timezone-aware for consistency with the strategy
        df['date'] = df['date'].dt.tz_localize(self.strategy.tz)
        
        # Mock current time to be today
        today = datetime.date(2023, 1, 1)
        mock_now = datetime.datetime.combine(today, datetime.time(9, 0))
        mock_now = self.strategy.tz.localize(mock_now)
        
        with patch.object(datetime, 'datetime', Mock(now=Mock(return_value=mock_now))):
            # Test with price above indicators
            current_price = 401.0
            result = self.strategy.check_initial_condition(df=current_price, df_5=df)
            self.assertEqual(result, "ABOVE")
            
            # Test with price below indicators
            current_price = 395.0
            result = self.strategy.check_initial_condition(df=current_price, df_5=df)
            self.assertEqual(result, "BELOW")
            
            # Test with price between indicators
            current_price = 398.5
            df['ema_short'] = [398.0, 398.0, 398.0]
            df['ema_long'] = [397.0, 397.0, 397.0]
            df['vwap'] = [400.0, 400.0, 400.0]
            result = self.strategy.check_initial_condition(df=current_price, df_5=df)
            self.assertIsNone(result)
            
    def test_check_for_entry(self):
        """Test checking for entry conditions."""
        # Test when not waiting for entry
        self.strategy.waiting_for_entry = False
        self.assertFalse(self.strategy.check_for_entry(400.0, 400.0))
        
        # Test when waiting for entry and price touches EMA (ABOVE condition)
        self.strategy.waiting_for_entry = True
        self.strategy.initial_condition = "ABOVE"
        
        # Price exactly at EMA
        self.assertTrue(self.strategy.check_for_entry(400.0, 400.0))
        
        # Price very close to EMA (within threshold)
        # Default threshold is 0.0003, so 400 * 0.0003 = 0.12
        self.assertTrue(self.strategy.check_for_entry(400.1, 400.0))
        
        # Price too far from EMA
        self.assertFalse(self.strategy.check_for_entry(402.0, 400.0))
        
        # Test with BELOW condition
        self.strategy.initial_condition = "BELOW"
        self.assertTrue(self.strategy.check_for_entry(400.0, 400.0))
        self.assertFalse(self.strategy.check_for_entry(395.0, 400.0))
        
    def test_check_stop_loss(self):
        """Test checking stop loss conditions."""
        # Create test data
        df = pd.DataFrame({
            'close': [401.0, 402.0],
            'ema_short': [400.0, 400.5],
            'ema_long': [399.0, 399.5],
            'vwap': [398.0, 398.5]
        })
        
        # Test when no position is open
        self.strategy.position = None
        current_price = 401.0
        self.assertFalse(self.strategy.check_stop_loss(current_price, df))
        
        # Test long position with price still above indicators
        self.strategy.position = "LONG"
        self.assertFalse(self.strategy.check_stop_loss(current_price, df))
        
        # Test long position with price below indicators (stop loss condition)
        df.iloc[-2] = [398.0, 399.0, 400.0, 401.0]  # price below all indicators
        current_price = 397.0
        self.assertTrue(self.strategy.check_stop_loss(current_price, df))
        
        # Test short position with price still below indicators
        self.strategy.position = "SHORT"
        df.iloc[-2] = [398.0, 399.0, 400.0, 401.0]  # price below all indicators
        current_price = 397.0
        self.assertFalse(self.strategy.check_stop_loss(current_price, df))
        
        # Test short position with price above indicators (stop loss condition)
        df.iloc[-2] = [402.0, 400.0, 399.0, 398.0]  # price above all indicators
        current_price = 403.0
        self.assertTrue(self.strategy.check_stop_loss(current_price, df))
        
    def test_place_order(self):
        """Test placing an order with Interactive Brokers."""
        # Mock IB's placeOrder method
        self.strategy.ib.placeOrder = MagicMock()
        self.strategy.ib.sleep = MagicMock()
        self.strategy.ib.reqTickers = MagicMock(return_value=[Mock(marketPrice=MagicMock(return_value=400.0))])
        self.strategy.ib.reqContractDetails = MagicMock(return_value=[Mock(contract=Mock())])
        self.strategy.ib.qualifyContracts = MagicMock()
        
        # Set position before placing order (needed for option type determination)
        self.strategy.position = "LONG"
        
        # Test placing a buy order
        self.strategy.place_order("BUY", 1)
        
        # Check that the correct methods were called
        self.strategy.ib.placeOrder.assert_called_once()
        args, _ = self.strategy.ib.placeOrder.call_args
        contract, order = args
        
        self.assertEqual(order.action, "BUY")
        self.assertEqual(order.totalQuantity, 1)
        
        # Test placing a sell order
        self.strategy.ib.placeOrder.reset_mock()
        self.strategy.position = "SHORT"
        self.strategy.option = None  # Reset option to test creation
        self.strategy.place_order("SELL", 2)
        
        args, _ = self.strategy.ib.placeOrder.call_args
        contract, order = args
        
        self.assertEqual(order.action, "SELL")
        self.assertEqual(order.totalQuantity, 2)
        
    def test_enter_position(self):
        """Test entering a position."""
        # Mock place_order method
        self.strategy.place_order = MagicMock()
        
        # Mock IB ticker response
        mock_ticker = Mock()
        mock_ticker.marketPrice = MagicMock(return_value=400.0)
        self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
        
        # Test entering long position
        self.strategy.enter_position("LONG")
        
        # Check that place_order was called correctly
        self.strategy.place_order.assert_called_once_with("BUY")
        
        # Check that state was updated correctly
        self.assertEqual(self.strategy.position, "LONG")
        self.assertEqual(self.strategy.entry_price, 400.0)
        self.assertTrue(self.strategy.today_trade_taken)
        self.assertFalse(self.strategy.waiting_for_entry)
        
        # Reset mocks and test entering short position
        self.strategy.place_order.reset_mock()
        self.strategy.position = None
        self.strategy.today_trade_taken = False
        self.strategy.waiting_for_entry = True
        
        self.strategy.enter_position("SHORT")
        
        self.strategy.place_order.assert_called_once_with("SELL")
        self.assertEqual(self.strategy.position, "SHORT")
        
    def test_exit_position(self):
        """Test exiting a position."""
        # Mock place_order method
        self.strategy.place_order = MagicMock()
        
        # Mock IB ticker response
        mock_ticker = Mock()
        mock_ticker.marketPrice = MagicMock(return_value=401.0)
        self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
        
        # Test exiting long position
        self.strategy.position = "LONG"
        self.strategy.entry_price = 400.0
        
        self.strategy.exit_position("Testing")
        
        # Check that place_order was called correctly
        self.strategy.place_order.assert_called_once_with("SELL")
        
        # Check that state was updated correctly
        self.assertIsNone(self.strategy.position)
        self.assertEqual(self.strategy.entry_price, 0)
        
        # Reset mocks and test exiting short position
        self.strategy.place_order.reset_mock()
        self.strategy.position = "SHORT"
        self.strategy.entry_price = 400.0
        
        self.strategy.exit_position("Testing")
        
        self.strategy.place_order.assert_called_once_with("BUY")
        
    def test_check_profit_target(self):
        """Test checking if profit target has been reached."""
        # Mock IB ticker response
        mock_ticker = Mock()
        self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
        
        # Test when no position is open
        self.strategy.position = None
        self.assertFalse(self.strategy.check_profit_target())
        
        # Test long position below profit target
        self.strategy.position = "LONG"
        self.strategy.entry_price = 400.0
        self.strategy.profit_target = 1.0
        
        mock_ticker.marketPrice = MagicMock(return_value=400.5)
        self.assertFalse(self.strategy.check_profit_target())
        
        # Test long position at profit target
        mock_ticker.marketPrice = MagicMock(return_value=401.0)
        self.assertTrue(self.strategy.check_profit_target())
        
        # Test short position below profit target
        self.strategy.position = "SHORT"
        self.strategy.entry_price = 400.0
        
        mock_ticker.marketPrice = MagicMock(return_value=399.5)
        self.assertFalse(self.strategy.check_profit_target())
        
        # Test short position at profit target
        mock_ticker.marketPrice = MagicMock(return_value=399.0)
        self.assertTrue(self.strategy.check_profit_target())
        
    def test_reset_daily_state(self):
        """Test resetting daily trading state."""
        self.strategy.today_trade_taken = True
        self.strategy.waiting_for_entry = True
        self.strategy.initial_condition = "ABOVE"
        
        self.strategy.reset_daily_state()
        
        self.assertFalse(self.strategy.today_trade_taken)
        self.assertFalse(self.strategy.waiting_for_entry)
        self.assertIsNone(self.strategy.initial_condition)
        
    def test_is_market_open(self):
        """Test checking if market is open."""
        # Mock current time to be during market hours
        test_day = datetime.date(2023, 1, 3)  # A Tuesday
        
        # Create a proper mock for datetime.now that returns a datetime object
        # instead of using patch.object which replaces the whole datetime class
        with patch('spy_ema_chad.datetime') as mock_datetime:
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
        # Set force close time
        test_day = datetime.date(2023, 1, 3)  # A Tuesday
        
        with patch('spy_ema_chad.datetime') as mock_datetime:
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
        
    @patch('time.sleep')
    def test_run_basic_flow(self, mock_sleep):
        """Test the basic flow of the run method."""
        # This is a simplified test that just verifies the basic structure works
        # A full test would be very complex due to the continuous loop
        
        # Mock methods to control flow
        self.strategy.connect_to_ib = MagicMock(return_value=True)
        self.strategy.get_historical_data = MagicMock(return_value=pd.DataFrame({
            'date': pd.to_datetime(['2023-01-01 09:00:00']),
            'close': [400.0],
            'high': [401.0],
            'low': [399.0],
            'volume': [1000]
        }))
        self.strategy.calculate_indicators = MagicMock(return_value=pd.DataFrame({
            'date': pd.to_datetime(['2023-01-01 09:00:00']),
            'close': [400.0],
            'ema_short': [399.0],
            'ema_long': [398.0],
            'vwap': [397.0]
        }))
        self.strategy.is_market_open = MagicMock(side_effect=[True, False])  # Run once then exit
        self.strategy.is_force_close_time = MagicMock(return_value=False)
        self.strategy.position = None
        
        # Mock IB ticker response
        mock_ticker = Mock()
        mock_ticker.marketPrice = MagicMock(return_value=400.0)
        self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
        
        # Add a side effect to exit the infinite loop
        mock_sleep.side_effect = KeyboardInterrupt()
        
        # Run the strategy (should exit after one iteration due to KeyboardInterrupt)
        try:
            self.strategy.run()
        except KeyboardInterrupt:
            pass
        
        # Verify methods were called
        self.strategy.connect_to_ib.assert_called_once()
        self.strategy.get_historical_data.assert_called()
        self.strategy.calculate_indicators.assert_called()
        self.strategy.is_market_open.assert_called()

if __name__ == '__main__':
    unittest.main() 