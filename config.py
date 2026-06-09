import os
from pathlib import Path


def load_env_file(path: str = ".env") -> None:
    """读取项目根目录 .env；系统环境变量优先，不会被 .env 覆盖。"""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


def get_required_env(name: str) -> str:
    """读取必填配置；缺少时直接报错，避免机器人带空密钥启动。"""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# ================= 1. 基础与数据库配置 =================
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "bot")
DB_PASSWORD = get_required_env("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "bot")

# 拼接异步数据库 URI，供新版的 AsyncSessionLocal 使用
DATABASE_URL = f"mysql+aiomysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

SCAN_INTERVAL = 5  # 波场节点扫块轮询间隔 (秒)

# ================= 2. SaaS 母平台核心配置 =================
SUPER_ADMIN_ID = int(get_required_env("SUPER_ADMIN_ID"))
MASTER_BOT_TOKEN = get_required_env("MASTER_BOT_TOKEN")
# SaaS 母平台机器人的跳转链接（请替换为您真实的机器人 Username 链接）
SAAS_BOT_URL = os.getenv("SAAS_BOT_URL", "https://t.me/YourSaaSBotUsername")

# ================= 3. 供应链与发货配置 (Netts API) =================
SERVER_IP = get_required_env("SERVER_IP")           # 绑定 Netts 的白名单公网 IP
NETTS_API_KEY = get_required_env("NETTS_API_KEY")
NETTS_ORDER_URL_1H = os.getenv("NETTS_ORDER_URL_1H", "https://netts.io/apiv2/order1h")
NETTS_ORDER_URL_5M = os.getenv("NETTS_ORDER_URL_5M", "https://netts.io/apiv2/order5m")
NETTS_PRICING_URL = os.getenv("NETTS_PRICING_URL", "https://netts.io/apiv2/pricing")
