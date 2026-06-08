# config.py

# ================= 1. 基础与数据库配置 =================
DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_USER = "bot"
DB_PASSWORD = "dWNWJNSxxxxx"
DB_NAME = "bot"  # 注意：这里改成了第一步DDL中的新库名

# 拼接异步数据库 URI，供新版的 AsyncSessionLocal 使用
DATABASE_URL = f"mysql+aiomysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

SCAN_INTERVAL = 5  # 波场节点扫块轮询间隔 (秒)

# ================= 2. SaaS 母平台核心配置 =================
SUPER_ADMIN_ID = 6474xxxxx             # 你的老板 TG ID
MASTER_BOT_TOKEN = "6645322183:AAHHEGshxxxxxxxxxxxxxxx" 
# SaaS 母平台机器人的跳转链接（请替换为您真实的机器人 Username 链接）
SAAS_BOT_URL = "https://t.me/YourSaaSBotUsername"

# ================= 3. 供应链与发货配置 (Netts API) =================
SERVER_IP = "47.2xxxxxxx"           # 绑定 Netts 的白名单公网 IP
NETTS_API_KEY = "5350bb644xxxxxxxx"   
NETTS_ORDER_URL_1H = "https://netts.io/apiv2/order1h"
NETTS_ORDER_URL_5M = "https://netts.io/apiv2/order5m"
NETTS_PRICING_URL = "https://netts.io/apiv2/pricing"
