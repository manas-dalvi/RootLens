"""RootLens Pytest Configuration and Custom Hook Definitions.

Registers custom pytest marks and common session configurations across all test suites.
"""

def pytest_configure(config):
    """Register custom markers to avoid PytestUnknownMarkWarning."""
    config.addinivalue_line(
        "markers", "benchmark: mark test as performance and reliability benchmark"
    )
    config.addinivalue_line(
        "markers", "e2e: mark test as live end-to-end evaluation"
    )

