import os
import sys

# Settings are validated at import time, so these must exist before any `src` import.
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/testdb")
os.environ.setdefault("AES_SECRET_KEY", "12345678901234567890123456789012")
os.environ.setdefault("USE_MOCK_BROKER", "false")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
