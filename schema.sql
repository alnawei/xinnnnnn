# models.py
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, Numeric, Boolean, DateTime, ForeignKey, Text
from config import DATABASE_URL
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship

# ==================== 初始化数据库连接与会话池 ====================
# 🛡️ SRE 加固：扩容高并发连接池，开启 pool_pre_ping 防断联假死
engine = create_async_engine(
    DATABASE_URL, 
    pool_size=100,             
    max_overflow=200,          
    pool_timeout=30,           
    pool_pre_ping=True,        
    pool_recycle=1800,         
    echo=False
)

# 🛡️ 核心：使用 async_sessionmaker 替代旧版的 sessionmaker，彻底消除懒加载冲突
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
    clone_bot_price_usdt = Column(Numeric(10, 2), default=0)  
    base_cost_65k = Column(Numeric(18, 6), default=0)
    base_cost_131k = Column(Numeric(18, 6), default=0)
    netts_cost_65k = Column(Numeric(18, 6), default=0)    
    netts_cost_131k = Column(Numeric(18, 6), default=0)   
    special_base_cost_65k = Column(Numeric(18, 6), default=0)
    special_base_cost_131k = Column(Numeric(18, 6), default=0)
    unactivated_fee_trx = Column(Numeric(18, 6), default=2.0)
    
    # --- 商业核心开关 --- 
    clone_fee_config = Column(String(255), default="30-29.9,365-299")      
    is_special_energy_global_enabled = Column(Boolean, default=True)       
    
    # --- 其他全局设置 ---
    show_customer_service = Column(Boolean, default=True)
    customer_service_link = Column(String(255))
    global_welcome_template = Column(Text)
    min_withdraw_amount = Column(Numeric(18, 6), default=100.0)
    zombie_tenant_days = Column(Integer, default=30)
    tron_api_keys = Column(String(1024), default="")  
    special_auth_config = Column(String(1024), default="{}")
    
    # --- 超管财务预警与身份同步配置 ---
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
    special_price_65k = Column(Numeric(18, 6), default=0) 
    special_price_131k = Column(Numeric(18, 6), default=0) 
    special_energy_duration = Column(String(10), default='1h') 
    
    # 💡 优化：加入关系映射，方便查询
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")

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
    
    # 💡 优化：加入关系映射
    tenant = relationship("Tenant", back_populates="users")
    receive_addresses = relationship("UserReceiveAddress", back_populates="user", cascade="all, delete-orphan")

# ==================== 6. 消费者能量接收地址表 ====================
class UserReceiveAddress(Base):
    __tablename__ = 'user_receive_addresses'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    address = Column(String(34), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 💡 优化：加入关系映射
    user = relationship("User", back_populates="receive_addresses")

# ==================== 7. 微小尾数充值订单表 ====================
class MicroDepositOrder(Base):
    __tablename__ = 'micro_deposit_orders'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    base_amount = Column(Integer, nullable=False)
    
    # 🚨 极度致命修复：坚决保留你的 3 位小数降级防撞库业务逻辑！
    fractional_amount = Column(Numeric(4, 3), nullable=False)
    expected_amount = Column(Numeric(10, 3), nullable=False)
    
    # 💡 优化：Enum 降级为 String(50)，彻底解除 ORM 锁死隐患
    status = Column(String(50), default='PENDING')
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
    
    # 💡 优化：Enum 降级为 String(50)
    status = Column(String(50), default='PENDING')
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
    
    # 💡 优化：Enum 降级为 String(50)
    order_type = Column(String(50), nullable=False) 
    target_address = Column(String(34), nullable=False)
    admin_base_cost = Column(Numeric(18, 6), default=0)
    tenant_markup = Column(Numeric(18, 6), default=0)
    is_unactivated_fee_charged = Column(Boolean, default=False)
    total_user_deducted = Column(Numeric(18, 6), default=0)
    
    # 💡 优化：Enum 降级为 String(50)
    status = Column(String(50), default='PENDING')
    tx_hash = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ==================== 10. 防双花交易记录表 ====================
class ProcessedTx(Base):
    __tablename__ = 'processed_txs'
    tx_hash = Column(String(64), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
# ==================== 11. 代理激活码表 ====================
class ActivationCode(Base):
    __tablename__ = 'activation_codes'
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), unique=True, nullable=False)
    duration_days = Column(Integer, nullable=False, default=30) 
    includes_special_energy = Column(Boolean, default=False)    
    is_used = Column(Boolean, default=False)
    used_by_tg_id = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)
    
# ==================== 12. SaaS 订阅开通订单表 ====================
class SaaSOrder(Base):
    __tablename__ = 'saas_orders'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tg_user_id = Column(BigInteger, nullable=False)
    order_type = Column(String(32), nullable=False) 
    days = Column(String(32), nullable=False)       
    price = Column(Numeric(10, 2), nullable=False)  
    status = Column(String(32), default='PENDING')  
    created_at = Column(DateTime, default=datetime.utcnow)
