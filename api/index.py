import os
import sys
from pathlib import Path

# Add project root to sys.path so app module is discoverable by Vercel
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.main import app

# Vercel entry point
__all__ = ["app"]
