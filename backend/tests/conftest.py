import os
import sys
from pathlib import Path

# Ensure backend and workspace roots are on sys.path for test runner discovery
backend_dir = Path(__file__).resolve().parent.parent
workspace_dir = backend_dir.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(workspace_dir) not in sys.path:
    sys.path.insert(0, str(workspace_dir))
