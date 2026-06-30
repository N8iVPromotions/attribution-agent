"""Pytest bootstrap.

The test suite imports the application's top-level modules (``agents``,
``utils``, ``attribution_models``, ...), which live in this directory. When
pytest is invoked from the repository root those modules are not importable
unless this directory is on ``sys.path``. Adding it here — in a conftest that
pytest loads before collecting any test module — makes the imports resolve in
both CI and local runs.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
