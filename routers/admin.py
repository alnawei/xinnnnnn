import json
import secrets
import string
from datetime import datetime, date
from decimal import Decimal
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, delete
from aiogram.filters import CommandObject
from filters.role import RoleFilter
from keyboards.reply import build_admin_keyboard
from models import WithdrawOrder, Tenant, User, EnergyOrder, MicroDepositOrder, SystemConfig, ActivationCode, TronApiNode, SaaSOrder

# ==================== 0. 工具函数：金额格式化 ====================
def format_amount(amount):
    """消除多余的零：1.500000 -> 1.5, 1.000000 -> 1"""
    if amount is None:
        return "0"
    s = f"{float(amount):.2f}"
    return s.rstrip('0').rstrip('.') if '.' in s else s

# =====================================================================
# ==================== 1. 路由与 FSM 状态定义区 ====================
# =====================================================================
admin_router = Router(name="admin_router")
admin_router.message.filter(RoleFilter(["admin"]))
admin_router.callback_query.filter(RoleFilter(["admin"]))

class AdminConfigFSM(StatesGroup):
    waiting_for_broadcast_content = State()
    wait_activation_days = State()
    wait_special_fee_65k = State()   # 👈 新增
    wait_special_fee_131k = State()  # 👈 新增
    # --- 基础定价参数 ---
    wait_markup_65k = State()
    wait_markup_131k = State()
    wait_special_fee = State()
    wait_unactivated_fee = State()  
    wait_special_address = State()
    
    # --- 豪华版：克隆机器人套餐 ---
    wait_clone_pkg_days = State()
    wait_clone_pkg_price = State()
    wait_clone_pkg_edit_price = State()

    # --- 豪华版：特价功能授权套餐 ---
    wait_spec_pkg_days = State()
    wait_spec_pkg_price = State()
    wait_spec_pkg_edit_price = State()
    
    # --- 全局设置与财务 ---
    wait_master_wallet = State()
    wait_min_withdraw = State()
    wait_customer_service = State()
    wait_cs_del = State()
    wait_api_nodes = State()
    wait_welcome_message = State()
    wait_zombie_days = State()
    
    # --- 🚫 封禁与手工调账状态 ---
    wait_ban_tenant_id = State()             # 等待输入要封禁的租户ID
    wait_adjust_tg_id = State()              # 调账：等待输入租户ID
    wait_adjust_type = State()               # 调账：等待选择增加/扣除
    wait_adjust_amount = State()             # 调账：等待输入金额
    
# =========================================================
# 超管财务预警阈值设置状态机
# =========================================================
class AdminAlertSetupFSM(StatesGroup):
    waiting_for_admin_limit = State()
    waiting_for_tenant_limit = State()

# =====================================================================
# ==================== 2. 主菜单响应区 (F.text) ====================
# =====================================================================
@admin_router.message(Command("start"))
async def admin_start(message: Message, state: FSMContext):
    await state.clear() 
    await message.answer("👑 欢迎来到 SaaS 超级控制台，请操作下方菜单：", reply_markup=build_admin_keyboard())

@admin_router.message(F.text == "📊 平台核心数据总览 (大盘看板)")
async def admin_menu_overview(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    today_start = datetime.combine(date.today(), datetime.min.time())
    
    active_tenants = await session.scalar(select(func.count(Tenant.id)).where(Tenant.is_active == True)) or 0
    total_users = await session.scalar(select(func.count(User.id))) or 0
    today_orders_count = await session.scalar(select(func.count(EnergyOrder.id)).where(EnergyOrder.created_at >= today_start, EnergyOrder.status == 'SUCCESS')) or 0
    today_deposit_trx = await session.scalar(select(func.sum(MicroDepositOrder.expected_amount)).where(MicroDepositOrder.created_at >= today_start, MicroDepositOrder.status == 'SUCCESS')) or Decimal('0.00')
    today_total_tx_count = await session.scalar(select(func.count(EnergyOrder.id)).where(EnergyOrder.created_at >= today_start)) or 0

    text = (
        "📊 <b>SaaS 平台核心数据总览</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>今日总交易单量</b>：{today_total_tx_count} 单\n"
        f"⚡️ <b>今日成功派发笔数</b>：{today_orders_count} 笔\n"
        f"💰 <b>今日总充值金额</b>：{format_amount(today_deposit_trx)} TRX\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>当前存活租户数</b>：{active_tenants} 位代理\n"
        f"👤 <b>当前散客总数</b>：{total_users} 人\n\n"
        f"<i>🕒 统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
    )
    await message.answer(text, parse_mode="HTML")

# routers/admin.py

@admin_router.message(F.text == "💰 定价管理")
async def admin_menu_pricing(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    
    # 动态解析多级克隆套餐
    clone_fees = getattr(config, 'clone_fee_config', '{}')
    
    # 1. 精准提取 65K 财务链路各层级
    netts_65k = Decimal(str(getattr(config, 'netts_cost_65k', 0.0) or 0.0))
    draw_65k = Decimal(str(config.base_cost_65k or 0.0))
    agent_65k = netts_65k + draw_65k # 进货 + 平台固定抽水
    
    # 2. 精准提取 131K 财务链路各层级
    netts_131k = Decimal(str(getattr(config, 'netts_cost_131k', 0.0) or 0.0))
    draw_131k = Decimal(str(config.base_cost_131k or 0.0))
    agent_131k = netts_131k + draw_131k # 进货 + 平台固定抽水

    cost_special_65k = format_amount(getattr(config, 'special_base_cost_65k', 0))
    cost_special_131k = format_amount(getattr(config, 'special_base_cost_131k', 0))
    unactivated_fee = format_amount(getattr(config, 'unactivated_fee_trx', 2.0))

    text = (
        "💰 <b>系统定价与财务链路管理</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔋 <b>65K 能量财务链路：</b>\n"
        f"├─ 进货成本: <code>{netts_65k:.2f}</code> TRX (自动更新)\n"
        f"├─ 平台抽水: <code>{draw_65k:.2f}</code> TRX (超管设置)\n"
        f"└─ <b>代理拿货价: <code>{agent_65k:.2f}</code> TRX</b>\n\n"
        "🔋 <b>131K 能量财务链路：</b>\n"
        f"├─ 进货成本: <code>{netts_131k:.2f}</code> TRX (自动更新)\n"
        f"├─ 平台抽水: <code>{draw_131k:.2f}</code> TRX (超管设置)\n"
        f"└─ <b>代理拿货价: <code>{agent_131k:.2f}</code> TRX</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🤖 克隆套餐：<b>已配置 (点击下方按钮管理)</b>\n"
        f"💎 特权开通：<b>已配置 (点击下方按钮管理)</b>\n"
        f"🔥 65K 全局特价售价：<b>{cost_special_65k} TRX</b>\n"
        f"🔥 131K 全局特价售价：<b>{cost_special_131k} TRX</b>\n"
        f"⚠️ 未激活附加费：<b>{unactivated_fee} TRX</b>\n"
        f"💡 全局授权开关：<b>{'✅ 开放授权' if getattr(config, 'is_special_energy_global_enabled', True) else '❌ 隐藏'}</b>\n\n"
        "👇 请选择您要配置的参数项目："
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 设置克隆机器人费用(多套餐)", callback_data="admin_set_packages")],
        [InlineKeyboardButton(text="💎 设置特价功能授权(多套餐)", callback_data="admin_manage_spec_packages")],
        [
            InlineKeyboardButton(text="⚡ 设 65K 平台抽水", callback_data="admin_set_markup_65k"),
            InlineKeyboardButton(text="⚡ 设 131K 平台抽水", callback_data="admin_set_markup_131k")
        ],
        [
            InlineKeyboardButton(text="🔥 设 65K 特价售价", callback_data="admin_set_special_fee_65k"),
            InlineKeyboardButton(text="🔥 设 131K 特价售价", callback_data="admin_set_special_fee_131k")
        ],
        [InlineKeyboardButton(text="🪙 设置未激活附加费", callback_data="admin_set_unactivated_fee")],
        [InlineKeyboardButton(text="👁 全局特价地址设置", callback_data="admin_set_special_address")],
        [InlineKeyboardButton(text="🔄 一键同步 Netts 上游底价", callback_data="admin_sync_netts_price")],
        [InlineKeyboardButton(text="🔄 切换特权全局开关", callback_data="admin_toggle_special_global")]
    ])
    
    if isinstance(message, CallbackQuery):
        await message.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "⚙️ 全局设置")
async def admin_menu_settings(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    
    welcome_preview = config.global_welcome_template[:20] + "..." if config.global_welcome_template else "未设置"
    cs_link = config.customer_service_link if config.customer_service_link else "未设置"
    
    api_keys_str = getattr(config, 'tron_api_keys', '') or ""
    api_keys = [k.strip() for k in api_keys_str.split(',') if k.strip()]
    api_count = len(api_keys)
    first_key_masked = "暂无节点"
    if api_count > 0:
        fk = api_keys[0]
        first_key_masked = f"{fk[:6]}****{fk[-4:]}" if len(fk) > 10 else "****"
    
    text = (
        "⚙️ <b>系统全局设置中心</b>\n\n"
        f"🏦 主收款地址：<code>{config.master_receive_address}</code>\n"
        f"💁 客服显示状态：<b>{'✅ 开启' if config.show_customer_service else '❌ 隐藏'}</b>\n"
        f"🔗 当前客服链接：<code>{cs_link}</code>\n"
        f"👋 当前欢迎语：<i>{welcome_preview}</i>\n"
        f"🌐 节点池概况：已挂载 <b>{api_count}</b> 个节点 (首节点: {first_key_masked})\n\n"
        "👇 请选择配置项："
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💁 客服配置(增删改/显隐)", callback_data="admin_set_customer_service")],
        [InlineKeyboardButton(text="🏦 全站收款主地址配置", callback_data="admin_set_master_wallet")],
        [InlineKeyboardButton(text="🌐 Tron API 节点池管理", callback_data="admin_set_tron_api")],
        [InlineKeyboardButton(text="👋 全局默认欢迎语设置", callback_data="admin_set_welcome_msg")],
        [InlineKeyboardButton(text="👁 切换客服显示状态", callback_data="admin_toggle_cs_display")]
        [InlineKeyboardButton(text="🔔 预警余额配置", callback_data="admin_set_alert_trigger")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "👥 租户管理")
async def admin_menu_tenants(message: Message, state: FSMContext):
    await state.clear()
    text = "👥 <b>SaaS 租户(代理)管理中心</b>\n\n在此管理您的下级代理，生成分销卡密、执行账户一键封禁或清理不活跃账号。"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 生成新租户激活码", callback_data="admin_tenant:gen_code")],
        [InlineKeyboardButton(text="📊 租户利润与状态列表", callback_data="admin_tenant_list_profit")],
        [InlineKeyboardButton(text="🧟 僵尸租户清理设置", callback_data="admin_tenant_zombie_set")],
        [InlineKeyboardButton(text="🚫 封禁/解封指定租户", callback_data="admin_toggle_ban_tenant")]  # 👈 新增按钮
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "💰 财务管理")
async def admin_menu_finance(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    
    text = (
        "💰 <b>平台财务结算中心</b>\n\n"
        f"📉 当前设置的最低提现门槛为：<b>{format_amount(config.min_withdraw_amount)} TRX</b>\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ 待处理提现审核", callback_data="admin_fin_pending_wd")],
        [InlineKeyboardButton(text="🏆 租户余额排行榜(分页)", callback_data="admin_fin_tenant_ranks")],
        [InlineKeyboardButton(text="📉 设置最低提现门槛(TRX)", callback_data="admin_set_min_withdraw")],
        [InlineKeyboardButton(text="✍️ 手工资金调账", callback_data="admin_manual_adjust_balance")]  # 👈 新增按钮
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "📢 平台全员广播")
async def admin_menu_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminConfigFSM.waiting_for_broadcast_content)
    text = (
        "📢 <b>全员消息广播引擎</b>\n\n"
        "系统将向所有存活的租户和散客发送广播。\n"
        "⚠️ <i>注：底层引擎已配置令牌桶限流算法，将以 25条/秒 的安全速率推送，严防 Telegram 官方封号。</i>\n\n"
        "👉 <b>HTML排版使用指南：</b>\n"
        "• 加粗文本：<code>&lt;b&gt;我是粗体&lt;/b&gt;</code>\n"
        "• 一键复制：<code>&lt;code&gt;点我复制&lt;/code&gt;</code>\n"
        "• 网页超链接：<code>&lt;a href=\"https://xxx.com\"&gt;点击访问&lt;/a&gt;</code>\n\n"
        "✍️ 请直接在此输入您要广播的内容，或者发送 /cancel 取消操作："
    )
    await message.answer(text, parse_mode="HTML")

# =====================================================================
# 🛡️ 安全版全员广播引擎 (带令牌桶防封号机制)
# =====================================================================
@admin_router.message(AdminConfigFSM.waiting_for_broadcast_content)
async def process_broadcast_content(message: Message, state: FSMContext, session: AsyncSession):
    if message.text and message.text.strip().lower() == '/cancel':
        await state.clear()
        return await message.answer("✅ 已取消广播发布。")

    broadcast_html = message.html_text
    if not broadcast_html:
        return await message.answer("❌ 无法识别内容，请输入有效的文本格式广播。")

    await state.clear()
    
    # 1. 抓取全站所有需要通知的人（散客 + 代理）
    # 获取所有的代理 TG ID
    tenant_stmt = select(Tenant.owner_tg_id).where(Tenant.is_active == True)
    tenant_ids = (await session.scalars(tenant_stmt)).all()
    
    # 获取所有的散客 TG ID
    user_stmt = select(User.tg_user_id)
    user_ids = (await session.scalars(user_stmt)).all()
    
    # 去重合并集合
    all_target_ids = set(tenant_ids + user_ids)
    
    if not all_target_ids:
        return await message.answer("⚠️ 当前平台没有任何有效用户，广播取消。")

    # 2. 返回任务启动确认
    status_msg = await message.answer(
        f"🚀 <b>全员广播已启动</b>\n\n"
        f"🎯 预计推送总人数：<b>{len(all_target_ids)} 人</b>\n"
        f"⏳ 正在后台列队发送中，为防官方限流，请耐心等待...",
        parse_mode="HTML"
    )

    # 3. 开始防风控的平滑推送 (后台协程异步处理，不阻塞主程序)
    import asyncio
    async def safe_broadcast_task(target_ids, html_content, status_message: Message):
        success_count = 0
        fail_count = 0
        
        for tg_id in target_ids:
            try:
                await message.bot.send_message(chat_id=tg_id, text=html_content, parse_mode="HTML")
                success_count += 1
            except Exception:
                fail_count += 1
            # 🛡️ 防封核心：每发一条强行 sleep 0.05 秒（控制在 20条/秒 极限安全阈值内）
            await asyncio.sleep(0.05)
            
        # 发送完毕后更新状态报告
        report = (
            f"✅ <b>全员广播任务执行完毕！</b>\n\n"
            f"📨 成功送达：<b>{success_count}</b> 人\n"
            f"🚫 失败/拉黑：<b>{fail_count}</b> 人"
        )
        try:
            await status_message.edit_text(report, parse_mode="HTML")
        except Exception:
            pass

    # 将任务推入后台
    asyncio.create_task(safe_broadcast_task(all_target_ids, broadcast_html, status_msg))

# =====================================================================
# ==================== 3. 豪华版：克隆机器人套餐管理 ====================
# =====================================================================
def get_clone_fee_dict(config_str: str) -> dict:
    if not config_str: return {"_is_open": True}
    try:
        data = json.loads(config_str)
        if "_is_open" not in data: data["_is_open"] = True
        return data
    except Exception:
        return {"_is_open": True}

async def render_clone_packages_panel(target, session: AsyncSession):
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    data = get_clone_fee_dict(getattr(config, 'clone_fee_config', '{}'))
    is_open = data.get("_is_open", True)
    status_emoji = "🟢 开放购买" if is_open else "🔴 暂停购买"
    pkgs = {k: v for k, v in data.items() if k != "_is_open"}
    sorted_days = sorted(pkgs.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)
    
    text = f"📦 <b>多级克隆套餐管理</b>\n\n· 当前状态：{status_emoji}\n· 套餐列表：\n"
    if not sorted_days:
        text += "<i>(当前暂无套餐数据)</i>\n"
    else:
        for day in sorted_days:
            text += f"🔷 <b>{day}天</b> ➡️ <code>{format_amount(pkgs[day])}</code> USDT\n"
            
    kb = InlineKeyboardBuilder()
    for day in sorted_days:
        kb.row(
            InlineKeyboardButton(text=f"✏️ 修改 {day}天", callback_data=f"admin_pkg_edit:{day}"),
            InlineKeyboardButton(text=f"🗑 删除", callback_data=f"admin_pkg_del:{day}")
        )
    kb.row(InlineKeyboardButton(text="➕ 添加套餐", callback_data="admin_pkg_add"))
    kb.row(InlineKeyboardButton(text="🔄 切换购买状态", callback_data="admin_pkg_toggle"))
    kb.row(InlineKeyboardButton(text="🔙 返回定价菜单", callback_data="admin_menu_pricing_back"))
    
    markup = kb.as_markup()
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")

@admin_router.callback_query(F.data == "admin_set_packages")
async def trigger_packages_panel(call: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    await render_clone_packages_panel(call, session)

# ⚠️ 共用返回按钮
@admin_router.callback_query(F.data == "admin_menu_pricing_back")
async def back_to_pricing_menu(call: CallbackQuery, session: AsyncSession, state: FSMContext):
    await admin_menu_pricing(call, session, state)

@admin_router.callback_query(F.data == "admin_pkg_toggle")
async def toggle_packages_status(call: CallbackQuery, session: AsyncSession):
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    data = get_clone_fee_dict(getattr(config, 'clone_fee_config', '{}'))
    data["_is_open"] = not data.get("_is_open", True)
    await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(clone_fee_config=json.dumps(data)))
    await session.commit()
    await call.answer("✅ 购买状态已切换！")
    await render_clone_packages_panel(call, session)

@admin_router.callback_query(F.data.startswith("admin_pkg_del:"))
async def delete_package(call: CallbackQuery, session: AsyncSession):
    day_to_del = call.data.split(":")[1]
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    data = get_clone_fee_dict(getattr(config, 'clone_fee_config', '{}'))
    if day_to_del in data:
        data.pop(day_to_del)
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(clone_fee_config=json.dumps(data)))
        await session.commit()
        await call.answer(f"✅ {day_to_del}天套餐已删除")
    else:
        await call.answer("⚠️ 套餐不存在", show_alert=True)
    await render_clone_packages_panel(call, session)

@admin_router.callback_query(F.data.startswith("admin_pkg_edit:"))
async def edit_package_start(call: CallbackQuery, state: FSMContext):
    day_to_edit = call.data.split(":")[1]
    await state.update_data(edit_pkg_day=day_to_edit)
    await state.set_state(AdminConfigFSM.wait_clone_pkg_edit_price)
    await call.message.edit_text(f"✏️ 请输入 <b>{day_to_edit} 天</b> 套餐的全新价格 (USDT)：\n<i>发送 /cancel 取消操作</i>", parse_mode="HTML")
    await call.answer()

# ----------------- 6. 豪华克隆面板：修改特定天数价格 -----------------
@admin_router.message(AdminConfigFSM.wait_clone_pkg_edit_price)
async def edit_package_finish(message: Message, state: FSMContext, session: AsyncSession):
    if message.text == "/cancel":
        await state.clear()
        return await render_clone_packages_panel(message, session)
        
    try:
        new_price = float(message.text.strip())
        if new_price < 0:
            raise ValueError
    except ValueError:
        # 拦截：拦截失败继续留在当前状态
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
        
    try:
        state_data = await state.get_data()
        day_to_edit = state_data["edit_pkg_day"]
        
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        data = get_clone_fee_dict(getattr(config, 'clone_fee_config', '{}'))
        data[day_to_edit] = new_price
        
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(clone_fee_config=json.dumps(data)))
        await session.commit()
        
        await message.answer("✅ 价格修改成功！")
        await state.clear()
        await render_clone_packages_panel(message, session)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 数据库操作异常：{str(e)}")
        await state.clear()

@admin_router.callback_query(F.data == "admin_pkg_add")
async def add_package_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_clone_pkg_days)
    await call.message.edit_text("✏️ 第一步：请输入新套餐的 <b>有效天数</b> (例如: 30)：\n<i>发送 /cancel 取消操作</i>", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_clone_pkg_days)
async def add_package_days(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
    if not message.text.strip().isdigit():
        return await message.answer("❌ 天数必须是纯整数！请重新输入：")
    await state.update_data(new_pkg_day=message.text.strip())
    await state.set_state(AdminConfigFSM.wait_clone_pkg_price)
    await message.answer(f"✏️ 第二步：请输入 <b>{message.text.strip()} 天</b> 套餐的价格 (USDT)：")

# ----------------- 7. 豪华克隆面板：添加新价格 -----------------
@admin_router.message(AdminConfigFSM.wait_clone_pkg_price)
async def add_package_finish(message: Message, state: FSMContext, session: AsyncSession):
    if message.text == "/cancel":
        await state.clear()
        return await render_clone_packages_panel(message, session)
        
    try:
        new_price = float(message.text.strip())
        if new_price < 0:
            raise ValueError
    except ValueError:
        # 拦截：拦截失败继续留在当前状态
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
        
    try:
        state_data = await state.get_data()
        new_pkg_day = state_data["new_pkg_day"]
        
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        data = get_clone_fee_dict(getattr(config, 'clone_fee_config', '{}'))
        data[new_pkg_day] = new_price
        
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(clone_fee_config=json.dumps(data)))
        await session.commit()
        
        await message.answer("✅ 新套餐添加成功！")
        await state.clear()
        await render_clone_packages_panel(message, session)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 数据库操作异常：{str(e)}")
        await state.clear()


# =====================================================================
# ==================== 4. 豪华版：特价功能授权套餐管理 ====================
# =====================================================================
def get_spec_auth_dict(config_str: str) -> dict:
    if not config_str: return {}
    try:
        return json.loads(config_str)
    except Exception:
        return {}

async def render_spec_packages_panel(target, session: AsyncSession):
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    is_open = config.is_special_energy_global_enabled
    status_emoji = "🟢 开放授权" if is_open else "🔴 暂停授权"
    data = get_spec_auth_dict(getattr(config, 'special_auth_config', '{}'))
    sorted_days = sorted(data.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)
    
    text = f"💎 <b>特价功能授权套餐管理</b>\n\n· 当前全局状态：{status_emoji}\n· 套餐列表明细：\n"
    if not sorted_days:
        text += "<i>(当前暂无套餐数据，请添加)</i>\n"
    else:
        for day in sorted_days:
            text += f"🔷 <b>{day}天</b> ➡️ <code>{format_amount(data[day])}</code> USDT\n"
            
    kb = InlineKeyboardBuilder()
    for day in sorted_days:
        kb.row(
            InlineKeyboardButton(text=f"✏️ 修改 {day}天价格", callback_data=f"admin_edit_spec_pkg:{day}"),
            InlineKeyboardButton(text=f"🗑 删除", callback_data=f"admin_del_spec_pkg:{day}")
        )
    kb.row(InlineKeyboardButton(text="➕ 添加特价套餐", callback_data="admin_add_spec_pkg_start"))
    kb.row(InlineKeyboardButton(text="🔄 切换授权状态", callback_data="admin_toggle_spec_auth_status"))
    kb.row(InlineKeyboardButton(text="🔙 返回定价菜单", callback_data="admin_menu_pricing_back"))
    
    markup = kb.as_markup()
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")

@admin_router.callback_query(F.data == "admin_manage_spec_packages")
async def trigger_spec_packages_panel(call: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    await render_spec_packages_panel(call, session)

@admin_router.callback_query(F.data == "admin_toggle_spec_auth_status")
async def toggle_spec_auth_status(call: CallbackQuery, session: AsyncSession):
    try:
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        new_status = not config.is_special_energy_global_enabled
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(is_special_energy_global_enabled=new_status))
        await session.commit()
        await call.answer("✅ 全局授权状态已切换！")
        await render_spec_packages_panel(call, session)
    except Exception as e:
        await session.rollback()
        await call.answer("❌ 状态切换失败", show_alert=True)

@admin_router.callback_query(F.data.startswith("admin_del_spec_pkg:"))
async def delete_spec_package(call: CallbackQuery, session: AsyncSession):
    day_to_del = call.data.split(":")[1]
    try:
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        data = get_spec_auth_dict(getattr(config, 'special_auth_config', '{}'))
        if day_to_del in data:
            data.pop(day_to_del)
            await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(special_auth_config=json.dumps(data)))
            await session.commit()
            await call.answer(f"✅ {day_to_del}天套餐已删除")
        else:
            await call.answer("⚠️ 该套餐不存在", show_alert=True)
        await render_spec_packages_panel(call, session)
    except Exception as e:
        await session.rollback()
        await call.answer("❌ 删除失败", show_alert=True)

@admin_router.callback_query(F.data.startswith("admin_edit_spec_pkg:"))
async def edit_spec_package_start(call: CallbackQuery, state: FSMContext):
    day_to_edit = call.data.split(":")[1]
    await state.update_data(edit_spec_day=day_to_edit)
    await state.set_state(AdminConfigFSM.wait_spec_pkg_edit_price)
    await call.message.edit_text(f"✏️ 请输入 <b>{day_to_edit} 天</b> 特价套餐的全新价格 (USDT)：\n<i>发送 /cancel 取消操作</i>", parse_mode="HTML")
    await call.answer()

# ----------------- 8. 豪华特价面板：修改特定天数价格 -----------------
@admin_router.message(AdminConfigFSM.wait_spec_pkg_edit_price)
async def edit_spec_package_finish(message: Message, state: FSMContext, session: AsyncSession):
    if message.text == "/cancel":
        await state.clear()
        return await render_spec_packages_panel(message, session)
        
    try:
        new_price = float(message.text.strip())
        if new_price < 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
        
    try:
        state_data = await state.get_data()
        day_to_edit = str(state_data["edit_spec_day"])
        
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        data = get_spec_auth_dict(getattr(config, 'special_auth_config', '{}'))
        data[day_to_edit] = new_price
        
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(special_auth_config=json.dumps(data)))
        await session.commit()
        
        await message.answer("✅ 价格修改成功！")
        await state.clear()
        await render_spec_packages_panel(message, session)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 发生数据库错误：{str(e)}")
        await state.clear()

@admin_router.callback_query(F.data == "admin_add_spec_pkg_start")
async def add_spec_package_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_spec_pkg_days)
    await call.message.edit_text("✏️ 第一步：请输入特价授权套餐的 <b>有效天数</b> (例如: 30)：\n<i>发送 /cancel 取消操作</i>", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_spec_pkg_days)
async def add_spec_package_days(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
    if not message.text.strip().isdigit():
        return await message.answer("❌ 天数必须是纯整数！请重新输入：")
    await state.update_data(new_spec_day=message.text.strip())
    await state.set_state(AdminConfigFSM.wait_spec_pkg_price)
    await message.answer(f"✏️ 第二步：请输入 <b>{message.text.strip()} 天</b> 授权套餐的价格 (USDT)：")

# ----------------- 9. 豪华特价面板：添加新价格 -----------------
@admin_router.message(AdminConfigFSM.wait_spec_pkg_price)
async def add_spec_package_finish(message: Message, state: FSMContext, session: AsyncSession):
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    try:
        new_price = float(message.text.strip())
        if new_price < 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
        
    try:
        state_data = await state.get_data()
        new_spec_day = str(state_data["new_spec_day"])
        
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        data = get_spec_auth_dict(getattr(config, 'special_auth_config', '{}'))
        data[new_spec_day] = new_price
        
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(special_auth_config=json.dumps(data)))
        await session.commit()
        
        await message.answer("✅ 新的特价授权套餐添加成功！")
        await state.clear()
        await render_spec_packages_panel(message, session)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 发生数据库错误：{str(e)}")
        await state.clear()


# =====================================================================
# ==================== 5. 单一参数动态配置闭环 ====================
# =====================================================================

@admin_router.callback_query(F.data == "admin_toggle_special_global")
async def toggle_special_global(call: CallbackQuery, session: AsyncSession, state: FSMContext):
    try:
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        new_status = not config.is_special_energy_global_enabled
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(is_special_energy_global_enabled=new_status))
        await session.commit()
        await call.answer(f"✅ 特价全局开关已{'开启' if new_status else '关闭'}！", show_alert=False)
        await admin_menu_pricing(call.message, session, state)
    except Exception as e:
        await session.rollback()
        await call.answer(f"❌ 切换失败，发生异常：{str(e)}", show_alert=True)

@admin_router.callback_query(F.data == "admin_toggle_cs_display")
async def toggle_cs_display(call: CallbackQuery, session: AsyncSession, state: FSMContext):
    try:
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        new_status = not config.show_customer_service
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(show_customer_service=new_status))
        await session.commit()
        await call.answer(f"✅ 客服显示状态已{'开启' if new_status else '隐藏'}！", show_alert=False)
        await call.message.delete()
        await admin_menu_settings(call.message, session, state)
    except Exception as e:
        await session.rollback()
        await call.answer(f"❌ 切换失败，发生异常：{str(e)}", show_alert=True)

# =====================================================================
# 模块：超管全局预警余额阈值动态配置
# =====================================================================

# 1. 点击按钮触发：进入超管余额预警设置
@admin_router.callback_query(F.data == "admin_set_alert_trigger")
async def cb_start_alert_setup(call: CallbackQuery, state: FSMContext):
    """点击预警余额通知按钮，开始配置"""
    await call.message.edit_text(
        "⚙️ <b>【全局设置 - 预警余额通知配置】</b>\n\n"
        "第一步：\n"
        "✏️ <b>请输入【超管 Netts 余额预警值】（TRX 数量，例如 50）：</b>\n"
        "<i>当您的 Netts 上游进货池余额低于此数值时，系统将向您报警。</i>\n\n"
        "(发送 /cancel 可退出配置)",
        parse_mode="HTML"
    )
    await state.set_state(AdminAlertSetupFSM.waiting_for_admin_limit)
    await call.answer()

# 2. 接收并处理超管预警值，引导进入租户预警设置
@admin_router.message(AdminAlertSetupFSM.waiting_for_admin_limit)
async def process_admin_alert_limit(message: Message, state: FSMContext):
    """处理超管预警线输入"""
    if message.text.strip() == '/cancel':
        await state.clear()
        await message.answer("✅ 已取消预警配置。")
        return

    input_val = message.text.strip()
    try:
        admin_limit = float(input_val)
        if admin_limit < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ 输入错误！请输入一个有效的正数（如 50 或 100.5）：")
        return

    # 暂存超管数据到内存状态机中
    await state.update_data(admin_limit=admin_limit)
    
    # 引导进入第二步
    await message.answer(
        "⚙️ <b>【全局设置 - 预警余额通知配置】</b>\n\n"
        "第二步：\n"
        "✏️ <b>请输入【特价租户余额预警值】（TRX 数量，例如 15）：</b>\n"
        "<i>当特价子商铺的【充值本金】低于此数值时，系统将自动向租户发送充值催收通知。</i>\n\n"
        "(发送 /cancel 可退出配置)",
        parse_mode="HTML"
    )
    await state.set_state(AdminAlertSetupFSM.waiting_for_tenant_limit)

# 3. 接收租户预警值，并永久写入数据库动态生效
@admin_router.message(AdminAlertSetupFSM.waiting_for_tenant_limit)
async def process_tenant_alert_limit(message: Message, state: FSMContext, session: AsyncSession):
    """处理租户预警线输入并最终保存"""
    if message.text.strip() == '/cancel':
        await state.clear()
        await message.answer("✅ 已取消预警配置。")
        return

    input_val = message.text.strip()
    try:
        tenant_limit = float(input_val)
        if tenant_limit < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ 输入错误！请输入一个有效的正数（如 15 或 20.5）：")
        return

    # 从内存中取出第一步暂存的超管数据
    data = await state.get_data()
    admin_limit = data.get("admin_limit")
    await state.clear()

    # 事务持久化入库
    try:
        config_stmt = select(SystemConfig).where(SystemConfig.id == 1)
        config = (await session.execute(config_stmt)).scalar_one_or_none()
        
        if not config:
            await message.answer("❌ 数据库中未初始化 SystemConfig(id=1) 记录，保存失败！")
            return

        # 更新阈值字段 (请确保 models.py 中已存在这两个字段)
        config.netts_alert_threshold = Decimal(str(admin_limit))
        config.tenant_alert_threshold = Decimal(str(tenant_limit))
        
        await session.commit()

        success_text = (
            "🚀 <b>预警阈值全新配置成功！已经实时生效！</b>\n\n"
            f"1️⃣ <b>超管 Netts 余额预警线</b>：<code>{admin_limit}</code> TRX\n"
            f"2️⃣ <b>特价租户本金预警线</b>：<code>{tenant_limit}</code> TRX\n\n"
            "<i>💡 提示：后台监控雷达引擎在下次巡航扫描时，将自动切换至此全新阈值，无需重启服务器。</i>"
        )
        await message.answer(success_text, parse_mode="HTML")
        
        # 无缝返回全局设置主菜单
        await admin_menu_settings(message, session, state)

    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 数据库保存失败，资金与配置安全回滚：{str(e)}")

@admin_router.callback_query(F.data == "admin_set_markup_65k")
async def trigger_set_markup_65k(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_markup_65k)
    await call.message.answer("✏️ 请输入新的 <b>65K 能量固定加价</b> (TRX)，例如: 1.5", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_markup_65k)
async def save_markup_65k(message: Message, state: FSMContext, session: AsyncSession):
    try:
        new_price = Decimal(message.text.strip())
        if new_price < 0:
            raise ValueError  # 拦截负数
    except Exception:
        # 拦截失败：不清除状态 (state.clear())，让超管继续在当前状态输入
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
    
    try:
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(base_cost_65k=new_price))
        await session.commit()
        await message.answer(f"✅ 修改成功！65K 平台加价已更新为 <b>{format_amount(new_price)} TRX</b>。", parse_mode="HTML")
        await state.clear()
        await admin_menu_pricing(message, session, state)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 保存失败，数据库发生异常：{str(e)}")
        await state.clear()

@admin_router.callback_query(F.data == "admin_set_markup_131k")
async def trigger_set_markup_131k(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_markup_131k)
    await call.message.answer("✏️ 请输入新的 <b>131K 能量固定加价</b> (TRX)，例如: 3.0", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_markup_131k)
async def save_markup_131k(message: Message, state: FSMContext, session: AsyncSession):
    try:
        new_price = Decimal(message.text.strip())
        if new_price < 0:
            raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
    
    try:
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(base_cost_131k=new_price))
        await session.commit()
        await message.answer(f"✅ 修改成功！131K 平台加价已更新为 <b>{format_amount(new_price)} TRX</b>。", parse_mode="HTML")
        await state.clear()
        await admin_menu_pricing(message, session, state)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 保存失败，数据库发生异常：{str(e)}")
        await state.clear()

# routers/admin.py (替换 65K 与 131K 对应的底价设置)

# --- 设 65K 特价底价 ---
@admin_router.callback_query(F.data == "admin_set_special_fee_65k")
async def trigger_set_special_fee_65k(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_special_fee_65k)
    text = (
        "✏️ 请输入新的 65K 全局特价售价 (TRX)\n"
        "💡 提示：输入 0 表示全局关闭/禁止下发此档位能量\n"
        "例如: 1.2\n"
        "发送 /cancel 取消操作"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_special_fee_65k)
async def save_special_fee_65k(message: Message, state: FSMContext, session: AsyncSession):
    try:
        new_price = Decimal(message.text.strip())
        if new_price < 0: 
            raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
    
    try:
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(special_base_cost_65k=new_price))
        await session.commit()
        
        status_text = f"<b>{format_amount(new_price)} TRX</b>" if new_price > 0 else "<b>全局关闭（禁止下发）</b>"
        await message.answer(f"✅ 修改成功！65K 全局特价售价已更新为 {status_text}。", parse_mode="HTML")
        await state.clear()
        await admin_menu_pricing(message, session, state)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 保存失败，发生异常：{str(e)}")
        await state.clear()
        # ...异常处理不变...

# 4. 替换 131K 触发器与保存状态的提示文案 (同理)
@admin_router.callback_query(F.data == "admin_set_special_fee_131k")
async def trigger_set_special_fee_131k(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_special_fee_131k)
    text = (
        "✏️ 请输入新的 131K 全局特价售价 (TRX)\n"
        "💡 提示：输入 0 表示全局关闭/禁止下发此档位能量\n"
        "例如: 2.4\n"
        "发送 /cancel 取消操作"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_special_fee_131k)
async def save_special_fee_131k(message: Message, state: FSMContext, session: AsyncSession):
    try:
        new_price = Decimal(message.text.strip())
        if new_price < 0: 
            raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
    
    try:
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(special_base_cost_131k=new_price))
        await session.commit()
        
        status_text = f"<b>{format_amount(new_price)} TRX</b>" if new_price > 0 else "<b>全局关闭（禁止下发）</b>"
        await message.answer(f"✅ 修改成功！131K 全局特价售价已更新为 {status_text}。", parse_mode="HTML")
        await state.clear()
        await admin_menu_pricing(message, session, state)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 保存失败，发生异常：{str(e)}")
        await state.clear()
        

@admin_router.callback_query(F.data == "admin_set_unactivated_fee")
async def trigger_set_unactivated_fee(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_unactivated_fee)
    await call.message.answer("✏️ 请输入新的 <b>未激活地址附加费</b> (TRX)，例如: 2.0", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_unactivated_fee)
async def save_unactivated_fee(message: Message, state: FSMContext, session: AsyncSession):
    try:
        new_fee = Decimal(message.text.strip())
        if new_fee < 0:
            raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
    
    try:
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(unactivated_fee_trx=new_fee))
        await session.commit()
        await message.answer(f"✅ 修改成功！未激活附加费已更新为 <b>{format_amount(new_fee)} TRX</b>。", parse_mode="HTML")
        await state.clear()
        await admin_menu_pricing(message, session, state)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 保存失败，发生数据库异常：{str(e)}")
        await state.clear()

@admin_router.callback_query(F.data == "admin_set_special_address")
async def trigger_set_special_address(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_special_address)
    await call.message.answer("✏️ 请输入新的 <b>全局特价能量兜底收款地址</b>\n⚠️ <i>必须是以大写 T 开头的 34 位波场地址！</i>", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_special_address)
async def save_special_address(message: Message, state: FSMContext, session: AsyncSession):
    addr = message.text.strip()
    if not addr.startswith("T") or len(addr) != 34:
        return await message.answer("❌ 地址格式不正确！必须是以大写 T 开头的 34 位波场地址。")
    await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(global_special_address=addr))
    await session.commit()
    await message.answer(f"✅ <b>修改成功！</b>兜底特价地址已更新为：\n<code>{addr}</code>", parse_mode="HTML")
    await state.clear()
    await admin_menu_pricing(message, session, state)

@admin_router.callback_query(F.data == "admin_set_master_wallet")
async def trigger_set_master_wallet(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_master_wallet)
    await call.message.answer("✏️ 请输入新的 <b>全站主收款地址</b>\n⚠️ <i>重要：这是您全站收钱的波场钱包，请务必核对大写 T 开头的 34 位字符！</i>", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_master_wallet)
async def save_master_wallet(message: Message, state: FSMContext, session: AsyncSession):
    addr = message.text.strip()
    if not addr.startswith("T") or len(addr) != 34:
        return await message.answer("❌ 地址格式不正确！必须是以大写 T 开头的 34 位波场地址。")
    await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(master_receive_address=addr))
    await session.commit()
    await message.answer(f"✅ <b>修改成功！</b>\n资金现已转入新主收款地址：\n<code>{addr}</code>", parse_mode="HTML")
    await state.clear()
    await admin_menu_settings(message, session, state)

@admin_router.callback_query(F.data == "admin_set_min_withdraw")
async def trigger_set_min_withdraw(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_min_withdraw)
    await call.message.answer("✏️ 请输入新的 <b>最低提现门槛</b> (TRX)，例如: 100", parse_mode="HTML")
    await call.answer()

# ----------------- 5. 保存最低提现门槛 -----------------
@admin_router.message(AdminConfigFSM.wait_min_withdraw)
async def save_min_withdraw(message: Message, state: FSMContext, session: AsyncSession):
    try:
        new_threshold = Decimal(message.text.strip())
        if new_threshold < 0:
            raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误！金额必须是有效的大于等于 0 的数字，请重新输入：")
    
    try:
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(min_withdraw_amount=new_threshold))
        await session.commit()
        await message.answer(f"✅ 修改成功！最低提现门槛已更新为 <b>{format_amount(new_threshold)} TRX</b>。", parse_mode="HTML")
        await state.clear()
        await admin_menu_finance(message, session, state)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 保存提现门槛失败：{str(e)}")
        await state.clear()

@admin_router.callback_query(F.data == "admin_set_welcome_msg")
async def trigger_set_welcome_msg(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_welcome_message)
    await call.message.answer("✏️ 请输入新的 <b>全局默认欢迎语</b> (支持 HTML 格式)：", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_welcome_message)
async def save_welcome_msg(message: Message, state: FSMContext, session: AsyncSession):
    text = message.html_text
    await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(global_welcome_template=text))
    await session.commit()
    await message.answer("✅ 全局默认欢迎语修改成功！")
    await state.clear()
    await admin_menu_settings(message, session, state)


# =====================================================================
# ==================== 6. 客服、节点管理与卡密生成 ====================
# =====================================================================

# --- Tron API 节点池管理 ---
# --- Tron API 节点池管理 ---
async def render_api_node_panel(target, session: AsyncSession):
    # 彻底废弃 SystemConfig 的文本字段，直接读取 TronApiNode 真实表
    stmt = select(TronApiNode).order_by(TronApiNode.id)
    nodes = (await session.execute(stmt)).scalars().all()
    
    text = "🌐 <b>Tron API 节点池管理</b>\n\n系统将自动轮询以下节点以保证高并发下的可用性：\n\n"
    kb = InlineKeyboardBuilder()
    if not nodes:
        text += "<i>⚠️ 当前暂无可用节点，请尽快追加！</i>\n"
    else:
        for idx, node in enumerate(nodes):
            key = node.api_key
            masked_key = f"{key[:6]}****{key[-4:]}" if len(key) > 10 else "****"
            # 同步展示后端的自动熔断状态与失败次数
            status = "🟢 正常" if node.is_active else "🔴 已熔断"
            text += f"{idx + 1}. <code>{masked_key}</code> [{status} | 失败:{node.fail_count}次]\n"
            # 删除按钮的 callback 携带数据库真实的物理 ID
            kb.row(InlineKeyboardButton(text=f"🗑 删除节点 {idx + 1}", callback_data=f"admin_del_node:{node.id}"))
            
    kb.row(InlineKeyboardButton(text="➕ 追加新节点", callback_data="admin_add_api_node"))
    kb.row(InlineKeyboardButton(text="🔙 返回全局设置", callback_data="admin_menu_settings_back"))
    
    markup = kb.as_markup()
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")

@admin_router.callback_query(F.data == "admin_set_tron_api")
async def trigger_api_node_panel(call: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    await render_api_node_panel(call, session)

@admin_router.callback_query(F.data == "admin_menu_settings_back")
async def back_to_settings_menu(call: CallbackQuery, session: AsyncSession, state: FSMContext):
    await call.message.delete()
    await admin_menu_settings(call.message, session, state)

@admin_router.callback_query(F.data == "admin_add_api_node")
async def add_api_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_api_nodes)
    await call.message.edit_text("✏️ 请输入新的 <b>TronGrid API Key</b>：\n<i>(系统将自动追加到节点池中，发送 /cancel 取消)</i>", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_api_nodes)
async def save_api_add(message: Message, state: FSMContext, session: AsyncSession):
    if message.text == "/cancel":
        await state.clear()
        return await render_api_node_panel(message, session)
        
    new_key = message.text.strip()
    try:
        # 查询该 Key 是否已在真实节点表中
        existing_node = await session.scalar(select(TronApiNode).where(TronApiNode.api_key == new_key))
        if existing_node:
            await message.answer("⚠️ 该 API Key 已存在于节点池中，请勿重复添加！")
            await state.clear()
            return await render_api_node_panel(message, session)
            
        # 写入真实的 TronApiNode 表，状态默认激活
        new_node = TronApiNode(api_key=new_key, is_active=True, fail_count=0)
        session.add(new_node)
        await session.commit()
        
        await message.answer("✅ 新节点追加成功，扫块引擎将立即生效！")
        await state.clear()
        await render_api_node_panel(message, session)
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 追加节点失败，数据库错误：{str(e)}")
        await state.clear()

@admin_router.callback_query(F.data.startswith("admin_del_node:"))
async def del_api_node(call: CallbackQuery, session: AsyncSession):
    # 提取数据库真实的节点 ID
    node_id = int(call.data.split(":")[1])
    try:
        # 基于主键定位并删除
        node_to_delete = await session.get(TronApiNode, node_id)
        if node_to_delete:
            await session.delete(node_to_delete)
            await session.commit()
            await call.answer(f"✅ 节点已成功删除！", show_alert=False)
        else:
            await call.answer("⚠️ 该节点不存在或已被删除", show_alert=True)
            
        await render_api_node_panel(call, session)
    except Exception:
        await session.rollback()
        await call.answer("❌ 删除失败，数据库异常", show_alert=True)

# --- 客服列表管理 ---
@admin_router.callback_query(F.data == "admin_set_customer_service")
async def menu_customer_service(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 显示当前客服列表", callback_data="admin_cs_list")],
        [InlineKeyboardButton(text="➕ 添加客服", callback_data="admin_cs_add"), InlineKeyboardButton(text="🗑 删除客服", callback_data="admin_cs_del")],
        [InlineKeyboardButton(text="🔙 返回全局设置", callback_data="admin_menu_settings_back")]
    ])
    await call.message.edit_text("💁 <b>客服配置管理</b>\n请选择操作：", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "admin_cs_list")
async def list_customer_service(call: CallbackQuery, session: AsyncSession):
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    links = config.customer_service_link.split(',') if config.customer_service_link else []
    if not links:
        return await call.answer("暂无客服配置", show_alert=True)
    msg = "📋 <b>当前客服列表：</b>\n"
    for i, link in enumerate(links, 1):
        msg += f"{i}. <code>{link}</code>\n"
    await call.message.answer(msg, parse_mode="HTML")
    await call.answer()

@admin_router.callback_query(F.data == "admin_cs_add")
async def add_customer_service_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_customer_service)
    await call.message.answer("✏️ 请输入要添加的客服链接（例如：@kefu123）：")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_customer_service)
async def save_customer_service_add(message: Message, state: FSMContext, session: AsyncSession):
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    links = config.customer_service_link.split(',') if config.customer_service_link else []
    links.append(message.text.strip())
    await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(customer_service_link=",".join(links)))
    await session.commit()
    await message.answer("✅ 客服添加成功！")
    await state.clear()

@admin_router.callback_query(F.data == "admin_cs_del")
async def del_customer_service_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_cs_del)
    await call.message.answer("✏️ 请输入要删除的客服序号（请先点击【显示当前客服列表】查看序号）：")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_cs_del)
async def save_customer_service_del(message: Message, state: FSMContext, session: AsyncSession):
    try:
        idx = int(message.text.strip()) - 1
        config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        links = config.customer_service_link.split(',') if config.customer_service_link else []
        if idx < 0 or idx >= len(links):
            raise ValueError
        removed = links.pop(idx)
        await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(customer_service_link=",".join(links)))
        await session.commit()
        await message.answer(f"✅ 客服 <code>{removed}</code> 已删除！", parse_mode="HTML")
        await state.clear()
    except ValueError:
        await message.answer("❌ 序号无效，请输入正确的数字！")

# --- 僵尸清理配置 ---
@admin_router.callback_query(F.data == "admin_tenant_zombie_set")
async def trigger_set_zombie_days(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    await state.set_state(AdminConfigFSM.wait_zombie_days)
    text = (
        f"✏️ 当前僵尸阈值：<b>{config.zombie_tenant_days} 天</b>\n"
        "请输入新的 <b>僵尸租户清理阈值</b> (天数)：\n\n"
        "⚠️ <i>说明：此操作仅用于修改清理阈值。实际的断开挂载与软删除操作，将由系统定时任务自动在后台执行！</i>"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_zombie_days)
async def save_zombie_days(message: Message, state: FSMContext, session: AsyncSession):
    try:
        days = int(message.text.strip())
        if days < 1: raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误，请输入大于 0 的纯数字天数！")
    await session.execute(update(SystemConfig).where(SystemConfig.id == 1).values(zombie_tenant_days=days))
    await session.commit()
    await message.answer(f"✅ 修改成功！僵尸租户清理阈值已更新为 <b>{days} 天</b>。", parse_mode="HTML")
    await state.clear()

# --- 生成卡密流程 ---
@admin_router.callback_query(F.data == "admin_tenant:gen_code")
async def generate_activation_code_start(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 克隆机器人", callback_data="admin_gen_code:basic")],
        [InlineKeyboardButton(text="🔥 克隆 + 特价能量", callback_data="admin_gen_code:pro")]
    ])
    await call.message.edit_text("⚙️ <b>请选择您要生成的代理卡密套餐类型：</b>", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("admin_gen_code:"))
async def generate_activation_code_type(call: CallbackQuery, state: FSMContext):
    code_type = call.data.split(":")[1]
    await state.update_data(code_type=code_type)
    await state.set_state(AdminConfigFSM.wait_activation_days)
    await call.message.edit_text("✍️ 请直接输入该激活码的有效天数（例如：30）：")

@admin_router.message(AdminConfigFSM.wait_activation_days)
async def generate_activation_code_finish(message: Message, state: FSMContext, session: AsyncSession):
    try:
        duration_days = int(message.text.strip())
        if duration_days <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ 格式错误，请输入大于 0 的纯数字天数！")

    data = await state.get_data()
    code_type = data.get("code_type", "basic")
    includes_special = (code_type == "pro")
    
    # 🛡️ 防御 1：使用密码学安全的 secrets 模块，绝对防推导！
    alphabet = string.ascii_uppercase + string.digits
    secure_random_str = ''.join(secrets.choice(alphabet) for _ in range(8))
    new_code_str = f"SAAS-VIP-{secure_random_str}"
    
    session.add(ActivationCode(code=new_code_str, duration_days=duration_days, includes_special_energy=includes_special))
    await session.commit()
    
    privilege_text = "克隆 + 特价能量" if includes_special else "仅克隆"
    text = (
        "✅ <b>生成成功！</b>\n"
        f"🔑 激活码：<code>{new_code_str}</code>\n"
        f"📦 包含特权：{privilege_text}\n"
        f"⏱️ 有效期：{duration_days} 天\n\n"
        "<i>请将此激活码发送给您的代理客户。</i>"
    )
    await message.answer(text, parse_mode="HTML")
    await state.clear()

# --- 提现与财务管理附属 ---
# ----------------- 🏆 租户(代理)利润排行榜 (带封禁状态显示) -----------------
@admin_router.callback_query(F.data == "admin_tenant_list_profit")
async def list_tenant_profit(call: CallbackQuery, session: AsyncSession):
    stmt = select(Tenant).order_by(Tenant.profit_balance.desc()).limit(15)
    tenants = (await session.scalars(stmt)).all()
    
    if not tenants:
        return await call.message.answer("暂无代理数据。")
        
    msg = "🏆 <b>租户(代理)利润排行榜 (Top 15)</b>\n━━━━━━━━━━━━━━━━━━\n"
    for i, t in enumerate(tenants, 1):
        # 💡 优先判断是否被人工封禁
        if getattr(t, "is_banned", False):
            status = "🚫(封禁)"
        else:
            status = "🟢" if t.is_active else "🔴(冻结)"
            
        msg += f"{i}. <b>系统ID</b>: <code>{t.id}</code> (TG: <code>{t.owner_tg_id}</code>) {status} | 利润: <code>{format_amount(t.profit_balance)}</code> TRX\n"
    
    msg += "━━━━━━━━━━━━━━━━━━\n<i>提示：手工调账或封禁时，输入系统ID或TG ID均可识别。</i>"
    await call.message.answer(msg, parse_mode="HTML")
    await call.answer()
# ----------------- 🏦 租户充值本金排行榜 (带封禁状态显示) -----------------
@admin_router.callback_query(F.data == "admin_fin_tenant_ranks")
async def list_tenant_balance(call: CallbackQuery, session: AsyncSession):
    stmt = select(Tenant).order_by(Tenant.deposit_balance.desc()).limit(15)
    tenants = (await session.scalars(stmt)).all()
    
    if not tenants:
        return await call.message.answer("暂无代理数据。")
        
    msg = "🏦 <b>租户充值本金排行榜 (Top 15)</b>\n━━━━━━━━━━━━━━━━━━\n"
    for i, t in enumerate(tenants, 1):
        # 💡 优先判断是否被人工封禁
        if getattr(t, "is_banned", False):
            status = "🚫(封禁)"
        else:
            status = "🟢" if t.is_active else "🔴(冻结)"
            
        msg += f"{i}. <b>系统ID</b>: <code>{t.id}</code> (TG: <code>{t.owner_tg_id}</code>) {status} | 本金: <code>{format_amount(t.deposit_balance)}</code> TRX\n"
        
    msg += "━━━━━━━━━━━━━━━━━━\n<i>提示：手工调账或封禁时，输入系统ID或TG ID均可识别。</i>"
    await call.message.answer(msg, parse_mode="HTML")
    await call.answer()

# ----------------- ⏳ 待处理提现审核大厅 (前5条独立工单卡片) -----------------
@admin_router.callback_query(F.data == "admin_fin_pending_wd")
async def fetch_pending_withdraw(call: CallbackQuery, session: AsyncSession):
    # 批量查询前 5 条 PENDING 的工单，按时间升序（先申请先处理）
    stmt = select(WithdrawOrder).where(WithdrawOrder.status == 'PENDING').order_by(WithdrawOrder.created_at.asc()).limit(5)
    orders = (await session.scalars(stmt)).all()
    
    if not orders:
        return await call.answer("🎉 太棒了，当前没有任何待处理的提现工单！", show_alert=True)
    
    # 删掉上一级的菜单，使大厅显得更清爽
    try:
        await call.message.delete()
    except Exception:
        pass

    # 遍历生成独立的工单卡片
    for order in orders:
        text = (
            "⏳ <b>待处理提现审核</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🔖 <b>订单 ID</b>：<code>{order.id}</code>\n"
            f"🏦 <b>申请租户</b>：Tenant ID <code>{order.tenant_id}</code>\n"
            f"💰 <b>提现金额</b>：<b>{format_amount(order.amount)} TRX</b>\n"
            f"📥 <b>目标地址</b>：\n<code>{order.target_address}</code>\n"
            f"📅 <b>申请时间</b>：{order.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "请线下转账后点击【确认已打款】；若信息有误请点击【驳回】（资金将退回）。"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ 确认已打款", callback_data=f"admin_wd_action:approve:{order.id}")],
            [InlineKeyboardButton(text="❌ 驳回并退回", callback_data=f"admin_wd_action:reject:{order.id}")]
        ])
        await call.message.answer(text, reply_markup=kb, parse_mode="HTML")

# ----------------- ⚖️ 提现审核与自动平账核心回调 -----------------
@admin_router.callback_query(F.data.startswith("admin_wd_action:"))
async def handle_withdraw_action(call: CallbackQuery, session: AsyncSession):
    # 解析意图与订单 ID
    parts = call.data.split(":")
    action = parts[1]
    order_id = int(parts[2])
    
    # 1. 开启数据库锁定查询该订单
    order = await session.get(WithdrawOrder, order_id)
    if not order:
        return await call.answer("❌ 找不到该提现工单，可能已被物理删除。", show_alert=True)
        
    if order.status != 'PENDING':
        return await call.answer(f"⚠️ 拦截操作：该工单已被处理 (当前状态: {order.status})，请勿重复点击！", show_alert=True)
        
    # 查询对应的租户记录
    tenant = await session.get(Tenant, order.tenant_id)
    if not tenant:
        return await call.answer("❌ 数据异常：找不到申请该提现的代理账户记录。", show_alert=True)
        
    try:
        # ================= 动作 A：确认已打款 =================
        if action == "approve":
            order.status = 'PAID'
            order.handled_at = datetime.utcnow()
            await session.commit()
            
            # 编辑原卡片为已完成状态
            await call.message.edit_text(
                f"✅ <b>工单 #{order.id} 已标记为打款完成。</b>\n"
                f"💸 已提现：<b>{format_amount(order.amount)}</b> TRX", 
                parse_mode="HTML"
            )
            
            # 异步发送 TG 成功通知至租户
            notice = (
                f"🎉 <b>提现到账通知</b>\n\n"
                f"您提现的 <code>{format_amount(order.amount)}</code> TRX 已由财务打款至您的波场地址，请注意查收！"
            )
            try:
                await call.bot.send_message(chat_id=tenant.owner_tg_id, text=notice, parse_mode="HTML")
            except Exception:
                pass  # 防御网络异常导致的回滚

        # ================= 动作 B：驳回并原路退回 =================
        elif action == "reject":
            order.status = 'REJECTED'
            order.handled_at = datetime.utcnow()
            
            # 💡 核心平账机制：将金额以 Decimal 高精度原路退回租户的 profit_balance
            tenant.profit_balance = tenant.profit_balance + order.amount
            await session.commit()
            
            # 编辑原卡片为驳回状态
            await call.message.edit_text(
                f"❌ <b>工单 #{order.id} 已驳回，资金已退回租户余额。</b>\n"
                f"💰 驳回金额：<b>{format_amount(order.amount)}</b> TRX", 
                parse_mode="HTML"
            )
            
            # 异步发送 TG 失败退款通知至租户
            notice = (
                f"⚠️ <b>提现驳回通知</b>\n\n"
                f"您提现的 <code>{format_amount(order.amount)}</code> TRX 申请未通过。\n"
                f"<i>可能原因：提现地址存在风险、余额对账不平或网络异常。</i>\n\n"
                f"资金已全额原路退回至您的获利余额！"
            )
            try:
                await call.bot.send_message(chat_id=tenant.owner_tg_id, text=notice, parse_mode="HTML")
            except Exception:
                pass

        await call.answer()

    except Exception as e:
        await session.rollback()
        await call.answer(f"❌ 数据库执行事务错误: {str(e)}", show_alert=True)
    
# =====================================================================
# ==================== 5. 封禁、调账与底层成本动态调节 ====================
# =====================================================================

# ----------------- 🚫 租户封禁/解封闭环 -----------------
@admin_router.callback_query(F.data == "admin_toggle_ban_tenant")
async def trigger_toggle_ban_tenant(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_ban_tenant_id)
    await call.message.answer("✏️ 请输入需要 <b>封禁/解封</b> 的租户 Telegram ID：", parse_mode="HTML")
    await call.answer()

# ----------------- 🚫 租户封禁/解封步骤 1：输入校验与内存暂存 -----------------
@admin_router.message(AdminConfigFSM.wait_ban_tenant_id)
async def process_ban_tenant_id(message: Message, state: FSMContext, session: AsyncSession):
    try:
        input_val = int(message.text.strip())  # 确保输入是纯数字
    except ValueError:
        return await message.answer("❌ 格式错误！请输入合法的纯数字 ID（系统ID 或 TG ID）：")

    try:
        # 🛡️ 双模查询：支持通过 系统自增ID(id) 或 代理Telegram ID(owner_tg_id) 两种方式精确匹配！
        tenant = await session.scalar(
            select(Tenant).where((Tenant.id == input_val) | (Tenant.owner_tg_id == input_val))
        )
        if not tenant:
            await message.answer("❌ 数据库中未找到该租户，请确认您输入的【系统 ID】或【Telegram ID】是否正确！")
            await state.clear()
            return

        # 💡 【核心重构】：绝不在此直接修改数据。将目标租户的 ID 信息暂存至 FSM 内存/缓存中
        await state.update_data(
            target_tenant_id=tenant.id, 
            target_owner_tg_id=tenant.owner_tg_id
        )

        status_text = "🔴 已封禁限制" if getattr(tenant, "is_banned", False) else "🟢 正常使用中"

        # 呈现排版极其精美的二次确认透视面板
        text = (
            "🎯 <b>已成功识别租户数据</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🔹 <b>系统数据库 ID</b>：<code>{tenant.id}</code>\n"
            f"🔹 <b>Telegram 账号 ID</b>：<code>{tenant.owner_tg_id}</code>\n"
            f"🔹 <b>当前系统状态</b>：<b>{status_text}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>请选择您要执行的风控意图：</b>"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🚫 封禁此租户", callback_data="admin_exec_ban:ban"),
                InlineKeyboardButton(text="✅ 解禁此租户", callback_data="admin_exec_ban:unban")
            ],
            [InlineKeyboardButton(text="🗑️ 彻底删除此租户", callback_data="admin_exec_delete")],
            [InlineKeyboardButton(text="↩️ 取消操作", callback_data="admin_exec_ban:cancel")]
        ])
        
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        await message.answer(f"❌ 系统检索异常：{str(e)}")
        await state.clear()
# ----------------- 💸 手工资金调账 -----------------
@admin_router.callback_query(F.data == "admin_manual_adjust_balance")
async def trigger_adjust_balance(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminConfigFSM.wait_adjust_tg_id)
    await call.message.answer("✏️ 第一步：请输入需要调账的租户 Telegram ID：")
    await call.answer()

# ----------------- 💸 手工资金调账 (双模检索) -----------------
@admin_router.message(AdminConfigFSM.wait_adjust_tg_id)
async def process_adjust_tg_id(message: Message, state: FSMContext, session: AsyncSession):
    try:
        input_val = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ 格式错误！请输入合法的纯数字 ID（系统ID 或 TG ID）：")

    # 🛡️ 【核心修复】：支持通过 系统自增ID(id) 或 代理Telegram ID(owner_tg_id) 两种方式精确匹配！
    tenant = await session.scalar(
        select(Tenant).where((Tenant.id == input_val) | (Tenant.owner_tg_id == input_val))
    )
    if not tenant:
        await message.answer("❌ 数据库中未找到该租户，请确认您输入的【系统 ID】或【Telegram ID】是否正确！")
        await state.clear()
        return

    # 将真实的 owner_tg_id 存入 FSM，方便后续精准发送 TG 变动提醒
    await state.update_data(adjust_tg_id=tenant.owner_tg_id)
    await state.set_state(AdminConfigFSM.wait_adjust_type)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 增加本金余额", callback_data="admin_adj_type:add"),
         InlineKeyboardButton(text="➖ 扣除本金余额", callback_data="admin_adj_type:sub")]
    ])
    await message.answer(
        f"⚙️ 租户 <code>{tenant.owner_tg_id}</code> (系统ID: {tenant.id}) 当前本金：<b>{format_amount(tenant.deposit_balance)} TRX</b>\n"
        f"请选择调账类型：", 
        reply_markup=kb, 
        parse_mode="HTML"
    )

@admin_router.callback_query(F.data.startswith("admin_adj_type:"))
async def process_adjust_type(call: CallbackQuery, state: FSMContext):
    adj_type = call.data.split(":")[1]
    await state.update_data(adjust_type=adj_type)
    await state.set_state(AdminConfigFSM.wait_adjust_amount)
    
    type_text = "增加" if adj_type == "add" else "扣除"
    await call.message.edit_text(f"✏️ 请输入要<b>{type_text}</b>的本金金额 (TRX)：", parse_mode="HTML")
    await call.answer()

@admin_router.message(AdminConfigFSM.wait_adjust_amount)
async def process_adjust_amount(message: Message, state: FSMContext, session: AsyncSession):
    try:
        amount = Decimal(message.text.strip())
        if amount <= 0:
            raise ValueError
    except Exception:
        return await message.answer("❌ 金额格式错误！请输入大于 0 的有效数字：")

    data = await state.get_data()
    tg_id = data.get("adjust_tg_id")
    adj_type = data.get("adjust_type")

    try:
        # 使用 deposit_balance 代表代理老板在主控平台的预存本金余额
        if adj_type == "add":
            stmt = update(Tenant).where(Tenant.owner_tg_id == tg_id).values(deposit_balance=Tenant.deposit_balance + amount)
            change_text = f"+{format_amount(amount)}"
        else:
            # 防透支：必须 deposit_balance >= amount
            stmt = update(Tenant).where(Tenant.owner_tg_id == tg_id, Tenant.deposit_balance >= amount).values(deposit_balance=Tenant.deposit_balance - amount)
            change_text = f"-{format_amount(amount)}"

        result = await session.execute(stmt)
        if result.rowcount == 0:
            await message.answer("❌ 调账失败：租户不存在或本金余额不足以完成扣款！")
            await state.clear()
            return

        await session.commit()
        await state.clear()
        
        # 【精准通知】：推送账目变更给租户老板
        try:
            notification = f"🔔 <b>后台账目变动通知</b>\n\n系统管理员对您的账户进行了手工调账。\n🔹 资金变动：<code>{change_text}</code> TRX\n🔹 请知悉！"
            await message.bot.send_message(chat_id=tg_id, text=notification, parse_mode="HTML")
            notice_status = "且租户已成功收到变动提醒"
        except Exception as tg_err:
            notice_status = f"但由于其未启动母平台机器人导致通知未直接送达"

        await message.answer(f"✅ <b>手工调账成功！</b>\n\n租户: <code>{tg_id}</code>\n变动: <b>{change_text} TRX</b>\n{notice_status}。", parse_mode="HTML")
        await admin_menu_finance(message, session, state)

    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 数据库操作异常：{str(e)}")
        await state.clear()
# ----------------- 🔄 一键手动同步 Netts 底价 (Callback 拦截) -----------------
@admin_router.callback_query(F.data == "admin_sync_netts_price")
async def manual_sync_netts_price(call: CallbackQuery, session: AsyncSession, state: FSMContext):
    from tasks import fetch_netts_prices
    
    try:
        prices = await fetch_netts_prices()
        
        if prices is not None:
            netts_65k, netts_131k = prices
            
            # 手动对账落库到 netts_cost
            await session.execute(
                update(SystemConfig)
                .where(SystemConfig.id == 1)
                .values(
                    netts_cost_65k=netts_65k, 
                    netts_cost_131k=netts_131k
                )
            )
            await session.commit()
            
            # 获取当前超管配置的抽水
            config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
            draw_65k = config.base_cost_65k or Decimal('0.00')
            draw_131k = config.base_cost_131k or Decimal('0.00')
            
            await call.answer(
                f"✅ 手动同步进货价成功(只进不舍)！\n\n"
                f"🔋 65K 能量财务链路：\n"
                f"├─ 进货成本: {netts_65k:.2f} TRX\n"
                f"├─ 平台抽水: {draw_65k:.2f} TRX\n"
                f"└─ 代理拿货价: {float(netts_65k + draw_65k):.2f} TRX\n\n"
                f"🔋 131K 能量财务链路：\n"
                f"├─ 进货成本: {netts_131k:.2f} TRX\n"
                f"├─ 平台抽水: {draw_131k:.2f} TRX\n"
                f"└─ 代理拿货价: {float(netts_131k + draw_131k):.2f} TRX", 
                show_alert=True
            )
            # 原地无缝渲染面板
            await admin_menu_pricing(call, session, state)
        else:
            await call.answer("⚠️ 同步失败：请求上游数据异常，请查看日志。", show_alert=True)
            
    except Exception as e:
        await session.rollback()
        await call.answer(f"❌ 同步异常：{str(e)}", show_alert=True)
        
# ----------------- 🚫 租户封禁/解封步骤 2：提取内存并原子流转 -----------------
@admin_router.callback_query(F.data.startswith("admin_exec_ban:"))
async def process_ban_confirmation(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    # 解析意图 (ban, unban, cancel)
    action = call.data.split(":")[1]
    
    # 1. ⚡ 从 FSM 缓存/内存中提取先前暂存的租户物理数据
    state_data = await state.get_data()
    tenant_id = state_data.get("target_tenant_id")
    owner_tg_id = state_data.get("target_owner_tg_id")
    
    # 防呆校验：如果 FSM 数据因为服务器重启或超长等待导致 Session/State 丢失
    if not tenant_id or not owner_tg_id:
        await state.clear()
        return await call.answer("⚠️ 缓存已超时失效，请重新在主菜单发起封禁流！", show_alert=True)
        
    # 取消逻辑
    if action == "cancel":
        await state.clear()
        await call.message.edit_text("↩️ <b>操作已取消</b>\n未对租户状态进行任何修改。", parse_mode="HTML")
        return await call.answer()
        
    try:
        # 2. 从数据库提取最新的租户 ORM 实体
        tenant = await session.get(Tenant, tenant_id)
        if not tenant:
            await state.clear()
            return await call.answer("❌ 提单失败：该租户数据可能已被其他进程物理删除！", show_alert=True)
            
        # 3. 按照意图进行状态流转
        if action == "ban":
            if getattr(tenant, "is_banned", False):
                await call.answer("⚠️ 该租户此前已处于封禁状态，无需重复封禁！", show_alert=True)
                await call.message.edit_text(f"⚠️ 租户 <code>{owner_tg_id}</code> 此前已是封禁状态，操作未变更。", parse_mode="HTML")
                await state.clear()
                return
                
            tenant.is_banned = True
            msg_result = f"❌ <b>强行封禁成功！</b>\n\n租户 <code>{owner_tg_id}</code> (系统ID: {tenant_id}) 已被管理员强行封禁，其所有子机器人将停止响应！"
            
        elif action == "unban":
            if not getattr(tenant, "is_banned", False):
                await call.answer("⚠️ 该租户当前处于正常状态，无需重复解封！", show_alert=True)
                await call.message.edit_text(f"⚠️ 租户 <code>{owner_tg_id}</code> 当前状态正常，无需重复解封。", parse_mode="HTML")
                await state.clear()
                return
                
            tenant.is_banned = False
            msg_result = f"🟢 <b>解除封禁成功！</b>\n\n租户 <code>{owner_tg_id}</code> (系统ID: {tenant_id}) 限制已解除，服务已恢复正常使用。"

        # 4. 强制提交并彻底清空 FSM 状态
        await session.commit()
        await state.clear()
        
        # 5. 【防止连击核心】：更新原消息，移除内联键盘 (防止超管回滚或重复点击导致冲突)
        await call.message.edit_text(msg_result, parse_mode="HTML")
        await call.answer("风控意图执行完毕")

    except Exception as e:
        await session.rollback()
        await call.answer(f"❌ 事务提交异常，已安全回滚：{str(e)}", show_alert=True)
        await state.clear()
        
# =====================================================================
# 新增独立逻辑：彻底删除租户与清理机器人的回调拦截
# =====================================================================
@admin_router.callback_query(F.data == "admin_exec_delete")
async def process_delete_tenant(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    # 1. ⚡ 从 FSM 缓存中提取暂存的租户物理数据
    state_data = await state.get_data()
    tenant_id = state_data.get("target_tenant_id")
    owner_tg_id = state_data.get("target_owner_tg_id")
    
    # 防呆校验
    if not tenant_id or not owner_tg_id:
        await state.clear()
        return await call.answer("⚠️ 缓存已超时失效，请重新在主菜单发起操作！", show_alert=True)
        
    try:
        # 2. 从数据库提取最新的租户 ORM 实体
        tenant = await session.get(Tenant, tenant_id)
        if not tenant:
            await state.clear()
            return await call.answer("❌ 删除失败：该租户数据已被清理！", show_alert=True)
            
        # 3. 卸载正在运行的机器人（局部引入 bot_manager 避免顶部循环引用）
        from bot_manager import bot_manager
        await bot_manager.unmount_bot(tenant_id)
        
        # 4. 彻底清理数据库（SQLAlchemy 关联了 ON DELETE CASCADE 会自动清理该租户名下的 user 表等流水数据）
        await session.delete(tenant)
        await session.commit()
        await state.clear()
        
        # 5. 更新原消息卡片，移除内联键盘
        msg_result = f"✅ <b>删除成功！</b>\n\n租户 <code>{owner_tg_id}</code> (系统ID: {tenant_id}) 的数据已彻底清除，绑定的机器人已安全下线。"
        await call.message.edit_text(msg_result, parse_mode="HTML")
        await call.answer("租户数据与机器人销毁完毕")

    except Exception as e:
        await session.rollback()
        await call.answer(f"❌ 删除异常，已安全回滚：{str(e)}", show_alert=True)
        await state.clear()
        
