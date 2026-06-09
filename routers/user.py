# routers/user.py

import json
import random
import asyncio
from decimal import Decimal
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State, StatesGroup
from filters.role import RoleFilter
from netts_api import delegate_energy
# 必须确保导入了 SaasOrder 和 SystemConfig
from models import SystemConfig, Tenant, User, UserReceiveAddress, MicroDepositOrder, EnergyOrder, SaaSOrder
# 导入您项目对应的模型与统一的主回复键盘构建器
from config import SAAS_BOT_URL
from keyboards.reply import build_user_main_keyboard
from sqlalchemy import select, desc, text
from tron_scanner import handle_balance_purchase
from tron_utils import is_valid_tron_address
import re  # 👈 追加这行：导入正则表达式模块


async def acquire_mysql_lock(session: AsyncSession, lock_name: str, timeout: int = 3) -> bool:
    result = await session.execute(text("SELECT GET_LOCK(:name, :timeout)"), {"name": lock_name, "timeout": timeout})
    return result.scalar() == 1


async def release_mysql_lock(session: AsyncSession, lock_name: str) -> None:
    await session.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})


# =========================================================
# 散客充值业务状态机定义
# =========================================================
class UserDepositFSM(StatesGroup):
    waiting_for_amount = State()
class UserAddressFSM(StatesGroup):
    waiting_for_address = State()
# =========================================================
# 散客充值 FSM 状态机 (10分钟尾数排重版)
# =========================================================
class TopUpFSM(StatesGroup):
    waiting_for_amount = State()
# =========================================================
# 克隆机器人 FSM 状态机
# =========================================================
class CloneBotFSM(StatesGroup):
    waiting_for_token = State()
# =========================================================
# 辅助函数：构建地址管理的内联键盘 (追加至 user.py 全局作用域中)
# =========================================================
def build_address_manage_keyboard(addresses: list, default_addr_id: int = None) -> InlineKeyboardMarkup:
    """构建地址管理的内联键盘 (悬浮按钮交互版)"""
    builder = InlineKeyboardBuilder()
    
    for addr in addresses:
        # 为了防止手机端按钮文字溢出难看，将 34 位地址缩略显示
        short_addr = f"{addr.address[:10]}...{addr.address[-6:]}"
        
        # 使用传入的 default_addr_id 进行比对，而不是 addr.is_default
        mark = "✅ " if addr.id == default_addr_id else ""
        btn_text = f"{mark}{short_addr}"
        
        # 将地址按钮和删除按钮放在同一排
        builder.row(
            InlineKeyboardButton(text=btn_text, callback_data=f"set_default_addr_{addr.id}"),
            InlineKeyboardButton(text="🗑️ 删除", callback_data=f"del_addr_{addr.id}")
        )
        
    # 如果地址不满 5 个，则在最下方显示添加按钮
    if len(addresses) < 5:
        builder.row(InlineKeyboardButton(text="➕ 添加新地址", callback_data="add_new_address"))
        
    return builder.as_markup()
# =========================================================
# 辅助函数：构建充值快捷金额内联键盘 (追加至 user.py 全局作用域)
# =========================================================
def build_topup_keyboard() -> InlineKeyboardMarkup:
    """构建充值金额选择键盘"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="10 TRX", callback_data="topup_10"),
        InlineKeyboardButton(text="50 TRX", callback_data="topup_50"),
        InlineKeyboardButton(text="100 TRX", callback_data="topup_100")
    )
    builder.row(InlineKeyboardButton(text="✏️ 自定义金额", callback_data="topup_custom"))
    return builder.as_markup()
# =========================================================
# 核心工厂函数：生成独立 Router 实例，防止多 Bot 挂载冲突
# =========================================================
def get_user_router() -> Router:
    router = Router(name="user_router")

    # 放行所有权限，确保代理老板和超管测试时也能走通 C 端页面
    router.message.filter(RoleFilter(["user", "guest", "tenant", "admin"]))
    router.callback_query.filter(RoleFilter(["user", "guest", "tenant", "admin"]))

    # =========================================================
    # 模块 A：全局入口 (/start) 自动分流与伪卡片排版
    # =========================================================
    @router.message(StateFilter('*'), Command("start"))
    async def user_start(message: Message, current_tenant, state: FSMContext, session: AsyncSession, current_user=None):
        await state.clear()
        
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        
        # 场景 A - 访问的是【SaaS 母平台】 (没有命中子机器人租户数据)
        if not current_tenant:
            show_cs = getattr(sys_config, "show_customer_service", True)
            
            # 动态构建底部物理键盘
            reply_builder = ReplyKeyboardBuilder()
            reply_builder.row(KeyboardButton(text="🚀 立即开通/克隆我的机器人"))
            
            if show_cs:
                reply_builder.row(
                    KeyboardButton(text="📖 商业赚钱模式介绍"),
                    KeyboardButton(text="💁 联系官方客服")
                )
            else:
                reply_builder.row(KeyboardButton(text="📖 商业赚钱模式介绍"))
            
            reply_markup = reply_builder.as_markup(resize_keyboard=True, is_persistent=True)

            await message.answer(
                "👋 <b>欢迎来到波场能量全自动分销 SaaS 平台！</b>\n\n"
                "无需任何技术基础，一键克隆拥有属于您自己的全自动能量售卖机器人，轻松开启被动收入！\n\n"
                "👇 请使用下方菜单栏选择您需要的服务：",
                reply_markup=reply_markup, 
                parse_mode="HTML"
            )
            return

        # 场景 B - 访问的是【代理的克隆子机器人】 (展示 C 端消费菜单)
        tg_user = message.from_user
        
        # 自动注册：检测并初始化 C 端用户记录，保证账本数据完整
        if not current_user:
            current_user = await session.scalar(
                select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
            )
            if not current_user:
                current_user = User(
                    tenant_id=current_tenant.id,
                    tg_user_id=tg_user.id,
                    tg_first_name=tg_user.first_name,
                    balance=Decimal('0.00'),
                    total_orders=0,
                    total_spent_trx=Decimal('0.00')
                )
                session.add(current_user)
                await session.commit()
                await session.refresh(current_user)

        # 动态计算普通能量实时销售零售价
        netts_65k = Decimal(str(getattr(sys_config, 'netts_cost_65k', 0.0) or 0.0))
        netts_131k = Decimal(str(getattr(sys_config, 'netts_cost_131k', 0.0) or 0.0))
        draw_65k = Decimal(str(sys_config.base_cost_65k or 0.0))
        draw_131k = Decimal(str(sys_config.base_cost_131k or 0.0))
        
        agent_cost_65k = netts_65k + draw_65k
        agent_cost_131k = netts_131k + draw_131k
        markup_65k = Decimal(str(current_tenant.markup_65k or 0.0))
        markup_131k = Decimal(str(current_tenant.markup_131k or 0.0))
        
        price_65k_val_raw = agent_cost_65k + markup_65k
        price_131k_val_raw = agent_cost_131k + markup_131k
        
        # 双轨制特价隔离与兜底决策逻辑
        show_special = False
        special_address = ""
        show_65k = False
        show_131k = False
        
        if getattr(current_tenant, "has_special_energy_right", False):
            # 租户已开通特价功能
            show_65k = float(getattr(current_tenant, "special_price_65k", 0) or 0) > 0
            show_131k = float(getattr(current_tenant, "special_price_131k", 0) or 0) > 0
            if show_65k or show_131k:
                show_special = True
                special_address = getattr(current_tenant, "special_energy_address", "")
        else:
            # 租户未开通特价功能，由超管全局售价兜底
            is_global_enabled = getattr(sys_config, "is_special_energy_global_enabled", True)
            if is_global_enabled:
                show_65k = float(getattr(sys_config, "special_base_cost_65k", 0) or 0) > 0
                show_131k = float(getattr(sys_config, "special_base_cost_131k", 0) or 0) > 0
                if show_65k or show_131k:
                    show_special = True
                    special_address = getattr(sys_config, "global_special_address", "")

        # 提取账户与基准价格数据，保证显示没有多余的小数点和零
        user_balance = f"{float(getattr(current_user, 'balance', 0.0) or 0.0):g}"
        total_orders = int(getattr(current_user, "total_orders", 0) or 0)
        total_spent = f"{float(getattr(current_user, 'total_spent_trx', 0.0) or 0.0):g}"
        price_65k_val = f"{float(price_65k_val_raw):g}"
        price_131k_val = f"{float(price_131k_val_raw):g}"

        # 1. 动态构建特价费率的树状线条
                # =====================================================================
        # 🎯 核心逻辑：特价能量三级向下兜底渲染 (Agent -> Admin -> None)
        # =====================================================================
        show_special = False
        special_address = ""
        special_price_text = ""
        dur_cn = ""
        
        # 提前拉取超管配置兜底备用
        g_65k = float(getattr(sys_config, 'special_base_cost_65k', 0.0) or 0.0)
        g_131k = float(getattr(sys_config, 'special_base_cost_131k', 0.0) or 0.0)
        g_addr = sys_config.global_special_address if sys_config else None

        # 尝试读取代理商自己的配置
        t_65k = 0.0
        t_131k = 0.0
        t_addr = None
        if current_tenant and getattr(current_tenant, 'has_special_energy_right', False):
            t_65k = float(getattr(current_tenant, 'special_price_65k', 0.0) or 0.0)
            t_131k = float(getattr(current_tenant, 'special_price_131k', 0.0) or 0.0)
            t_addr = current_tenant.special_energy_address

        # 🥇 优先级 1：代理商自营特价 (有特权，且至少设了一个价格，且设置了发货地址)
        if (t_65k > 0 or t_131k > 0) and t_addr:
            show_special = True
            special_address = t_addr
            dur_val = getattr(current_tenant, "special_energy_duration", "1h")
            dur_cn = "5分钟" if dur_val == "5m" else "1小时"
            
            if t_65k > 0 and t_131k > 0:
                special_price_text = (
                    f"\n├ 1️⃣ <b>免费转 1 笔 U (65K)</b>：{t_65k:g} TRX"
                    f"\n├ 2️⃣ <b>免费转 2 笔 U (131K)</b>：{t_131k:g} TRX"
                )
            elif t_65k > 0:
                special_price_text = f"\n├ 1️⃣ <b>免费转 1 笔 U (65K)</b>：{t_65k:g} TRX"
            elif t_131k > 0:
                special_price_text = f"\n├ 2️⃣ <b>免费转 2 笔 U (131K)</b>：{t_131k:g} TRX"

        # 🥈 优先级 2：超管全局兜底 (代理没开特价，或者把价格设为了 0) -> 走平台直营
        elif (g_65k > 0 or g_131k > 0) and g_addr:
            show_special = True
            special_address = g_addr
            dur_cn = "5分钟"  # 💡 平台直营兜底强制时效 5 分钟，利润最大化
            
            if g_65k > 0 and g_131k > 0:
                special_price_text = (
                    f"\n├ 1️⃣ <b>免费转 1 笔 U (65K)</b>：{g_65k:g} TRX"
                    f"\n├ 2️⃣ <b>免费转 2 笔 U (131K)</b>：{g_131k:g} TRX"
                )
            elif g_65k > 0:
                special_price_text = f"\n├ 1️⃣ <b>免费转 1 笔 U (65K)</b>：{g_65k:g} TRX"
            elif g_131k > 0:
                special_price_text = f"\n├ 2️⃣ <b>免费转 2 笔 U (131K)</b>：{g_131k:g} TRX"

        # 2. 拼接 /start 完整欢迎语上半部分
        welcome_text = (
            "⚡️ <b>波场全自动能量分销系统</b>\n\n"
            "<b>👤 我的账户</b>\n"
            f"├ 昵称：{tg_user.first_name}\n"
            f"├ 编号：<code>{tg_user.id}</code>\n"
            f"├ 余额：{user_balance} TRX\n"
            f"└ 数据：{total_orders} 笔 | 共消费 {total_spent} TRX\n\n"
            
            "<b>💡 平台实时基准价</b>\n"
            f"├ 🟢 对方有 U：{float(price_65k_val):.2f} TRX / 笔\n"
            f"└ 🟡 对方无 U：{float(price_131k_val):.2f} TRX / 笔\n\n"
        )
        
        # 🥉 优先级 3：动态追加特价卡片区域 (如果 show_special 为 False，直接跳过不显示)
        if show_special and special_address:
            welcome_text += (
                "<b>💥 特价费率：</b>"
                f"{special_price_text}\n"
                "├ 📥 <b>使用教程</b>：转账对应数量的 TRX 到下面地址，3秒后再去转 U 不扣 TRX 手续费！\n"
                f"├ ⏱ <b>时效说明</b>：能量 <b>{dur_cn}</b> 内有效，到期自动收回。\n"
                "└ ✅ <b>能量租用地址 (点击自动复制)</b>：\n"
                f"<code>{special_address}</code>\n"
                "   <i>(付款后立即生效，秒到不提醒)</i>\n\n"
                "👇 💡 强烈建议您将此地址<b>【点击复制】</b>并保存至您的收藏夹，随时转账，随时秒到！\n\n"
            )
        # 获取用户默认绑定的接收地址 (对应 default_receive_address_id)
        default_address = None
        if current_user and current_user.default_receive_address_id:
            default_address = await session.scalar(
                select(UserReceiveAddress).where(UserReceiveAddress.id == current_user.default_receive_address_id)
            )
        
        # 收尾部分：地址与提示 (自动兼容绑定状态，无地址时不加 code 复制标签)
        if default_address:
            welcome_text += (
                "<b>📍 默认接收地址</b>\n"
                f"<code>{default_address.address}</code>\n\n"
            )
        else:
            welcome_text += (
                "<b>📍 默认接收地址</b>\n"
                "⚠️ 暂未绑定(请先点击下方📍 地址管理添加)\n\n"
            )
            
        welcome_text += "👇 请使用底部键盘选择所需服务："

        reply_markup = build_user_main_keyboard(show_special=show_special)
        await message.answer(welcome_text, reply_markup=reply_markup, parse_mode="HTML")

    # =========================================================
    # 模块 A2：“特价能量”底部回复菜单点击监听器 (独立展示特价面板)
    # =========================================================
    @router.message(StateFilter('*'), F.text.in_(["🔥 特价能量"]))
    async def cmd_special_energy_handler(message: Message, current_tenant, session: AsyncSession):
        """
        独立响应“特价能量”按钮，仅展示纯净且对齐的特价卡片排版
        """
        if not current_tenant:
            return  # 仅限子机器人调用，防御安全
            
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        
        # 1. 严格执行双轨制特价隔离与兜底决策逻辑
        show_special = False
        special_address = ""
        show_65k = False
        show_131k = False
        
        if getattr(current_tenant, "has_special_energy_right", False):
            show_65k = float(getattr(current_tenant, "special_price_65k", 0) or 0) > 0
            show_131k = float(getattr(current_tenant, "special_price_131k", 0) or 0) > 0
            if show_65k or show_131k:
                show_special = True
                special_address = getattr(current_tenant, "special_energy_address", "")
        else:
            is_global_enabled = getattr(sys_config, "is_special_energy_global_enabled", True)
            if is_global_enabled:
                show_65k = float(getattr(sys_config, "special_base_cost_65k", 0) or 0) > 0
                show_131k = float(getattr(sys_config, "special_base_cost_131k", 0) or 0) > 0
                if show_65k or show_131k:
                    show_special = True
                    special_address = getattr(sys_config, "global_special_address", "")

        # 2. 拦截判定：如果当前租户或超管全局把特价关闭了（即 show_special 为 False）
        if not show_special or not special_address:
            await message.answer("❌ 抱歉，当前暂无正在进行的限时特价活动。")
            return

        # 格式化特售价数据，去除多余的零
        price_65k_special = f"{float(current_tenant.special_price_65k if getattr(current_tenant, 'has_special_energy_right', False) else sys_config.special_base_cost_65k):g}"
        price_131k_special = f"{float(current_tenant.special_price_131k if getattr(current_tenant, 'has_special_energy_right', False) else sys_config.special_base_cost_131k):g}"

        # 3. 动态构建树状费率线条
        special_price_text = ""
        if show_65k and show_131k:
            special_price_text = (
                f"\n├ 1️⃣ <b>免费转 1 笔 U (65K)</b>：{price_65k_special} TRX"
                f"\n└ 2️⃣ <b>免费转 2 笔 U (131K)</b>：{price_131k_special} TRX"
            )
        elif show_65k:
            special_price_text = f"\n└ 1️⃣ <b>免费转 1 笔 U (65K)</b>：{price_65k_special} TRX"
        elif show_131k:
            special_price_text = f"\n└ 2️⃣ <b>免费转 2 笔 U (131K)</b>：{price_131k_special} TRX"

        # 💡 新增：获取时效展示文案。租户取自身配置，超管兜底强制显示为 5 分钟
        if getattr(current_tenant, "has_special_energy_right", False):
            dur_val = getattr(current_tenant, "special_energy_duration", "1h")
        else:
            dur_val = "5m"
        dur_cn = "5分钟" if dur_val == "5m" else "1小时"

        # 4. 组装并回复纯净特价卡片文案
        special_card_text = (
            "⚡️ <b>专属限时特价能量供应</b>\n\n"
            "<b>💥 特价费率：</b>"
            f"{special_price_text}\n"
            "├ 📥 <b>使用教程</b>：转账对应数量的 TRX 到下面地址，3秒后再去转 U 不扣 TRX 手续费！\n"
            f"├ ⏱ <b>时效说明</b>：能量 <b>{dur_cn}</b> 内有效，到期自动收回。\n"  # 👈 核心修改：动态插入时效说明
            "└ ✅ <b>能量租用地址 (点击自动复制)</b>：\n"
            f"<code>{special_address}</code>\n"
            "   <i>(付款后立即生效，秒到不提醒)</i>\n\n"
            "👇 💡 强烈建议您将此地址<b>【点击复制】</b>并保存至您的收藏夹，随时转账，随时秒到！"
        )

        await message.answer(special_card_text, parse_mode="HTML")

    # =========================================================
    # 模块 B：母平台 - SaaS 购买流直接渲染（跳过中间分流菜单）
    # =========================================================
    @router.message(StateFilter('*'), F.text == "🚀 立即开通/克隆我的机器人")
    async def saas_clone_bot_handler(message: Message, current_tenant, session: AsyncSession):
        if current_tenant: 
            return # 仅限母平台触发
            
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        clone_fee_str = getattr(sys_config, "clone_fee_config", "{}")

        try:
            clone_data = json.loads(clone_fee_str)
        except Exception:
            clone_data = {"_is_open": True}

        # 1. 检查总平台克隆套餐开启状态
        is_open = clone_data.get("_is_open", True)
        if not is_open:
            return await message.answer("⚠️ 该克隆机器人套餐购买暂时暂停开放。")

        clone_pkgs = {k: v for k, v in clone_data.items() if k != "_is_open"}
        sorted_clone = sorted(clone_pkgs.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)

        if not sorted_clone:
            return await message.answer("⚠️ <b>当前暂无可用的套餐数据，请联系客服。</b>", parse_mode="HTML")

        text = (
            "🛒 <b>请选择您要开通的商铺套餐：</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ 独立专属数字分销商铺\n"
            "✅ 自定义高溢价，纯利 100% 归你\n"
            "✅ 对接全网极速低价能量池\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "👇 <b>请点击下方按钮选择：</b>"
        )
        
        kb_builder = InlineKeyboardBuilder()
        for day in sorted_clone:
            price = clone_pkgs[day]
            day_label = f"{day}天" if str(day).isdigit() else str(day)
            kb_builder.row(InlineKeyboardButton(
                text=f"💎 {day_label} 授权版 - {float(price):.1f} USDT", 
                callback_data=f"buy_pkg:clone:{day}"
            ))
        
        kb_builder.row(InlineKeyboardButton(text="❌ 关闭菜单", callback_data="close_saas_menu"))

        await message.answer(text, reply_markup=kb_builder.as_markup(), parse_mode="HTML")

    # 辅助拦截：处理 [❌ 关闭菜单] 动作
    @router.callback_query(F.data == "close_saas_menu")
    async def close_saas_menu_handler(call: CallbackQuery):
        await call.answer()
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass

    # 辅助拦截：返回套餐列表的回调逻辑
    # 🌟 修复：收银台一键平滑退回到最新极简套餐卡片
    @router.callback_query(F.data.startswith("saas_menu:"))
    async def saas_menu_category_handler(call: CallbackQuery, session: AsyncSession):
        await call.answer()  # 必须先响应，消除转圈状态
        
        parts = call.data.split(":")
        if len(parts) != 2:
            return
            
        pkg_type = parts[1]
        
        # 🛡️ 强制防越权拦截：若非克隆机器套餐，则直接关闭卡片（母平台C端不直接兜售特价插件）
        if pkg_type != "clone":
            try:
                await call.message.delete()
            except TelegramBadRequest:
                pass
            return

        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        clone_fee_str = getattr(sys_config, "clone_fee_config", "{}")

        try:
            clone_data = json.loads(clone_fee_str)
        except Exception:
            clone_data = {"_is_open": True}

        # 检查开放购买状态
        is_open = clone_data.get("_is_open", True)
        if not is_open:
            return await call.answer("⚠️ 该克隆机器人套餐购买暂时暂停开放", show_alert=True)

        clone_pkgs = {k: v for k, v in clone_data.items() if k != "_is_open"}
        sorted_clone = sorted(clone_pkgs.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)

        if not sorted_clone:
            try:
                await call.message.edit_text("⚠️ <b>当前暂无可用的套餐数据，请联系客服。</b>", parse_mode="HTML")
            except TelegramBadRequest:
                pass
            return

        # 100% 对齐主入口文本
        text = (
            "🛒 <b>请选择您要开通的商铺套餐：</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ 独立专属数字分销商铺\n"
            "✅ 自定义高溢价，纯利 100% 归你\n"
            "✅ 对接全网极速低价能量池\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "👇 <b>请点击下方按钮选择：</b>"
        )
        
        kb_builder = InlineKeyboardBuilder()
        for day in sorted_clone:
            price = clone_pkgs[day]
            day_label = f"{day}天" if str(day).isdigit() else str(day)
            kb_builder.row(InlineKeyboardButton(
                text=f"💎 {day_label} 授权版 - {float(price):.1f} USDT", 
                callback_data=f"buy_pkg:clone:{day}"
            ))
        
        # 底部返回链路中，仅保留该关闭按钮，彻底抹除多余的旧版中间层分类提示
        kb_builder.row(InlineKeyboardButton(text="❌ 关闭菜单", callback_data="close_saas_menu"))

        try:
            # 采用 edit_text 和 edit_reply_markup 在原消息卡片上实现无缝平滑回退
            await call.message.edit_text(text, reply_markup=kb_builder.as_markup(), parse_mode="HTML")
        except TelegramBadRequest:
            pass

    # 3. 点击具体套餐购买，创建独立 SaaSOrder 订单并渲染收银台
    # 3. 点击具体套餐购买，创建独立 SaaSOrder 订单并渲染收银台
    @router.callback_query(F.data.startswith("buy_pkg:"))
    async def saas_buy_pkg_callback(call: CallbackQuery, session: AsyncSession):
        parts = call.data.split(":")
        if len(parts) != 3: 
            return await call.answer()
            
        pkg_type = parts[1] # 'clone' 或 'special'
        day = parts[2]
        
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        
        if pkg_type == "clone":
            config_str = getattr(sys_config, "clone_fee_config", "{}")
            pkg_name = "独立专属子机器人授权"
        else:
            config_str = getattr(sys_config, "special_auth_config", "{}")
            pkg_name = "增值功能插件 (特价地址授权)"
            
        try: 
            fee_data = json.loads(config_str)
        except Exception: 
            fee_data = {}
        
        price = fee_data.get(day)
        if not price:
            await call.answer("❌ 该套餐不存在或已下架，请重新选择。", show_alert=True)
            return
            
        day_label = f"{day}天" if str(day).isdigit() else str(day)
        
        try:
            # 1. 拦截获取主收款地址
            if not sys_config or not sys_config.master_receive_address:
                await call.answer("❌ 平台未配置收款地址，请联系管理员！", show_alert=True)
                return
                
            master_address = sys_config.master_receive_address
            
            # 使用动态读取的套餐基础价格
            base_usdt_price = Decimal(str(price))

            # 2. 高并发防撞单：USDT 尾数碰撞拦截
            pending_stmt = select(SaaSOrder.price).where(
                SaaSOrder.status == "PENDING",
                SaaSOrder.order_type == pkg_type
            )
            pending_res = await session.execute(pending_stmt)
            used_prices = {row[0] for row in pending_res.all()}

            final_usdt_amount = base_usdt_price
            for i in range(1, 1000):
                step_amount = base_usdt_price + Decimal(str(i * 0.01))
                if step_amount not in used_prices:
                    final_usdt_amount = step_amount
                    break

            # 3. 干净利落的事务入库：只记录“谁要买、付多少钱”
            new_saas_order = SaaSOrder(
                tg_user_id=call.from_user.id,
                order_type=pkg_type,
                days=str(day),
                price=final_usdt_amount,       # 唯一尾数 USDT 金额
                status="PENDING"
                # ⚠️ 绝对不要在这里尝试写入 bot_token 或关联 Tenant，保持字段纯净
            )
            session.add(new_saas_order)
            await session.commit()

            # 4. 弹出 USDT 专属收银台
            cashier_text = (
                f"💵 <b>授权开通收银台 (USDT-TRC20)</b>\n\n"
                f"📋 <b>订单商品</b>：🤖 {pkg_name} ({day_label})\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"💰 <b>请转账确切的 USDT 金额（必须包含尾数）：</b>\n"
                f"👉 <code>{final_usdt_amount}</code> <b>USDT</b>\n\n"
                f"📥 <b>官方 TRC20 收款地址：</b>\n"
                f"👉 <code>{master_address}</code>\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"⚠️ <b>防掉单警告：</b>\n"
                f"必须使用 <b>TRC20</b> 网络，且必须转入带有小数点后的<b>精确金额</b>！转错金额将无法自动到账！\n\n"
                f"<i>⚡️ 链上确认入账后，系统将为您下发激活授权凭证，您即可一键兑换开通机器人！</i>"
            )
            
            # 附带返回按钮
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 返回套餐列表", callback_data=f"saas_menu:{pkg_type}")]
            ])
            
            await call.message.edit_text(cashier_text, reply_markup=kb, parse_mode="HTML")
            await call.answer()

        except Exception as err:
            await session.rollback()
            # 强制打印真实错误日志至控制台，防止报错被吞没
            import traceback
            traceback.print_exc()
            await call.answer(f"❌ 系统异常，订单创建失败：{str(err)}", show_alert=True)

    # 4. 底部键盘：商业赚钱模式介绍
    @router.message(StateFilter('*'), F.text == "📖 商业赚钱模式介绍")
    async def saas_business_intro_handler(message: Message, current_tenant):
        if current_tenant: return 

        intro_text = (
            "🌟 <b>【2026 蓝海暴利：全自动能量分销商铺】</b> 🌟\n\n"
            "还在苦苦寻找没有风险、躺着赚钱的副业吗？\n"
            "无需囤货、无需客服、无需技术代码，<b>一部手机即可轻松掌控属于你的 Web3 自动提款机！</b>\n\n"
            "⚙️ <b>核心赚钱逻辑揭秘：</b>\n\n"
            "🔸 <b>【全网超低拿货底价】</b>\n"
            "无缝对接 SaaS 母平台庞大的超级能量池，享有近乎成本的绝密批发价！\n\n"
            "🔸 <b>【自定义高溢价利润空间】</b>\n"
            "在您自己的独立机器人后台，您可以随意设定面向散户的“零售价”。<b>每一笔成交的中间差价，100% 全部进入您绑定的波场钱包，秒结秒到账！</b>\n\n"
            "🔸 <b>【一键无痕克隆，沉淀私域】</b>\n"
            "开通后，只需前往官方 @BotFather 申请一个机器人名字发给系统。瞬间为您克隆出一个<b>跟本平台一模一样的全功能机器人！</b> 客户只会认为这是您自己花重金开发的大型平台，完美建立个人 IP！\n\n"
            "🔸 <b>【24小时无人值守全自动发货】</b>\n"
            "您只需要发发朋友圈、向群里引流推广您的机器人链接。剩下的繁琐流程：<b>用户充值 ➡️ 购买能量 ➡️ 链上秒派发 ➡️ 利润分账</b>，全部由服务器 24 小时全自动处理！\n\n"
            "🚀 <b>早一步克隆，早一天霸占市场！立即点击底部的 [开通克隆] 开启您的躺赚之旅！</b>"
        )
        await message.answer(intro_text, parse_mode="HTML")

    # 5. 底部键盘：联系官方客服
    @router.message(StateFilter('*'), F.text == "💁 联系官方客服")
    async def saas_contact_cs_handler(message: Message, current_tenant, session: AsyncSession):
        if current_tenant: return 
        
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        raw_link = sys_config.customer_service_link.split(',')[0].strip() if sys_config.customer_service_link else ""
        
        if raw_link.startswith("@"):
            cs_link = f"https://t.me/{raw_link[1:]}"
        elif raw_link and not raw_link.startswith("http"):
            cs_link = f"https://{raw_link}"
        else:
            cs_link = raw_link or "https://t.me/"
            
        cs_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 点击联系客服", url=cs_link)]
        ])
        
        await message.answer(
            "👨‍💻 <b>官方客服中心</b>\n\n"
            "点击下方按钮与我们的客服人员取得联系：",
            reply_markup=cs_kb,
            parse_mode="HTML"
        )
    # =========================================================
    # 模块 C：子机器人 - “👤 个人中心”与充值账户回流
    # =========================================================

    # 1. 响应散客点击底部“👤 个人中心”回复菜单
    @router.message(StateFilter('*'), F.text == "👤 个人中心")
    async def show_personal_center(message: Message, current_tenant, session: AsyncSession):
        """展示 C 端散户的个人中心：积分余额与最近 3 笔消费明细"""
        if not current_tenant:
            return
            
        tg_user = message.from_user
        
        # A. 获取该散客记录
        current_user = await session.scalar(
            select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
        )
        if not current_user:
            return await message.answer("❌ 校验失败：未找到您的账户记录，请发送 /start 激活账户！")
            
        # B. 查询最近 3 笔能量消费订单 (严格对齐 EnergyOrder 模型)
        recent_orders_stmt = select(EnergyOrder).where(
            EnergyOrder.user_id == current_user.id
        ).order_by(desc(EnergyOrder.created_at)).limit(3)
        
        recent_orders = (await session.execute(recent_orders_stmt)).scalars().all()
        
        # C. 拼装账单文本
        orders_text = ""
        if not recent_orders:
            orders_text = "<i>暂无消费记录</i>\n"
        else:
            for order in recent_orders:
                time_str = order.created_at.strftime("%m-%d %H:%M") if order.created_at else "未知时间"
                # 状态汉化映射 (严格对齐 EnergyOrder Enum)
                status_map = {
                    "SUCCESS": "✅ 成功", 
                    "PENDING": "⏳ 处理中", 
                    "FAILED_REFUNDED": "🔄 退款", 
                    "FAILED_SILENT": "❌ 失败"
                }
                status_cn = status_map.get(order.status, order.status)
                
                # 读取扣费金额 total_user_deducted，并消除多余的零
                cost_amt = f"{float(order.total_user_deducted):g}" if order.total_user_deducted else "0"
                orders_text += f"▪️ <code>{time_str}</code> | 消费 <code>{cost_amt}</code> TRX | {status_cn}\n"

        # D. 构建个人中心面板文本
        user_balance = f"{float(current_user.balance):g}" if current_user.balance else "0"
        panel_text = (
            f"👤 <b>我的个人中心</b>\n\n"
            f"💰 <b>当前可用余额</b>：<code>{user_balance}</code> TRX\n"
            f"➖➖➖➖➖➖➖➖➖\n"
            f"📝 <b>最近 3 笔消费记录</b>：\n"
            f"{orders_text}\n"
            f"<i>💡 提示：余额可直接用于极速租用能量，全网最快到达！</i>"
        )
        
        # E. 构建底部的悬浮充值按钮
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 立即充值帐户", callback_data="open_recharge_menu")]
        ])
        
        await message.answer(panel_text, reply_markup=markup, parse_mode="HTML")

    # 1.5 响应个人中心内联按钮的点击，平滑展示充值面板
    @router.callback_query(F.data == "open_recharge_menu")
    async def cb_open_recharge_menu(callback: CallbackQuery, current_tenant):
        if not current_tenant:
            return  # 仅限子机器人调用，防御安全
            
        text = (
            "💰 <b>账户余额充值</b>\n\n"
            "系统采用先进的【微小尾数识别技术】，无需绑定付款地址，转账秒到账！\n\n"
            "👇 <i>请选择或输入您要充值的 TRX 基础金额：</i>"
        )
        # 将个人中心卡片直接编辑修改为充值选择键盘，提供沉浸式体验
        await callback.message.edit_text(text, reply_markup=build_topup_keyboard(), parse_mode="HTML")
        await callback.answer()
    # 2. 响应点击“✏️ 自定义金额” Inline 按钮
    @router.callback_query(F.data == "topup_custom")
    async def cb_topup_custom(callback: CallbackQuery, state: FSMContext):
        """响应自定义金额按钮"""
        await callback.message.edit_text(
            "✏️ <b>请输入您要充值的 TRX 基础金额（整数）：</b>\n\n"
            "💡 <i>建议充值 5 TRX 以上。发送 /cancel 可取消操作。</i>",
            parse_mode="HTML"
        )
        await state.set_state(TopUpFSM.waiting_for_amount)
        await callback.answer()

    # 3. 响应快捷金额按钮 (10, 50, 100) 并带入租户数据与异常退回
    @router.callback_query(F.data.startswith("topup_"))
    async def cb_topup_preset(callback: CallbackQuery, current_tenant, session: AsyncSession):
        """响应快捷金额按钮 (10, 50, 100)"""
        if callback.data == "topup_custom":
            return
            
        base_amount = int(callback.data.split("_")[1])
        # 给出加载提示
        wait_msg = await callback.message.edit_text("🔄 正在为您分配专属充值通道，请稍候...")
        await callback.answer()
        
        # 传入 current_tenant 确保多 Bot 账户资金绝对隔离
        await generate_tail_order(wait_msg, callback.from_user, base_amount, current_tenant, session)
    # 4. 处理散客在输入框发送的自定义充值金额 (全量防呆防御版)
    @router.message(TopUpFSM.waiting_for_amount)
    async def process_custom_topup(message: Message, state: FSMContext, current_tenant, session: AsyncSession):
        """处理用户在输入框发送的自定义充值金额"""
        # 🛡️ 防呆 1：拦截非文本消息（图片、贴纸等），防止 NoneType 报错导致死锁
        if not message.text:
            return await message.answer("❌ <b>格式错误！</b>\n请输入纯数字文本，或发送 /cancel 退出。", parse_mode="HTML")
            
        if message.text.strip().lower() == '/cancel':
            await state.clear()
            return await message.answer("✅ 已取消充值操作。")
            
        # 🛡️ 防呆 2：拦截超长字符串（防 CPU 解析阻塞与数据库 INT 溢出）
        if len(message.text.strip()) > 6:
            return await message.answer("❌ <b>金额过大！</b>\n单笔充值最多支持 6 位数。请分批充值或重新输入：", parse_mode="HTML")
            
        if not message.text.isdigit():
            return await message.answer("❌ 请输入有效的正整数金额！或发送 /cancel 取消：")
            
        base_amount = int(message.text.strip())
        if base_amount < 5:
            return await message.answer("❌ 充值金额不能低于 5 TRX，请重新输入：")
            
        await state.clear()
        wait_msg = await message.answer("🔄 正在为您分配专属充值通道，请稍候...")
        await generate_tail_order(wait_msg, message.from_user, base_amount, current_tenant, session)


    # =========================================================
    # 模块 D：C端“🛒 立即租用”余额消费与能量派发模块
    # =========================================================

    # =========================================================
    # 模块 D：C端“⚡️ 立即租用”余额消费与能量派发模块
    # =========================================================

    # 1. 触发界面：响应“⚡️ 立即租用”回复菜单点击 (兼容之前的表情符号)
    @router.message(StateFilter('*'), F.text.in_(["⚡️ 立即租用", "🛒 立即租用"]))
    async def sub_bot_rent_energy_handler(message: Message, current_tenant, state: FSMContext, session: AsyncSession):
        if not current_tenant:
            return  # 仅限子机器人调用，防御安全
            
        tg_user = message.from_user
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        
        # A. 查询并获取该租户下的独立用户记录
        user = await session.scalar(
            select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
        )
        user_balance = f"{float(getattr(user, 'balance', 0.0) or 0.0):g}" if user else "0"

        # B. 获取用户默认地址
        default_addr_obj = None
        if user and user.default_receive_address_id:
            from models import UserReceiveAddress
            default_addr_obj = await session.scalar(
                select(UserReceiveAddress).where(UserReceiveAddress.id == user.default_receive_address_id)
            )
        default_address_str = default_addr_obj.address if default_addr_obj else "⚠️ 暂未绑定 (请先前往地址管理添加)"

        # C. 计算实时零售基准价 (Netts 进价 + 平台抽水 + 租户自设利润)
        netts_65k = Decimal(str(getattr(sys_config, 'netts_cost_65k', 0.0) or 0.0))
        netts_131k = Decimal(str(getattr(sys_config, 'netts_cost_131k', 0.0) or 0.0))
        draw_65k = Decimal(str(sys_config.base_cost_65k or 0.0))
        draw_131k = Decimal(str(sys_config.base_cost_131k or 0.0))
        
        price_65k_val = f"{(netts_65k + draw_65k + Decimal(str(current_tenant.markup_65k or 0.0))):g}"
        price_131k_val = f"{(netts_131k + draw_131k + Decimal(str(current_tenant.markup_131k or 0.0))):g}"
        
        # D. 拼接租用引导卡片
        rent_text = (
            "⚡️ <b>请选择您需要租用的能量类型：</b>\n\n"
            "💳 <b>您的账户余额：</b>\n"
            f"└ <b>{user_balance} TRX</b>\n\n"
            "💡 <b>平台实时基准价</b>\n"
            f"├ 🟢 对方有 U：{price_65k_val} TRX / 笔\n"
            f"└ 🟡 对方无 U：{price_131k_val} TRX / 笔\n\n"
            "📍 <b>当前默认接收地址：</b>\n"
            f"<code>{default_address_str}</code>\n\n"
            "💡 <i>提示：购买将直接扣除您的账户余额，并向您的【默认接收地址】派发能量。</i>"
        )
        
        # 构建 Inline 键盘 (严格对齐回调标识)
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from aiogram.utils.keyboard import InlineKeyboardBuilder

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🟢 对方有 U (65K)", callback_data="rent_65k"),
            InlineKeyboardButton(text="🟡 对方无 U (131K)", callback_data="rent_131k")
        )
        kb = builder.as_markup()

        await message.answer(rent_text, reply_markup=kb, parse_mode="HTML")

    # 2. 核心业务逻辑：处理租用回调 (带原子锁的安全扣费与 Netts 发货预留)
    @router.callback_query(F.data.in_(["rent_65k", "rent_131k"]))
    async def cb_process_rent_energy(call: CallbackQuery, current_tenant, session: AsyncSession):
        if not current_tenant:
            await call.answer("❌ 操作失败：仅能在子机器人中进行租用。")
            return
            
        tg_user = call.from_user
        
        # 🛡️ UI 层并发锁：接管点击后，立刻销毁内联键盘，防止散客疯狂连点打满数据库连接池
        try:
            await call.message.edit_reply_markup(reply_markup=None)
            await call.message.edit_text("🔄 <b>正在锁定库存与校验安全环境，请稍候...</b>", parse_mode="HTML")
        except TelegramBadRequest:
            # 已经被点过并销毁了，直接丢弃并发请求
            return
        
        # 1. 校验用户身份
        user = await session.scalar(
            select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
        )
        if not user:
            await call.answer("❌ 校验失败：未找到您的账户记录！", show_alert=True)
            return
            
        # 2. 校验默认地址
        default_address = None
        if user.default_receive_address_id:
            from models import UserReceiveAddress
            default_address = await session.scalar(
                select(UserReceiveAddress).where(UserReceiveAddress.id == user.default_receive_address_id)
            )
            
        if not default_address:
            await call.answer("❌ 操作失败：您尚未绑定默认接收地址。\n\n请先点击菜单栏【📍 地址管理】进行添加！", show_alert=True)
            return

        # 3. 动态核算当前最终售价
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        
        if call.data == "rent_65k":
            admin_cost = Decimal(str(getattr(sys_config, 'netts_cost_65k', 0.0) or 0.0)) + Decimal(str(sys_config.base_cost_65k or 0.0))
            markup = Decimal(str(current_tenant.markup_65k or 0.0))
            price = admin_cost + markup
            order_type = "BALANCE_65K"
            energy_amount = 65000
        else:
            admin_cost = Decimal(str(getattr(sys_config, 'netts_cost_131k', 0.0) or 0.0)) + Decimal(str(sys_config.base_cost_131k or 0.0))
            markup = Decimal(str(current_tenant.markup_131k or 0.0))
            price = admin_cost + markup
            order_type = "BALANCE_131K"
            energy_amount = 131000

        # 4. 余额防穿仓预检
        if user.balance < price:
            await call.answer(
                f"❌ 余额不足！\n\n本次需要：{float(price):g} TRX\n当前余额：{float(user.balance):g} TRX\n\n请先点击【💰 充值帐户】充值！",
                show_alert=True
            )
            return

        try:
            # 5. 【核心安全机制】：采用原子级 Update 语句进行乐观锁扣费，防止高并发将余额扣成负数
            from sqlalchemy import update
            stmt = update(User).where(
                User.id == user.id, 
                User.balance >= price
            ).values(
                balance=User.balance - price,
                total_orders=User.total_orders + 1,
                total_spent_trx=User.total_spent_trx + price
            )
            result = await session.execute(stmt)
            
            # 如果影响行数为 0，说明在并发期间余额已不足
            if result.rowcount == 0:
                await call.answer("❌ 扣费失败：账户余额不足或系统拥堵！", show_alert=True)
                await session.rollback()
                return
                
            # 6. 生成并记录租用订单
            from models import EnergyOrder
            new_order = EnergyOrder(
                tenant_id=current_tenant.id,
                user_id=user.id,
                order_type=order_type,
                target_address=default_address.address,
                admin_base_cost=admin_cost,
                tenant_markup=markup,
                total_user_deducted=price,
                status='PROCESSING'  # 状态设为处理中
            )
            session.add(new_order)
            
            # 统一提交强一致性事务
            await session.commit()
            
            # 7. 刷新 UI 提供前端过渡反馈
            await call.message.edit_text("⏳ <b>订单生成完毕，正在向波场主网下发能量，请稍候约 3-10 秒...</b>", parse_mode="HTML")
            await call.answer()

            # ========================================================
            # ========================================================
            # 8. Netts 派发接口接入与失败退款防客诉闭环
            # ========================================================
            try:
                # 真实调用 Netts API 请求能量派发
                result = await delegate_energy(
                    target_address=default_address.address, 
                    amount=energy_amount
                )
                
                if result["success"]:
                    # 发货成功：更新订单状态为大写成功
                    new_order.status = 'SUCCESS'
                    
                    # ====== 💰 SaaS 租户精准分润逻辑开始 ======
                    try:
                        # 租户的真实纯利润，即为函数顶部已计算好的 markup 变量 (Decimal 格式)
                        # 它已天然剔除 Netts 进货成本与超管固定抽水
                        tenant_profit = markup
                        
                        # 结算入账至租户的利润余额 (profit_balance)
                        if tenant_profit > Decimal("0"):
                            # 重新获取租户记录以防止并发覆盖
                            from models import Tenant
                            active_tenant = await session.get(Tenant, current_tenant.id)
                            if active_tenant:
                                active_tenant.profit_balance = active_tenant.profit_balance + tenant_profit
                    except Exception as profit_err:
                        import logging
                        logging.warning(f"⚠️ [分润失败预警] 订单 {new_order.id} 分润核算异常: {profit_err}")
                    # ====== 💰 SaaS 租户精准分润逻辑结束 ======

                    # 统一提交订单状态更新与分润资金事务
                    await session.commit()
                    
                    success_msg = (
                        "✅ <b>能量派发成功！</b>\n\n"
                        f"🎯 <b>接收地址</b>：<code>{default_address.address}</code>\n"
                        f"⚡ <b>到账额度</b>：{energy_amount} 能量\n"
                        "<i>您的能量已极速到账，快去体验免手续费转账吧！</i>"
                    )
                    await call.message.edit_text(success_msg, parse_mode="HTML")
                else:
                    if result.get("uncertain"):
                        await session.rollback()
                        locked_order = await session.scalar(
                            select(EnergyOrder).where(
                                EnergyOrder.id == new_order.id,
                                EnergyOrder.status == 'PROCESSING'
                            ).with_for_update()
                        )
                        if locked_order:
                            locked_order.status = 'MANUAL_REVIEW'
                            await session.commit()
                        await call.message.edit_text(
                            "⚠️ <b>订单进入人工确认</b>\n\n"
                            f"原因：{result.get('msg', '上游返回不确定')}\n\n"
                            "为避免上游已发货但系统误退款，资金暂时冻结在本订单中。请联系平台客服核对，确认失败后再退款。",
                            parse_mode="HTML"
                        )
                        return

                    # 如果上游明确返回失败，抛出异常交由下方的退款中心处理
                    raise Exception(result["msg"])
                    
            except Exception as dispatch_err:
                # 🚨 触发核心退款回滚机制（此时钱已被扣除，必须安全加回去）
                await session.rollback()  # 确保清理掉未提交的脏状态

                locked_order = await session.scalar(
                    select(EnergyOrder).where(
                        EnergyOrder.id == new_order.id,
                        EnergyOrder.status == 'PROCESSING'
                    ).with_for_update()
                )
                if not locked_order:
                    await call.message.edit_text(
                        "⚠️ <b>订单状态已经被处理，请勿重复操作。</b>",
                        parse_mode="HTML"
                    )
                    return

                locked_order.status = 'FAILED_REFUNDED'

                # 重新拉取用户记录，防止并发导致的覆盖写
                refund_user = await session.scalar(
                    select(User).where(User.id == user.id).with_for_update()
                )
                if refund_user:
                    # 完美原路退回资金，并抹除这笔失败的消费统计
                    refund_user.balance = refund_user.balance + price
                    refund_user.total_orders = refund_user.total_orders - 1
                    refund_user.total_spent_trx = refund_user.total_spent_trx - price
                    
                # 强一致性提交退款事务
                await session.commit()
                
                fail_msg = (
                    "❌ <b>能量派发失败，已为您全额退款！</b>\n\n"
                    f"⚠️ <b>失败原因</b>：{str(dispatch_err)}\n"
                    f"💰 <b>退回金额</b>：{float(price):g} TRX 已安全返回您的账户余额。\n\n"
                    "<i>上游能量池可能暂时拥堵或无库存，请稍后重新尝试租用。</i>"
                )
                await call.message.edit_text(fail_msg, parse_mode="HTML")
                
                # 直接 return，终止执行，避免触发最外层的大回滚报错
                return
            # ========================================================
            
        except Exception as e:
            await session.rollback()
            await call.answer(f"❌ 系统结算异常，交易已安全回滚：{str(e)}", show_alert=True)
    # 3. 地址管理：响应底部菜单的 F.text == "📍 地址管理" (严格对齐 models.py 字段)
    @router.message(StateFilter('*'), F.text == "📍 地址管理")
    async def cmd_address_manage_handler(message: Message, current_tenant, session: AsyncSession):
        if not current_tenant:
            return  # 仅限子机器人调用，防御安全
            
        tg_user = message.from_user
        
        # A. 查询获取该租户下的独立用户实体
        user = await session.scalar(
            select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
        )
        if not user:
            # 兼容防抖处理：若用户尚未初始化，则先予以自动注册
            user = User(
                tenant_id=current_tenant.id,
                tg_user_id=tg_user.id,
                tg_first_name=tg_user.first_name,
                balance=Decimal('0.00'),
                total_orders=0,
                total_spent_trx=Decimal('0.00')
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
        # B. 查询当前用户绑定的所有地址 (严格使用 models.py 中的 UserReceiveAddress)
        stmt = select(UserReceiveAddress).where(UserReceiveAddress.user_id == user.id).order_by(UserReceiveAddress.id)
        result = await session.execute(stmt)
        addresses = result.scalars().all()
        
        if not addresses:
            text = (
                "📍 <b>地址管理中心</b>\n\n"
                "您当前尚未绑定任何能量接收地址。\n"
                "⚠️ <i>必须绑定至少 1 个地址才能进行能量租用！</i>\n\n"
                "💡 (每位用户最多可绑定 5 个接收地址)"
            )
        else:
            text = (
                "📍 <b>地址管理中心</b>\n\n"
                "👇 <b>请点击下方悬浮按钮将其设为默认，或点击垃圾桶删除。</b>\n"
                f"💡 已绑定 {len(addresses)}/5 个地址。"
            )

        # 传入整个 addresses 对象列表和当前用户的 default_receive_address_id
        reply_markup = build_address_manage_keyboard(addresses, user.default_receive_address_id)
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
        # =========================================================
    # 模块 E：C端“📍 地址管理”增量 FSM 绑定与“🤖 克隆机器人”拉新
    # =========================================================

    # 1. 响应点击“➕ 添加新地址”的 Callback 动作
    @router.callback_query(F.data == "add_new_address")
    async def cb_add_address(callback: CallbackQuery, state: FSMContext):
        """响应点击添加新地址按钮"""
        await callback.message.answer(
            "✏️ <b>请输入您的波场 (TRC20) 接收地址：</b>\n\n"
            "💡 <i>提示：地址必须以大写 T 开头，且为 34 位字符。</i>\n"
            "发送 /cancel 可取消本次操作。",
            parse_mode="HTML"
        )
        await state.set_state(UserAddressFSM.waiting_for_address)
        await callback.answer()

    # 2. 拦截并处理用户在地址录入状态机下发送的文本消息 (完美对齐 models.py)
    @router.message(UserAddressFSM.waiting_for_address)
    async def process_new_address(message: Message, state: FSMContext, current_tenant, session: AsyncSession):
        """处理用户发送的新地址"""
        if message.text.strip().lower() == '/cancel':
            await state.clear()
            await message.answer("✅ 已取消添加操作。")
            return

        address = message.text.strip()
        
        # 基础格式校验
        if not is_valid_tron_address(address):
            await message.answer(
                "❌ <b>地址格式不正确！</b>\n波场 TRC20 地址必须以 <b>T</b> 开头且为 34 位字符。\n"
                "请重新输入，或发送 /cancel 取消：",
                parse_mode="HTML"
            )
            return

        tg_user = message.from_user
        
        # A. 查询并防抖该租户下当前散客的用户记录
        user = await session.scalar(
            select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
        )
        if not user:
            await message.answer("❌ 校验失败：未找到您的账户记录！")
            await state.clear()
            return

        # B. 查询该用户现有的地址，检查上限和重复 (基于 models.py.UserReceiveAddress)
        stmt = select(UserReceiveAddress).where(UserReceiveAddress.user_id == user.id)
        result = await session.execute(stmt)
        existing_addresses = result.scalars().all()

        if len(existing_addresses) >= 5:
            await message.answer("❌ 您的地址数量已达上限 (5个)，无法继续添加。")
            await state.clear()
            return

        if any(a.address == address for a in existing_addresses):
            await message.answer("❌ 该地址已存在，请勿重复添加！请重新输入或发送 /cancel：")
            return

        try:
            # C. 存入数据库：如果是第一条地址，则自动设为默认
            is_default = (len(existing_addresses) == 0)
            new_addr = UserReceiveAddress(user_id=user.id, address=address)
            session.add(new_addr)
            
            # 强制刷盘以获取新地址在数据库中的主键自增 ID
            await session.flush()
            
            # 更新默认地址绑定指针 (对应 models.py: User.default_receive_address_id)
            if is_default:
                user.default_receive_address_id = new_addr.id
                
            await session.commit()
            await state.clear()
            
            default_tips = "\n(由于这是您的首个地址，已自动设为默认接收地址)" if is_default else ""
            await message.answer(
                f"✅ <b>成功添加地址！</b>\n<code>{address}</code>{default_tips}\n\n"
                "您现在可以随时去租用能量了！",
                parse_mode="HTML"
            )
        except Exception as e:
            await session.rollback()
            await message.answer(f"❌ 系统保存失败，请联系客服：{str(e)}")
            await state.clear()

    # =========================================================
    # 模块 G：克隆机器人 Token 收集交互流
    # =========================================================
    
    # 1. 响应底部物理 Reply 菜单上的“🤖 克隆机器人”按钮
    # =========================================================
    # 模块 G：克隆机器人“购后绑定”授权核销交互流
    # =========================================================
    
    # 1. 触发入口：拦截并检查是否有可用的“已付款授权”
    @router.message(StateFilter('*'), F.text == "🤖 克隆机器人")
    async def start_clone_bot(message: Message, state: FSMContext, session: AsyncSession):
        """触发克隆流程：前置核查付费授权"""
        tg_user = message.from_user
        
        # 查询当前用户是否有 PAID 状态的克隆授权订单
        paid_order_stmt = select(SaaSOrder).where(
            SaaSOrder.tg_user_id == tg_user.id,
            SaaSOrder.order_type == "clone",
            SaaSOrder.status == "PAID"
        )
        paid_order = (await session.execute(paid_order_stmt)).scalars().first()

        if not paid_order:
            await message.answer(
                "❌ <b>未检测到可用的开通授权！</b>\n\n"
                "您需要先在商铺购买「独立专属数字分销商铺」授权后，才能绑定机器人。\n"
                "👉 请点击主菜单的 <b>[🚀 立即开通/克隆我的机器人]</b> 进行购买。",
                parse_mode="HTML"
            )
            return

        # 如果有授权，引导输入 Token
        intro_text = (
            f"✅ <b>已检测到您拥有 1 个可用的授权凭证 ({paid_order.days}天)！</b>\n\n"
            "只需要简单两步，您就能立刻拥有全自动发卡/能量售卖机器人：\n\n"
            "1️⃣ 前往官方 @BotFather 创建一个新机器人。\n"
            "2️⃣ 复制获取到的 <b>HTTP API Token</b> 发送到这里。\n\n"
            "✏️ <b>请在下方粘贴您的 Bot Token（如 123456:ABC-DEF1234ghIkl-zyx57W2v...）：</b>\n"
            "(发送 /cancel 可退出)"
        )
        await message.answer(intro_text, parse_mode="HTML")
        await state.set_state(CloneBotFSM.waiting_for_token)
        
        # 将订单 ID 与天数存入状态机内存，留给下一步核销用
        await state.update_data(order_id=paid_order.id, days=int(paid_order.days))

    # 2. Token 接收、排重、核销与动态点火
    @router.message(CloneBotFSM.waiting_for_token)
    async def process_clone_token(message: Message, state: FSMContext, session: AsyncSession):
        """处理 Token、核销订单并创建/更新租户，最后拉起子机器人"""
        if message.text.strip().lower() == '/cancel':
            await state.clear()
            await message.answer("✅ 已退出克隆操作。")
            return

        import re
        token = message.text.strip()
        if not re.match(r'^[0-9]+:[a-zA-Z0-9_-]+$', token):
            await message.answer("❌ <b>Token 格式错误！</b>\n请确保是从 @BotFather 复制的完整 API Token，请重新发送：", parse_mode="HTML")
            return

        # 防重复检查：确保这个 token 没被别人用过
        exist_tenant = (await session.execute(select(Tenant).where(Tenant.bot_token == token))).scalars().first()
        if exist_tenant:
            await message.answer("❌ <b>绑定失败！</b>\n该机器人 Token 已经被绑定过，请去 @BotFather 重新生成一个全新的机器人 Token。")
            return

        state_data = await state.get_data()
        order_id = state_data.get("order_id")
        days = state_data.get("days", 30)
        await state.clear()
        
        tg_user = message.from_user

        # 开启事务：核销订单 -> Upsert租户 -> 动态挂载
        try:
            # a. 锁定并核销订单状态为 ACTIVATED (已激活使用)
            order_stmt = select(SaaSOrder).where(SaaSOrder.id == order_id)
            order = (await session.execute(order_stmt)).scalar_one()
            order.status = "ACTIVATED" 
            
            # b. 【核心防爆机制】：检测用户是否已有租户记录 (防止 owner_tg_id 唯一键冲突)
            tenant_stmt = select(Tenant).where(Tenant.owner_tg_id == tg_user.id)
            tenant = (await session.execute(tenant_stmt)).scalar_one_or_none()
            
            now = datetime.utcnow()
            if tenant:
                # 续费或重置：如果用户已有记录，更新 Token 和到期时间
                tenant.bot_token = token
                tenant.is_active = True
                if tenant.expire_time < now:
                    tenant.expire_time = now + timedelta(days=days)
                else:
                    tenant.expire_time = tenant.expire_time + timedelta(days=days)
            else:
                # 全新开通：创建新租户
                expire_time = now + timedelta(days=days)
                tenant = Tenant(
                    owner_tg_id=tg_user.id,
                    bot_token=token,
                    expire_time=expire_time,
                    deposit_balance=Decimal("0.00"),
                    profit_balance=Decimal("0.00"),
                    is_active=True
                )
                session.add(tenant)
                
            await session.commit()
            await session.refresh(tenant)

            # c. 🔥 核心：调用 bot_manager 动态拉起新机器人
            from bot_manager import bot_manager
            try:
                # 使用项目中真实的 mount_bot 进行平滑挂载
                is_success = await bot_manager.mount_bot(tenant.id, token)
                if is_success:
                    await message.answer(
                        f"🎉 <b>点火成功！您的专属机器人已上线！</b>\n\n"
                        f"🆔 租户 ID：#{tenant.id}\n"
                        f"⏳ 到期时间：<code>{tenant.expire_time.strftime('%Y-%m-%d %H:%M')}</code>\n\n"
                        f"🚀 请前往您的新机器人发送 /start，即可进入您的专属超管后台配置利润率与收款地址！",
                        parse_mode="HTML"
                    )
                else:
                    await message.answer("⚠️ <b>数据库记录已生成，但机器人进程拉起失败！</b>\n您的 Token 可能已被其他系统占用或失效，请前往【⚙️ 机器设置】尝试更换。")
            except Exception as bot_err:
                await message.answer(f"⚠️ 机器人拉起异常，请联系总管检查网络：{bot_err}")

        except Exception as e:
            await session.rollback()
            import traceback
            traceback.print_exc()
            await message.answer(f"❌ 系统异常，核销失败：{str(e)}")

    # 1. 响应散客点击“设为默认”动作 (严格对齐 models.py.UserReceiveAddress)
    @router.callback_query(F.data.startswith("set_default_addr_"))
    async def cb_set_default_address(callback: CallbackQuery, current_tenant, session: AsyncSession):
        """处理点击设置默认地址"""
        if not current_tenant:
            await callback.answer("❌ 操作失败：无效的租户环境")
            return
            
        addr_id = int(callback.data.split("_")[-1])
        tg_user = callback.from_user
        
        # 精准获取该租户下的 User 实体
        stmt = select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        
        if not user:
            await callback.answer("❌ 用户不存在", show_alert=True)
            return

        # 更新用户的 default_receive_address_id 指针
        user.default_receive_address_id = addr_id
        await session.commit()
        
        # 重新拉取该散客名下绑定的所有地址数据
        addr_stmt = select(UserReceiveAddress).where(UserReceiveAddress.user_id == user.id).order_by(UserReceiveAddress.id)
        addresses = (await session.execute(addr_stmt)).scalars().all()
        
        # 重新生成悬浮键盘并刷新 UI
        kb = build_address_manage_keyboard(addresses, user.default_receive_address_id)
        await callback.message.edit_reply_markup(reply_markup=kb)
        
        await callback.answer("✅ 已成功切换默认接收地址！")


    # 2. 响应散客点击“🗑️ 删除地址”动作 (严格对齐 models.py.UserReceiveAddress)
    @router.callback_query(F.data.startswith("del_addr_"))
    async def cb_delete_address(callback: CallbackQuery, current_tenant, session: AsyncSession):
        """处理点击删除地址"""
        if not current_tenant:
            await callback.answer("❌ 操作失败：无效的租户环境")
            return
            
        addr_id = int(callback.data.split("_")[-1])
        tg_user = callback.from_user
        
        # 精准获取该租户下的 User 实体
        user_stmt = select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
        user = (await session.execute(user_stmt)).scalar_one_or_none()
        
        if not user:
            await callback.answer("❌ 用户不存在", show_alert=True)
            return
            
        # 安全验证：检查此地址是否确实归属于该散客，防越权删除
        addr_stmt = select(UserReceiveAddress).where(UserReceiveAddress.id == addr_id, UserReceiveAddress.user_id == user.id)
        addr_to_delete = (await session.execute(addr_stmt)).scalar_one_or_none()
        
        if not addr_to_delete:
            await callback.answer("❌ 地址不存在或已被删除", show_alert=True)
            return
            
        # 联动清空：若删除的正是用户当前默认的接收地址，先清空指针
        if user.default_receive_address_id == addr_id:
            user.default_receive_address_id = None

        # 提交删除并更新事务
        await session.delete(addr_to_delete)
        await session.commit()
        
        # 重新获取剩余绑定的所有接收地址数据
        remain_stmt = select(UserReceiveAddress).where(UserReceiveAddress.user_id == user.id).order_by(UserReceiveAddress.id)
        addresses = (await session.execute(remain_stmt)).scalars().all()
        
        # 判定刷新 UI 呈现
        if not addresses:
            # 地址已全部被删除，重置提示文案与空键盘
            text = (
                "📍 <b>地址管理中心</b>\n\n"
                "您当前尚未绑定任何能量接收地址。\n"
                "⚠️ <i>必须绑定至少 1 个地址才能进行能量租用！</i>\n\n"
                "💡 (每位用户最多可绑定 5 个接收地址)"
            )
            kb = build_address_manage_keyboard([], None)
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            # 如果删除了默认地址但仍有剩余地址，则自动分配首个地址为新的默认
            if user.default_receive_address_id is None:
                user.default_receive_address_id = addresses[0].id
                await session.commit()
                
            # 重新渲染并无缝刷新内联键盘
            kb = build_address_manage_keyboard(addresses, user.default_receive_address_id)
            await callback.message.edit_reply_markup(reply_markup=kb)
            
        await callback.answer("🗑️ 地址已成功删除！")
        # 5. 智能尾数生成算法：支持高并发下 2位 到 3位 精度无缝切换 (严格对齐 models.py.MicroDepositOrder)
    # 5. 核心核算：生成带有唯一尾数的充值订单（防撞库、自动精度降级与多租户隔离）
    async def generate_tail_order(message: Message, tg_user, base_amount: int, current_tenant, session: AsyncSession):
        """核心机制：生成带微小尾数的充值订单"""
        lock_name = f"micro_deposit_tail:{base_amount}"
        lock_acquired = await acquire_mysql_lock(session, lock_name)
        if not lock_acquired:
            await message.edit_text("❌ 当前充值通道繁忙，请稍后重试。")
            return

        try:
            config_stmt = select(SystemConfig).where(SystemConfig.id == 1)
            system_config = (await session.execute(config_stmt)).scalar_one_or_none()
            if not system_config or not system_config.master_receive_address:
                await message.edit_text("❌ 系统暂未配置全局收款地址，请联系管理员。")
                return
            master_address = system_config.master_receive_address

            now_time = datetime.utcnow()
            expire_time = now_time + timedelta(minutes=10)
            fractional_val = None
            expected_amount = None
            is_three_digits = False

            for _ in range(40):
                tail_int = random.randint(1, 99)
                test_fractional = Decimal(f"0.{tail_int:02d}")
                test_expected = Decimal(str(base_amount)) + test_fractional
                stmt = select(MicroDepositOrder).where(
                    MicroDepositOrder.expected_amount == test_expected,
                    MicroDepositOrder.status == "PENDING",
                    MicroDepositOrder.expired_at > now_time
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if not existing:
                    fractional_val = test_fractional
                    expected_amount = test_expected
                    break

            if not fractional_val:
                is_three_digits = True
                for _ in range(50):
                    tail_int = random.randint(1, 999)
                    if tail_int % 10 == 0:
                        continue
                    test_fractional = Decimal(f"0.{tail_int:03d}")
                    test_expected = Decimal(str(base_amount)) + test_fractional
                    stmt = select(MicroDepositOrder).where(
                        MicroDepositOrder.expected_amount == test_expected,
                        MicroDepositOrder.status == "PENDING",
                        MicroDepositOrder.expired_at > now_time
                    )
                    existing = (await session.execute(stmt)).scalar_one_or_none()
                    if not existing:
                        fractional_val = test_fractional
                        expected_amount = test_expected
                        break

            if not fractional_val:
                await message.edit_text("❌ 当前充值通道极其繁忙，请稍后或更换金额重试。")
                return

            try:
                user_record = await session.scalar(
                    select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == tg_user.id)
                )
                if not user_record:
                    await message.edit_text("❌ 获取用户信息失败，无法锁定专属充值通道。")
                    return

                new_order = MicroDepositOrder(
                    tenant_id=current_tenant.id,
                    user_id=user_record.id,
                    base_amount=Decimal(str(base_amount)),
                    fractional_amount=fractional_val,
                    expected_amount=expected_amount,
                    status="PENDING",
                    expired_at=expire_time
                )
                session.add(new_order)
                await session.commit()
            except Exception as db_err:
                await session.rollback()
                await message.edit_text(f"❌ 专属通道锁定失败，入库异常：{str(db_err)}")
                return
        finally:
            await release_mysql_lock(session, lock_name)

        amount_display = f"{expected_amount:.2f}" if not is_three_digits else f"{expected_amount:.3f}"
        invoice_text = (
            "✅ <b>专属充值订单已生成！</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🚨 <b>⚠️ 核心安全警告（非常重要）：</b>\n"
            "系统采用智能尾数识别记账，请务必<b>【一分不差】</b>地转账下方带有小数点的精确金额！\n"
            "多付、少付、抹零、或者自行凑整均会导致资金丢失且无法自动到账！\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💰 <b>需转账精确金额：</b>\n"
            f"<code>{amount_display}</code> TRX 👈 <i>(点击数字自动复制)</i>\n\n"
            "📥 <b>充值收款地址：</b>\n"
            f"<code>{master_address}</code> 👈 <i>(点击地址自动复制)</i>\n\n"
            "⏱ <i>本订单有效期仅 <b>10 分钟</b>，请抓紧时间支付。转账后 1-3 分钟内智能合约自动为您核销并增加余额。</i>"
        )
        await message.edit_text(invoice_text, parse_mode="HTML")

    return router
    
