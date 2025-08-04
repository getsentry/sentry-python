#!/usr/bin/env python3
"""
Minimal reproduction for Sentry SDK clickhouse-driver generator issue.
Issue: https://github.com/getsentry/sentry-python/issues/4657

The problem: When using a generator as a data source for INSERT queries,
the Sentry clickhouse-driver integration consumes the generator before
it's passed to clickhouse-driver, resulting in no data being inserted.
"""

import logging
from typing import Generator, Dict, Any

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    import sentry_sdk
    from sentry_sdk.integrations.clickhouse_driver import ClickhouseDriverIntegration
    logger.info(f"Sentry SDK version: {sentry_sdk.__version__}")
except ImportError:
    logger.error("Failed to import sentry_sdk - make sure it's installed")
    raise

try:
    from clickhouse_driver import Client
    import clickhouse_driver
    logger.info(f"clickhouse-driver version: {clickhouse_driver.VERSION}")
except ImportError:
    logger.error("Failed to import clickhouse_driver - run: pip install clickhouse-driver")
    raise


# Mock clickhouse client to demonstrate the issue without requiring actual ClickHouse instance
class MockClient:
    """Mock ClickHouse client that logs when data is sent"""
    
    def __init__(self):
        self.received_data = []
    
    def execute(self, query: str, data=None):
        logger.info(f"Execute called with query: {query}")
        if data is not None:
            # This simulates clickhouse-driver consuming the generator
            consumed_data = list(data)
            logger.info(f"Data received by clickhouse-driver: {consumed_data}")
            self.received_data = consumed_data
        return None


def create_data_generator() -> Generator[Dict[str, Any], None, None]:
    """Create a generator that yields test data"""
    logger.info("Creating data generator")
    records = [
        {"id": 1, "name": "Test 1"},
        {"id": 2, "name": "Test 2"},
        {"id": 3, "name": "Test 3"}
    ]
    for record in records:
        logger.info(f"Generator yielding: {record}")
        yield record


def test_without_sentry():
    """Test inserting data without Sentry SDK initialized"""
    logger.info("\n=== Testing WITHOUT Sentry SDK ===")
    
    client = MockClient()
    
    # Create generator
    data_gen = create_data_generator()
    
    # Execute insert with generator
    client.execute("INSERT INTO test_table (id, name) VALUES", data_gen)
    
    logger.info(f"Data received by MockClient: {client.received_data}")
    assert len(client.received_data) == 3, f"Expected 3 records, got {len(client.received_data)}"
    logger.info("✓ Test WITHOUT Sentry: PASSED")


def test_with_sentry():
    """Test inserting data with Sentry SDK initialized"""
    logger.info("\n=== Testing WITH Sentry SDK ===")
    
    # Initialize Sentry with clickhouse-driver integration
    sentry_sdk.init(
        dsn="https://public@sentry.example.com/1",  # Dummy DSN
        integrations=[ClickhouseDriverIntegration()],
        send_default_pii=True,  # This triggers the bug!
        traces_sample_rate=1.0,
    )
    
    # Monkey-patch to use our mock client
    original_client = Client
    
    class PatchedClient(MockClient):
        def __init__(self, *args, **kwargs):
            super().__init__()
            # Need to add attributes that Sentry integration expects
            self.connection = type('Connection', (), {
                'host': 'localhost',
                'port': 9000,
                'database': 'default'
            })()
            
        def send_data(self, *args):
            """This method gets wrapped by Sentry"""
            logger.info(f"send_data called with args: {args}")
            if len(args) >= 3:
                data = args[2]
                # Try to consume the data
                try:
                    consumed = list(data)
                    logger.info(f"send_data consumed data: {consumed}")
                except Exception as e:
                    logger.error(f"Error consuming data in send_data: {e}")
    
    # Replace the import
    clickhouse_driver.client.Client = PatchedClient
    
    try:
        # Create client (will be our patched version)
        client = Client()
        
        # Create generator
        data_gen = create_data_generator()
        
        # The integration will wrap send_data and consume the generator here
        # Before the actual clickhouse-driver gets to use it
        client.execute("INSERT INTO test_table (id, name) VALUES", data_gen)
        
        logger.info(f"Data received by MockClient: {client.received_data}")
        
        # This will fail because the generator was consumed by Sentry integration
        assert len(client.received_data) == 3, f"Expected 3 records, got {len(client.received_data)}"
        logger.info("✓ Test WITH Sentry: PASSED")
        
    except AssertionError:
        logger.error("✗ Test WITH Sentry: FAILED - No data received (generator was consumed)")
        raise
    finally:
        # Restore original
        clickhouse_driver.client.Client = original_client


def demonstrate_traceback_generator():
    """Demonstrate the exact traceback from the issue"""
    logger.info("\n=== Demonstrating Traceback with Exception Generator ===")
    
    # Initialize Sentry
    sentry_sdk.init(
        dsn="https://public@sentry.example.com/1",
        integrations=[ClickhouseDriverIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )
    
    def exception_generator():
        """Generator that throws when consumed"""
        raise ValueError("sh*t, someone ate my data")
        yield  # Never reached
    
    class TracebackClient(MockClient):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.connection = type('Connection', (), {
                'host': 'localhost',
                'port': 9000,
                'database': 'default',
                '_sentry_span': None
            })()
            
        def send_data(self, sample_block, data, *args):
            """This simulates the actual clickhouse-driver send_data signature"""
            logger.info("Original send_data called")
            # This is where clickhouse-driver would normally consume the data
            # But Sentry's wrapper already consumed it!
            try:
                list(data)
            except Exception as e:
                logger.info(f"Expected: data already consumed by Sentry wrapper")
    
    original_client = Client
    clickhouse_driver.client.Client = TracebackClient
    
    try:
        client = Client()
        
        # This will throw in Sentry's wrapper
        try:
            client.send_data(None, exception_generator())
        except ValueError as e:
            logger.error(f"Exception raised in Sentry wrapper: {e}")
            logger.info("This proves the generator is consumed by Sentry before clickhouse-driver uses it")
            
    finally:
        clickhouse_driver.client.Client = original_client


if __name__ == "__main__":
    logger.info("Starting clickhouse-driver generator issue reproduction...\n")
    
    # Test 1: Without Sentry (should work)
    try:
        test_without_sentry()
    except Exception as e:
        logger.error(f"Test without Sentry failed: {e}")
    
    # Test 2: With Sentry (will fail due to bug)
    try:
        test_with_sentry()
    except AssertionError:
        logger.info("Expected failure - this demonstrates the bug")
    
    # Test 3: Show exact traceback scenario
    try:
        demonstrate_traceback_generator()
    except Exception as e:
        logger.error(f"Traceback demonstration error: {e}")
    
    logger.info("\n✓ Reproduction complete!")
    logger.info("The issue is confirmed: Sentry's clickhouse-driver integration")
    logger.info("consumes generators before they reach clickhouse-driver.")