
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def build_admin_keyboard() -> ReplyKeyboardMarkup:
    """超管端底部键盘 (1-2-2-1 布局)"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 平台核心数据总览 (大盘看板)")
    builder.button(text="💰 定价管理")
    builder.button(text="⚙️ 全局设置")
    builder.button(text="👥 租户管理")
    builder.button(text="💰 财务管理")
    builder.button(text="📢 平台全员广播")
    builder.adjust(1, 2, 2, 1)
    return builder.as_markup(resize_keyboard=True, is_persistent=True)

def build_tenant_keyboard(is_special_energy_global_enabled: bool) -> ReplyKeyboardMarkup:
    """
    租户端底部物理键盘重构版
    包含超管全局权限鉴权：仅根据系统全局开关决定是否显示特价能量菜单
    """
    builder = ReplyKeyboardBuilder()
    
    # 第一排
    builder.row(
        KeyboardButton(text="🏠 个人中心"),
        KeyboardButton(text="💰 加价设置"),
        KeyboardButton(text="⚙️ 机器设置")
    )
    # 第二排
    builder.row(
        KeyboardButton(text="🛒 续费机器"),
        KeyboardButton(text="📊 账户流水")
    )
    
    # 第三排（动态加载：仅受超管后台全局开关控制）
    if is_special_energy_global_enabled:
        builder.row(
            KeyboardButton(text="⚡ 开通特价"),
            KeyboardButton(text="⚙️ 特价设置")
        )
        
    return builder.as_markup(resize_keyboard=True, is_persistent=True)

def build_user_main_keyboard(show_special: bool) -> ReplyKeyboardMarkup:
    """
    动态构建C端用户主菜单
    :param show_special: 是否展示特价能量按钮
    """
    builder = ReplyKeyboardBuilder()
    
    # 第一排：特价能量（动态追加）
    if show_special:
        builder.row(KeyboardButton(text="🔥 特价能量"))
        
    # 第二排：立即租用 与 充值帐户
    builder.row(
        KeyboardButton(text="🛒 立即租用"),
        KeyboardButton(text="👤 个人中心")
    )
    
    # 第三排：地址管理 与 克隆机器人
    builder.row(
        KeyboardButton(text="📍 地址管理"),
        KeyboardButton(text="🤖 克隆机器人")
    )
    
    return builder.as_markup(resize_keyboard=True)
