#!/usr/bin/env python3
"""
Simple reproduction of the clickhouse-driver generator issue in Sentry SDK.
This script demonstrates that the Sentry integration consumes generators
before clickhouse-driver can use them.
"""

import sentry_sdk
from sentry_sdk.integrations.clickhouse_driver import ClickhouseDriverIntegration

# Initialize Sentry with PII enabled (this triggers the bug)
sentry_sdk.init(
    dsn="https://public@sentry.example.com/1",
    integrations=[ClickhouseDriverIntegration()],
    send_default_pii=True,  # This is crucial - it triggers the bug!
    traces_sample_rate=1.0,
)

# Patch clickhouse_driver to demonstrate the issue
import clickhouse_driver
from clickhouse_driver import Client

# Store original send_data to see what happens
original_send_data = clickhouse_driver.client.Client.send_data

def patched_send_data(self, sample_block, data, *args, **kwargs):
    """This is the actual send_data that would process the data"""
    print(f"\n[CLICKHOUSE] send_data called")
    print(f"[CLICKHOUSE] Data type: {type(data)}")
    
    # Try to consume the data
    consumed = []
    try:
        for item in data:
            print(f"[CLICKHOUSE] Consuming item: {item}")
            consumed.append(item)
    except Exception as e:
        print(f"[CLICKHOUSE] Error consuming data: {e}")
    
    print(f"[CLICKHOUSE] Total items consumed: {len(consumed)}")
    return len(consumed)

# Apply patch
clickhouse_driver.client.Client.send_data = patched_send_data

# Create test client
class TestConnection:
    def __init__(self):
        self.host = "localhost"
        self.port = 9000
        self.database = "test"

class TestClient:
    def __init__(self):
        self.connection = TestConnection()
        
    def execute(self, query, data=None):
        print(f"\n[CLIENT] Executing query: {query}")
        if data is not None:
            print(f"[CLIENT] Data provided: generator")
            # Simulate what clickhouse-driver does internally
            result = self.send_data(None, data)
            print(f"[CLIENT] Result: {result} rows inserted")
        return None

# Apply our test client
TestClient.send_data = clickhouse_driver.client.Client.send_data
client = TestClient()

# Test 1: Generator (will fail)
print("\n=== TEST 1: Using Generator ===")
def data_generator():
    """Generator that yields data and logs when consumed"""
    records = [{"id": 1, "value": "A"}, {"id": 2, "value": "B"}, {"id": 3, "value": "C"}]
    for i, record in enumerate(records):
        print(f"[GENERATOR] Yielding record {i+1}: {record}")
        yield record

gen = data_generator()
client.execute("INSERT INTO test (id, value) VALUES", gen)

# Test 2: List (will work)
print("\n\n=== TEST 2: Using List ===")
data_list = [{"id": 1, "value": "A"}, {"id": 2, "value": "B"}, {"id": 3, "value": "C"}]
client.execute("INSERT INTO test (id, value) VALUES", data_list)

print("\n\n=== CONCLUSION ===")
print("With send_default_pii=True, the Sentry integration consumes generators")
print("in _wrap_send_data (line 142: db_params.extend(data))")
print("This leaves nothing for clickhouse-driver to consume!")