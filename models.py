# models.py
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, Numeric, Boolean, Date, DateTime, Enum, ForeignKey, Text
from config import DATABASE_URL
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
# ==================== 初始化数据库连接与会话池 ====================
# 这里就是 AsyncSessionLocal 的定义位置，供全局调用
# 🛡️ SRE 加固：扩容高并发连接池，开启 pool_pre_ping 防断联假死
engine = create_async_engine(
    DATABASE_URL, 
    pool_size=10,             # 常驻基础连接数扩容至 （10-100）
    max_overflow=20,          # 流量洪峰时最大允许 （20-200） 个溢出连接
    pool_timeout=30,           # 获取连接的最高等待时间
    pool_pre_ping=True,        # 每次使用前 Ping 一下 MySQL，断线自动重连！
    pool_recycle=1800,         # 连接存活期缩短至半小时，防服务端强制断开
    echo=False
)

# 🛡️ 核心修复：使用 async_sessionmaker 替代旧版的 sessionmaker，彻底消除 MissingGreenlet 懒加载冲突
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# ==================== 1. 系统全局配置表 ====================
class SystemConfig(Base):
    __tablename__ = 'system_configs'
    id = Column(Integer, primary_key=True, default=1)
    
    # --- 基础与收款配置 ---
    master_receive_address = Column(String(34), nullable=False)
    global_special_address = Column(String(34))
    
    # --- 定价与底价配置 ---
    clone_bot_price_usdt = Column(Numeric(10, 2), default=0)  # 保留兼容旧版
    base_cost_65k = Column(Numeric(18, 6), default=0)
    base_cost_131k = Column(Numeric(18, 6), default=0)
    netts_cost_65k = Column(Numeric(18, 6), default=0)    # 新增：65K上游采购进货成本
    netts_cost_131k = Column(Numeric(18, 6), default=0)   # 新增：131K上游采购进货成本
    special_base_cost_65k = Column(Numeric(18, 6), default=0)
    special_base_cost_131k = Column(Numeric(18, 6), default=0)
    unactivated_fee_trx = Column(Numeric(18, 6), default=2.0)
    
    # 👇 --- 本次新增补齐的核心字段 --- 👇
    clone_fee_config = Column(String(255), default="30-29.9,365-299")      # 新增：存储克隆多套餐配置字符串
    is_special_energy_global_enabled = Column(Boolean, default=True)       # 新增：控制特价能量全局开关
    # --- 其他全局设置 ---
    show_customer_service = Column(Boolean, default=True)
    customer_service_link = Column(String(255))
    global_welcome_template = Column(Text)
    min_withdraw_amount = Column(Numeric(18, 6), default=100.0)
    zombie_tenant_days = Column(Integer, default=30)
    tron_api_keys = Column(String(1024), default="")  # 新增：用于存储API节点池
    special_auth_config = Column(String(1024), default="{}")
    
    # --- 新增：超管财务预警与身份同步配置 ---
    super_admin_tg_id = Column(String(50), nullable=True)
    netts_alert_threshold = Column(Numeric(18, 2), default=50.0)
    tenant_alert_threshold = Column(Numeric(18, 2), default=15.0)

# ==================== 2. 波场 API 高可用节点池 ====================
class TronApiNode(Base):
    __tablename__ = 'tron_api_nodes'
    id = Column(Integer, primary_key=True, autoincrement=True)
    api_key = Column(String(128), nullable=False)
    rpc_url = Column(String(255), default='https://api.trongrid.io')
    weight = Column(Integer, default=10)
    is_active = Column(Boolean, default=True)
    fail_count = Column(Integer, default=0)
    last_used_at = Column(DateTime)

# ==================== 3. 链上扫块指针表 ====================
class BlockScanPointer(Base):
    __tablename__ = 'block_scan_pointers'
    job_name = Column(String(64), primary_key=True)
    last_scanned_block = Column(BigInteger, nullable=False)
    address = Column(String(64))
    asset_type = Column(String(16))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ==================== 4. 核心租户表 ====================
class Tenant(Base):
    __tablename__ = 'tenants'
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_tg_id = Column(BigInteger, unique=True, nullable=False)
    bot_token = Column(String(128), unique=True, nullable=False)
    deposit_balance = Column(Numeric(18, 6), default=0)
    profit_balance = Column(Numeric(18, 6), default=0)
    withdraw_address = Column(String(34))
    is_active = Column(Boolean, default=True)
    expire_time = Column(DateTime, nullable=False)
    last_active_time = Column(DateTime, default=datetime.utcnow)
    markup_65k = Column(Numeric(18, 6), default=0)
    markup_131k = Column(Numeric(18, 6), default=0)
    has_special_energy_right = Column(Boolean, default=False)
    show_special_energy = Column(Boolean, default=True)
    special_energy_address = Column(String(34))
    markup_special = Column(Numeric(18, 6), default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_banned = Column(Boolean, default=False)
    special_price_65k = Column(Numeric(18, 6), default=0) # 65K 特价绝对售价 (0代表未开启)
    special_price_131k = Column(Numeric(18, 6), default=0) # 131K 特价绝对售价 (0代表未开启)
    special_energy_duration = Column(String(10), default='1h') # 新增：特价能量时效 ('5m' 或 '1h')

# ==================== 5. C端消费者表 ====================
class User(Base):
    __tablename__ = 'users'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False)
    tg_user_id = Column(BigInteger, nullable=False)
    tg_first_name = Column(String(128))
    balance = Column(Numeric(18, 6), default=0)
    withdraw_address = Column(String(34))
    total_orders = Column(Integer, default=0)
    total_spent_trx = Column(Numeric(18, 6), default=0)
    default_receive_address_id = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.utcnow)

# ==================== 6. 消费者能量接收地址表 ====================
class UserReceiveAddress(Base):
    __tablename__ = 'user_receive_addresses'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    address = Column(String(34), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# ==================== 7. 微小尾数充值订单表 ====================
class MicroDepositOrder(Base):
    __tablename__ = 'micro_deposit_orders'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    base_amount = Column(Integer, nullable=False)
    fractional_amount = Column(Numeric(4, 3), nullable=False)
    expected_amount = Column(Numeric(10, 3), nullable=False)
    status = Column(Enum('PENDING', 'SUCCESS', 'EXPIRED'), default='PENDING')
    tx_hash = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)
    expired_at = Column(DateTime, nullable=False)

# ==================== 8. 提现审核订单表 ====================
class WithdrawOrder(Base):
    __tablename__ = 'withdraw_orders'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, nullable=False)
    amount = Column(Numeric(18, 6), nullable=False)
    target_address = Column(String(34), nullable=False)
    status = Column(Enum('PENDING', 'PAID', 'REJECTED'), default='PENDING')
    tx_hash = Column(String(64))
    reject_reason = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    handled_at = Column(DateTime)

# ==================== 9. 核心交易能量派发订单表 ====================
class EnergyOrder(Base):
    __tablename__ = 'energy_orders'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, nullable=False)
    user_id = Column(BigInteger)
    order_type = Column(Enum('BALANCE_65K', 'BALANCE_131K', 'DIRECT_SPECIAL', 'DIRECT_SPECIAL_65K', 'DIRECT_SPECIAL_131K'), nullable=False)
    target_address = Column(String(34), nullable=False)
    admin_base_cost = Column(Numeric(18, 6), default=0)
    tenant_markup = Column(Numeric(18, 6), default=0)
    is_unactivated_fee_charged = Column(Boolean, default=False)
    total_user_deducted = Column(Numeric(18, 6), default=0)
    status = Column(Enum('PENDING', 'SUCCESS', 'FAILED_REFUNDED', 'FAILED_SILENT', 'PROCESSING', 'MANUAL_REVIEW'), default='PENDING')
    tx_hash = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ==================== 10. 防双花交易记录表 ====================
class ProcessedTx(Base):
    __tablename__ = 'processed_txs'
    tx_hash = Column(String(64), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ==================== 11. 财务日报汇总表 ====================
class FinancialDailySummary(Base):
    __tablename__ = 'financial_daily_summaries'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    summary_date = Column(Date, nullable=False)
    tenant_id = Column(Integer, nullable=False, default=0)
    deposit_success_count = Column(Integer, default=0)
    deposit_trx = Column(Numeric(18, 6), default=0)
    energy_success_count = Column(Integer, default=0)
    energy_refund_count = Column(Integer, default=0)
    energy_failed_count = Column(Integer, default=0)
    energy_user_paid_trx = Column(Numeric(18, 6), default=0)
    energy_refund_trx = Column(Numeric(18, 6), default=0)
    admin_cost_trx = Column(Numeric(18, 6), default=0)
    tenant_profit_trx = Column(Numeric(18, 6), default=0)
    withdraw_paid_count = Column(Integer, default=0)
    withdraw_paid_trx = Column(Numeric(18, 6), default=0)
    withdraw_rejected_count = Column(Integer, default=0)
    withdraw_rejected_trx = Column(Numeric(18, 6), default=0)
    saas_paid_count = Column(Integer, default=0)
    saas_paid_usdt = Column(Numeric(18, 6), default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ==================== 12. 代理激活码表 ====================
class ActivationCode(Base):
    __tablename__ = 'activation_codes'
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), unique=True, nullable=False)
    duration_days = Column(Integer, nullable=False, default=30) # 新增：有效期天数
    includes_special_energy = Column(Boolean, default=False)    # 新增：是否包含特价能量
    is_used = Column(Boolean, default=False)
    used_by_tg_id = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)
    
# ==================== 13. SaaS 订阅开通订单表 ====================
class SaaSOrder(Base):
    __tablename__ = 'saas_orders'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tg_user_id = Column(BigInteger, nullable=False)
    order_type = Column(String(32), nullable=False) # 'clone' (机器人主程序) 或 'special' (特价插件)
    days = Column(String(32), nullable=False)       # 购买天数
    price = Column(Numeric(10, 2), nullable=False)  # 需支付金额(USDT)
    status = Column(String(32), default='PENDING')  # PENDING(待支付), PAID(已支付)
    created_at = Column(DateTime, default=datetime.utcnow)
