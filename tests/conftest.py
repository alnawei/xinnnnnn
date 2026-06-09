import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DB_PASSWORD", "test-password")
os.environ.setdefault("SUPER_ADMIN_ID", "1")
os.environ.setdefault("MASTER_BOT_TOKEN", "123456:test-token")
os.environ.setdefault("SERVER_IP", "127.0.0.1")
os.environ.setdefault("NETTS_API_KEY", "test-netts-key")
