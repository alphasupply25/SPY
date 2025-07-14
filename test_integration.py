#!/usr/bin/env python
# Integration tests for SPY EMA CHAD trading system

import unittest
from unittest.mock import patch, Mock, MagicMock
import datetime
import pandas as pd
import numpy as np
import pytz
from ib_insync import Stock, MarketOrder, util

from spy_ema_chad import SPYEMAChad

class TestSPYEMAChadIntegration(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a strategy instance with mocked IB connection
        self.strategy = SPYEMAChad(paper_trading=True)
        self.strategy.ib = Mock()
        self.strategy.tz = pytz.timezone('US/Eastern')
        
        # Create realistic test data for a complete trading day
        # This will be used for all integration tests
        self.test_day = datetime.date(2023, 1, 3)  # A Tuesday
        
        # Create timestamp from 9:00 AM to 3:00 PM in 5 min increments
        times = []
        current_time = datetime.datetime.combine(self.test_day, datetime.time(8, 30))
        end_time = datetime.datetime.combine(self.test_day, datetime.time(15, 0))
        
        while current_time <= end_time:
            times.append(current_time)
            current_time += datetime.timedelta(minutes=5)
        
        # Generate prices with some randomness but with a general uptrend
        # Start at 400 and end around 405
        np.random.seed(42)  # for reproducibility
        n_periods = len(times)
        
        # Base trend
        base_trend = np.linspace(400, 405, n_periods)
        
        # Add some random noise
        noise = np.random.normal(0, 0.5, n_periods)
        
        # Create final prices
        closes = base_trend + noise
        
        # Create realistic OHLC data
        opens = closes - np.random.normal(0, 0.2, n_periods)
        highs = np.maximum(opens, closes) + np.random.normal(0.3, 0.2, n_periods)
        lows = np.minimum(opens, closes) - np.random.normal(0.3, 0.2, n_periods)
        
        # Create volume data with some randomness
        volumes = np.random.randint(800, 1500, n_periods)
        
        # Create the DataFrame
        self.test_data = pd.DataFrame({
            'date': times,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes
        })
        
        # Add the day column needed by the strategy
        self.test_data['day'] = self.test_data['date'].dt.date
        
        # Pre-calculate indicators to use in tests
        self.test_data_with_indicators = self.strategy.calculate_indicators(self.test_data.copy())
        
    def test_long_entry_and_exit_on_profit(self):
        """Test a complete trading scenario where a long trade is entered and exited on profit."""
        # Mock the historical data to use our test data
        self.strategy.get_historical_data = MagicMock(return_value=self.test_data_with_indicators)
        
        # Fix datetime mocking
        mock_datetime = datetime.datetime.combine(self.test_day, datetime.time(9, 0))
        
        # Create a date patch that returns our test day
        with patch('datetime.datetime') as mock_dt:
            # Configure the mock datetime.now() to return a MagicMock with date() method
            mock_now_result = MagicMock()
            mock_now_result.date.return_value = self.test_day
            mock_dt.now.return_value = mock_now_result
            
            # Pass through original methods
            mock_dt.combine = datetime.datetime.combine
            mock_dt.strptime = datetime.datetime.strptime
            
            # Get the initial condition
            self.strategy.initial_condition = self.strategy.check_initial_condition(self.test_data_with_indicators)
            
            # Ensure we got an initial condition
            self.assertIsNotNone(self.strategy.initial_condition)
            
            # Set waiting for entry
            self.strategy.waiting_for_entry = True
            
            # Mock current price to be at the 9 EMA to trigger entry
            current_ema = self.test_data_with_indicators.iloc[7]['ema_short']  # Around 9:05 AM
            
            mock_ticker = Mock()
            mock_ticker.marketPrice = MagicMock(return_value=current_ema)
            self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
            
            # Mock place_order so we don't actually place orders
            self.strategy.place_order = MagicMock()
            
            # Check for entry
            result = self.strategy.check_for_entry(current_ema, current_ema)
            self.assertTrue(result)
            
            # Enter position
            entry_direction = "LONG" if self.strategy.initial_condition == "ABOVE" else "SHORT"
            self.strategy.enter_position(entry_direction)
            
            # Check that we entered a position
            self.assertIsNotNone(self.strategy.position)
            expected_position = "LONG" if self.strategy.initial_condition == "ABOVE" else "SHORT"
            self.assertEqual(self.strategy.position, expected_position)
            
            # Now simulate price going up to hit profit target
            mock_ticker.marketPrice = MagicMock(return_value=self.strategy.entry_price + self.strategy.profit_target)
            
            # Check profit target
            result = self.strategy.check_profit_target()
            self.assertTrue(result)
            
            # Exit position
            self.strategy.exit_position("Profit target reached")
            
            # Check that we exited the position
            self.assertIsNone(self.strategy.position)

    def test_short_entry_and_exit_on_stop_loss(self):
        """Test a complete trading scenario where a short trade is entered and exited on stop loss."""
        # Modify our test data to have a downtrend so we get a SHORT signal
        downtrend_data = self.test_data.copy()
        # Make price below indicators at 9:00 AM
        idx_9am = downtrend_data[downtrend_data['date'].dt.time == datetime.time(9, 0)].index[0]
        downtrend_data.loc[idx_9am, 'close'] = 395.0
        
        # Recalculate indicators
        downtrend_data = self.strategy.calculate_indicators(downtrend_data)
        
        # Mock the historical data
        self.strategy.get_historical_data = MagicMock(return_value=downtrend_data)
        
        # Fix datetime mocking
        mock_datetime = datetime.datetime.combine(self.test_day, datetime.time(9, 0))
        
        # Create a date patch that returns our test day
        with patch('datetime.datetime') as mock_dt:
            # Configure the mock datetime.now() to return a MagicMock with date() method
            mock_now_result = MagicMock()
            mock_now_result.date.return_value = self.test_day
            mock_dt.now.return_value = mock_now_result
            
            # Pass through original methods
            mock_dt.combine = datetime.datetime.combine
            mock_dt.strptime = datetime.datetime.strptime
            
            # Get the initial condition
            self.strategy.initial_condition = self.strategy.check_initial_condition(downtrend_data)
            
            # Ensure we got a BELOW condition
            self.assertEqual(self.strategy.initial_condition, "BELOW")
            
            # Set waiting for entry
            self.strategy.waiting_for_entry = True
            
            # Mock current price to be at the 9 EMA to trigger entry
            current_ema = downtrend_data.iloc[8]['ema_short']  # Around 9:10 AM
            
            mock_ticker = Mock()
            mock_ticker.marketPrice = MagicMock(return_value=current_ema)
            self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
            
            # Mock place_order so we don't actually place orders
            self.strategy.place_order = MagicMock()
            
            # Check for entry
            result = self.strategy.check_for_entry(current_ema, current_ema)
            self.assertTrue(result)
            
            # Enter position
            entry_direction = "LONG" if self.strategy.initial_condition == "ABOVE" else "SHORT"
            self.strategy.enter_position(entry_direction)
            
            # Check that we entered a SHORT position
            self.assertEqual(self.strategy.position, "SHORT")
            
            # Now modify a candle to trigger stop loss
            # Make price go above all indicators in the last completed candle
            stop_loss_df = downtrend_data.copy()
            stop_loss_df.iloc[-2, stop_loss_df.columns.get_indexer(['close'])] = 405.0  # Price above all indicators
            stop_loss_df.iloc[-2, stop_loss_df.columns.get_indexer(['ema_short'])] = 400.0
            stop_loss_df.iloc[-2, stop_loss_df.columns.get_indexer(['ema_long'])] = 399.0
            stop_loss_df.iloc[-2, stop_loss_df.columns.get_indexer(['vwap'])] = 398.0
            
            # Check stop loss
            result = self.strategy.check_stop_loss(stop_loss_df)
            self.assertTrue(result)
            
            # Exit position
            self.strategy.exit_position("Stop loss triggered")
            
            # Check that we exited the position
            self.assertIsNone(self.strategy.position)

    def test_force_close_at_end_of_day(self):
        """Test that positions are force closed at the end of the day."""
        # Set up a position
        self.strategy.position = "LONG"
        self.strategy.entry_price = 400.0
        
        # Create real datetime objects for testing
        mock_now = datetime.datetime.combine(self.test_day, datetime.time(14, 55))
        force_close_time = datetime.datetime.combine(self.test_day, datetime.time(14, 55))
        
        # Convert to timezone-aware datetimes
        mock_now_tz = self.strategy.tz.localize(mock_now)
        force_close_time_tz = self.strategy.tz.localize(force_close_time)
        
        # Create a patch for datetime.now() to return our mock time
        with patch('datetime.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now_tz
            mock_dt.combine.return_value = force_close_time_tz
            mock_dt.strptime.return_value = datetime.datetime.strptime(self.strategy.force_close_time, "%H:%M:%S")
            
            # Mock place_order so we don't actually place orders
            self.strategy.place_order = MagicMock()
            
            mock_ticker = Mock()
            mock_ticker.marketPrice = MagicMock(return_value=401.0)
            self.strategy.ib.reqTickers = MagicMock(return_value=[mock_ticker])
            
            # Check force close time
            result = self.strategy.is_force_close_time()
            self.assertTrue(result)
            
            # Exit position
            self.strategy.exit_position("Force close time")
            
            # Check that we exited the position
            self.assertIsNone(self.strategy.position)
            
    def test_no_trade_when_price_between_indicators(self):
        """Test that no trade is taken when price is between indicators at 9:00 AM."""
        # Modify our test data to have price between indicators at 9:00 AM
        between_data = self.test_data.copy()
        idx_9am = between_data[between_data['date'].dt.time == datetime.time(9, 0)].index[0]
        
        # Set price to be between indicators
        between_data.loc[idx_9am, 'close'] = 399.5
        
        # Recalculate indicators
        between_data = self.strategy.calculate_indicators(between_data)
        
        # Make sure the 9:00 AM bar has price between indicators
        between_data.iloc[idx_9am, between_data.columns.get_indexer(['ema_short'])] = 400.0
        between_data.iloc[idx_9am, between_data.columns.get_indexer(['ema_long'])] = 398.0
        between_data.iloc[idx_9am, between_data.columns.get_indexer(['vwap'])] = 401.0
        
        # Mock the historical data
        self.strategy.get_historical_data = MagicMock(return_value=between_data)
        
        # Fix datetime mocking
        mock_datetime = datetime.datetime.combine(self.test_day, datetime.time(9, 0))
        
        # Create a date patch that returns our test day
        with patch('datetime.datetime') as mock_dt:
            # Configure the mock datetime.now() to return a MagicMock with date() method
            mock_now_result = MagicMock()
            mock_now_result.date.return_value = self.test_day
            mock_dt.now.return_value = mock_now_result
            
            # Pass through original methods
            mock_dt.combine = datetime.datetime.combine
            mock_dt.strptime = datetime.datetime.strptime
            
            # Get the initial condition
            result = self.strategy.check_initial_condition(between_data)
            
            # Ensure we got None, meaning no trade today
            self.assertIsNone(result)

if __name__ == '__main__':
    unittest.main() 