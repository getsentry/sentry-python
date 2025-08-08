#!/usr/bin/env python3
"""
Test case for the clickhouse-driver generator issue.
This can be used to verify that a fix works correctly.
"""

import pytest
from unittest.mock import Mock, patch
from typing import Generator, Dict, Any

import sentry_sdk
from sentry_sdk.integrations.clickhouse_driver import ClickhouseDriverIntegration


def create_test_generator(records: list) -> Generator[Dict[str, Any], None, None]:
    """Create a test generator that yields records"""
    for record in records:
        yield record


class TestClickhouseGeneratorIssue:
    """Test that generators are not consumed by Sentry integration"""
    
    def setup_method(self):
        """Initialize Sentry before each test"""
        sentry_sdk.init(
            dsn="https://public@sentry.example.com/1",
            integrations=[ClickhouseDriverIntegration()],
            send_default_pii=True,  # This triggers the bug
            traces_sample_rate=1.0,
        )
    
    def teardown_method(self):
        """Clean up after each test"""
        # Reset Sentry SDK
        import sentry_sdk.hub
        sentry_sdk.hub.main_hub = sentry_sdk.hub.Hub()
    
    @patch('clickhouse_driver.client.Client')
    def test_generator_not_consumed(self, mock_client_class):
        """Test that generators passed to execute() are not consumed by Sentry"""
        # Setup mock client
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        # Track what data send_data receives
        received_data = []
        
        def mock_send_data(sample_block, data, *args, **kwargs):
            # Consume the data like clickhouse-driver would
            received_data.extend(list(data))
            return len(received_data)
        
        mock_client.send_data = mock_send_data
        mock_client.connection = Mock(host='localhost', port=9000, database='test')
        
        # Simulate execute() calling send_data internally
        def mock_execute(query, data=None):
            if data is not None:
                return mock_client.send_data(None, data)
            return None
        
        mock_client.execute = mock_execute
        
        # Test data
        test_records = [
            {"id": 1, "name": "Test 1"},
            {"id": 2, "name": "Test 2"},
            {"id": 3, "name": "Test 3"}
        ]
        
        # Create generator
        data_gen = create_test_generator(test_records)
        
        # Import and use clickhouse_driver
        from clickhouse_driver import Client
        client = Client()
        
        # Execute with generator
        result = client.execute("INSERT INTO test_table (id, name) VALUES", data_gen)
        
        # Verify that clickhouse-driver received all the data
        assert len(received_data) == 3, f"Expected 3 records, but got {len(received_data)}"
        assert received_data == test_records
    
    @patch('clickhouse_driver.client.Client')
    def test_list_still_works(self, mock_client_class):
        """Test that lists still work correctly (regression test)"""
        # Setup mock client
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        # Track what data send_data receives
        received_data = []
        
        def mock_send_data(sample_block, data, *args, **kwargs):
            received_data.extend(list(data))
            return len(received_data)
        
        mock_client.send_data = mock_send_data
        mock_client.connection = Mock(host='localhost', port=9000, database='test')
        
        def mock_execute(query, data=None):
            if data is not None:
                return mock_client.send_data(None, data)
            return None
        
        mock_client.execute = mock_execute
        
        # Test data as list
        test_records = [
            {"id": 1, "name": "Test 1"},
            {"id": 2, "name": "Test 2"},
            {"id": 3, "name": "Test 3"}
        ]
        
        # Import and use clickhouse_driver
        from clickhouse_driver import Client
        client = Client()
        
        # Execute with list
        result = client.execute("INSERT INTO test_table (id, name) VALUES", test_records)
        
        # Verify that clickhouse-driver received all the data
        assert len(received_data) == 3
        assert received_data == test_records


if __name__ == "__main__":
    """Run the tests directly"""
    test = TestClickhouseGeneratorIssue()
    
    print("Running test_generator_not_consumed...")
    test.setup_method()
    try:
        test.test_generator_not_consumed()
        print("✓ PASSED (This means the bug is fixed!)")
    except AssertionError as e:
        print(f"✗ FAILED: {e}")
        print("This is expected with the current bug.")
    finally:
        test.teardown_method()
    
    print("\nRunning test_list_still_works...")
    test.setup_method()
    try:
        test.test_list_still_works()
        print("✓ PASSED (Lists work correctly)")
    except AssertionError as e:
        print(f"✗ FAILED: {e}")
    finally:
        test.teardown_method()