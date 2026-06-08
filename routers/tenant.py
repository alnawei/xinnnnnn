# routers/tenant.py
import datetime
import random
import json
from decimal import Decimal
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter  # 👈 新增 StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select
from aiogram.exceptions import TelegramBadRequest
from filters.role import RoleFilter
from keyboards.reply import build_tenant_keyboard
from bot_manager import bot_manager
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timedelta
from models import Tenant, WithdrawOrder, SystemConfig, ActivationCode, EnergyOrder, SaaSOrder, MicroDepositOrder, User


# ==================== 0. 工具函数 ====================
def format_amount(amount):
    """消除多余的零：1.500000 -> 1.5, 1.000000 -> 1"""
    if amount is None:
        return "0"
    s = f"{float(amount):.2f}"
    return s.rstrip('0').rstrip('.') if '.' in s else s


# ==================== 1. 租户管理路由 (给老租户用) ====================
tenant_router = Router(name="tenant_router")
tenant_router.message.filter(RoleFilter(["tenant"]))
tenant_router.callback_query.filter(RoleFilter(["tenant"]))

@tenant_router.message(StateFilter('*'), Command("start"))
async def tenant_start(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    await state.clear()
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    is_special_enabled = getattr(sys_config, "is_special_energy_global_enabled", True)
    await message.answer(
        "🏠 欢迎来到管理中心：", 
        reply_markup=build_tenant_keyboard(is_special_enabled)
    )

class TenantWithdrawFSM(StatesGroup):
    waiting_for_amount = State()
# 👇 新增：代理本金充值状态机
class TenantDepositFSM(StatesGroup):
    waiting_for_amount = State()

class TenantSpecialFSM(StatesGroup):
    wait_special_address = State()
    wait_markup_special = State()
    wait_special_address = State()
    wait_special_price_65k = State()
    wait_special_price_131k = State()
@tenant_router.message(StateFilter('*'), F.text == "🏠 个人中心")
async def tenant_dashboard(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    await state.clear() # 强制清理残留状态
    
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    is_special_enabled = getattr(sys_config, "is_special_energy_global_enabled", True)
    total_balance = current_tenant.deposit_balance + current_tenant.profit_balance
    
    text = (
        f"🏠 <b>代理控制台首页</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>系统 ID</b>: <code>{current_tenant.id}</code>\n"
        f"🤖 <b>机器人状态</b>: {'🟢 运行中' if current_tenant.is_active else '🔴 已冻结'}\n"
        f"⏱ <b>授权到期时间</b>: {current_tenant.expire_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )
    if is_special_enabled and current_tenant.has_special_energy_right:
        text += f"💰 <b>账户余额</b>：{format_amount(total_balance)} TRX（包含充值余额：{format_amount(current_tenant.deposit_balance)} | 获利余额：{format_amount(current_tenant.profit_balance)}）\n"
    else:
        text += f"💰 <b>账户余额</b>：{format_amount(total_balance)} TRX\n"
        
    text += "━━━━━━━━━━━━━━━━━━\n📝 <i>提示：如需提现获利余额，请先在【机器设置】中绑定提现地址，然后点击下方按钮。</i>"
    
    # 🌟 使用 Builder 动态构建个人主页的悬浮菜单
    kb_builder = InlineKeyboardBuilder()
    
    # 1. 常驻按钮：提现利润（任何状态下都显示）
    kb_builder.row(InlineKeyboardButton(text="💸 申请提现 (仅限获利部分)", callback_data="tenant_withdraw_apply"))
    
    # 2. 动态追加：必须是拥有特价能量权限的租户，才渲染充值本金入口
    if is_special_enabled and current_tenant.has_special_energy_right:
        kb_builder.row(InlineKeyboardButton(text="💎 充值本金 (TRX直充)", callback_data="tenant_deposit_trx"))
    
    await message.answer(text, reply_markup=kb_builder.as_markup(), parse_mode="HTML")
# =========================================================
# 代理/租户本金充值流程 (仅限拥有特权的代理可用)
# =========================================================
@tenant_router.callback_query(F.data == "tenant_deposit_trx")
async def trigger_tenant_deposit(call: CallbackQuery, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    await call.answer()  # 消除转圈
    
    # 防越权校验
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    is_special_enabled = getattr(sys_config, "is_special_energy_global_enabled", True)
    
    if not (is_special_enabled and current_tenant.has_special_energy_right):
        return await call.message.answer("⚠️ 您当前未开通特价能量功能，无需充值本金。")

    # 第一步：引导输入金额并进入 FSM 等待状态
    text = (
        "💎 <b>代理本金充值通道 (TRX 直充)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✏️ <b>请输入您需要充值的本金金额（必须为整数，建议 50 以上）：</b>\n\n"
        "<i>系统将为您自动生成专属的尾数防伪订单，发送 /cancel 可取消。</i>"
    )
    
    try:
        await call.message.edit_text(text, parse_mode="HTML")
        await state.set_state(TenantDepositFSM.waiting_for_amount)
    except TelegramBadRequest:
        pass

@tenant_router.message(TenantDepositFSM.waiting_for_amount)
async def process_tenant_deposit_amount(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    """第二步：处理输入的金额，生成尾数单并弹出标准收银台"""
    if message.text.strip().lower() == '/cancel':
        await state.clear()
        return await message.answer("✅ 已取消本金充值操作。")

    if not message.text.isdigit():
        return await message.answer("❌ 格式错误！请输入有效的【纯数字整数】金额：")

    base_amount = int(message.text.strip())
    if base_amount < 10:
        return await message.answer("❌ 充值金额不可低于 10 TRX，请重新输入：")

    await state.clear()
    wait_msg = await message.answer("🔄 正在为您生成高精度安全防伪订单，请稍候...")

    try:
        # 获取系统配置与主收款地址
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        master_wallet = getattr(sys_config, "master_receive_address", "未配置")

        # 核心算法：尾数防撞库生成
        now_time = datetime.utcnow()
        expire_time = now_time + timedelta(minutes=10)
        
        final_amount = None
        is_three_digits = False

        # === 阶段一：2 位小数尾数排重 (.01 ~ .99) ===
        for _ in range(40):
            tail_int = random.randint(1, 99)
            tail_str = f"0.{tail_int:02d}"
            test_amount = Decimal(str(base_amount)) + Decimal(tail_str)
            
            stmt = select(MicroDepositOrder).where(
                MicroDepositOrder.expected_amount == test_amount,
                MicroDepositOrder.status == 'PENDING',
                MicroDepositOrder.expired_at > now_time
            )
            if not (await session.execute(stmt)).scalar_one_or_none():
                final_amount = test_amount
                break

        # === 阶段二：自动降级 3 位小数尾数 (.001 ~ .999) ===
        if not final_amount:
            is_three_digits = True
            for _ in range(50):
                tail_int = random.randint(1, 999)
                if tail_int % 10 == 0: 
                    continue
                tail_str = f"0.{tail_int:03d}"
                test_amount = Decimal(str(base_amount)) + Decimal(tail_str)
                
                stmt = select(MicroDepositOrder).where(
                    MicroDepositOrder.expected_amount == test_amount,
                    MicroDepositOrder.status == 'PENDING',
                    MicroDepositOrder.expired_at > now_time
                )
                if not (await session.execute(stmt)).scalar_one_or_none():
                    final_amount = test_amount
                    break

        if not final_amount:
            return await wait_msg.edit_text("❌ 当前充值通道极其繁忙，请稍后或更换金额重试。")

        # 查询代理老板自己的散客 User ID（维持外键关联一致性）
        user_record = await session.scalar(
            select(User).where(User.tenant_id == current_tenant.id, User.tg_user_id == message.from_user.id)
        )
        user_id_val = user_record.id if user_record else 0
        fractional_val = final_amount - Decimal(str(base_amount))

        # 将订单写入数据库锁定尾数
        new_order = MicroDepositOrder(
            tenant_id=current_tenant.id,
            user_id=user_id_val,
            base_amount=base_amount,
            fractional_amount=fractional_val,
            expected_amount=final_amount,
            status='PENDING',
            expired_at=expire_time
        )
        session.add(new_order)
        await session.commit()

        # 第三步：渲染标准收银台卡片
        amount_display = f"{final_amount:.2f}" if not is_three_digits else f"{final_amount:.3f}"
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
            f"<code>{master_wallet}</code> 👈 <i>(点击地址自动复制)</i>\n\n"
            "⏱ <i>本订单有效期仅 <b>10 分钟</b>，请抓紧时间支付。转账后 1-3 分钟内智能合约自动为您核销并增加进货本金。</i>"
        )
        
        # 附带返回个人中心按钮
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 返回个人中心", callback_data="back_to_tenant_home")]
        ])
        
        await wait_msg.edit_text(invoice_text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        await session.rollback()
        await wait_msg.edit_text(f"❌ 专属通道锁定失败，入库异常：{str(e)}")

# =========================================================
# 辅助逻辑：配合上述面板的返回动作
# =========================================================
@tenant_router.callback_query(F.data == "back_to_tenant_home")
async def back_to_tenant_home_handler(call: CallbackQuery, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    await call.answer()
    # 直接复用前面写好的 tenant_dashboard 处理函数，刷新出个人中心数据
    await tenant_dashboard(call.message, current_tenant, session, state)
# =========================================================
# 🐛 [Bug 1 修复]：所有 CallbackQuery 必须先 answer() 并用 try/except 包裹 edit_text
# =========================================================

@tenant_router.callback_query(F.data == "tenant_withdraw_apply")
async def trigger_withdraw(call: CallbackQuery, current_tenant: Tenant, state: FSMContext, session: AsyncSession):
    await call.answer() # 必须先响应防止转圈
    
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    min_withdraw = sys_config.min_withdraw_amount
    
    if not current_tenant.withdraw_address:
        return await call.message.answer("⚠️ 您尚未配置提现地址，请先在【机器人设置】中绑定！")
        
    if current_tenant.profit_balance < min_withdraw:
        return await call.message.answer(f"⚠️ 您的获利余额不足最低提现门槛（{format_amount(min_withdraw)} TRX）！")
        
    await state.set_state(TenantWithdrawFSM.waiting_for_amount)
    text = (
        f"💸 <b>申请利润提现</b>\n\n"
        f"可提现余额：<b>{format_amount(current_tenant.profit_balance)}</b> TRX\n"
        f"提现目标地址：<code>{current_tenant.withdraw_address}</code>\n"
        f"最低门槛：{format_amount(min_withdraw)} TRX\n\n"
        f"✏️ 请输入您要提现的金额：\n<i>发送 /cancel 取消操作</i>"
    )
    
    try:
        await call.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass


@tenant_router.message(TenantWithdrawFSM.waiting_for_amount)
async def process_withdraw_amount(message: Message, state: FSMContext, current_tenant: Tenant, session: AsyncSession):
    # 🛡️ 防呆 1：图片/贴纸拦截
    if not message.text:
        return await message.answer("❌ <b>格式错误！</b>\n请输入纯数字文本，或发送 /cancel 退出。", parse_mode="HTML")

    if message.text.strip().lower() == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    # 🛡️ 防呆 2：防 SQL Decimal 溢出攻击与恶意负数攻击
    if len(message.text.strip()) > 12:
        return await message.answer("❌ 输入的金额过大，请重新输入：")
        
    try:
        amount = Decimal(message.text.strip())
        if amount <= 0: raise ValueError
    except Exception:
        return await message.answer("❌ 金额格式错误，请输入大于 0 的有效数字：")
        
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    min_withdraw = Decimal(str(sys_config.min_withdraw_amount or 100.0))
    
    if amount < min_withdraw:
        return await message.answer(f"❌ 提现金额不可低于 {format_amount(min_withdraw)} TRX，请重新输入：")
        
    if amount > current_tenant.profit_balance:
        return await message.answer(f"❌ 您的可提现获利余额不足（仅有 {format_amount(current_tenant.profit_balance)} TRX），请重新输入：")
        
    try:
        # a. 开启强一致性事务扣减余额 (采用 Update 原子锁，绝对防止高并发将利润扣成负数)
        stmt = (
            update(Tenant)
            .where(Tenant.id == current_tenant.id, Tenant.profit_balance >= amount)
            .values(profit_balance=Tenant.profit_balance - amount)
        )
        result = await session.execute(stmt)
        
        if result.rowcount == 0:
            await state.clear()
            return await message.answer("❌ 提现失败：系统繁忙或您的余额已被变动，请稍后重试！")
            
        # b. 创建提现工单记录至 withdraw_orders 表
        withdraw_order = WithdrawOrder(
            tenant_id=current_tenant.id,
            amount=amount,
            target_address=current_tenant.withdraw_address,
            status='PENDING'
        )
        session.add(withdraw_order)
        await session.commit()
        
        await state.clear()
        
        # c. 提示租户
        await message.answer(
            f"✅ <b>提现工单已成功提交！</b>\n\n"
            f"💸 申请金额：<b>{format_amount(amount)}</b> TRX\n"
            f"📥 收款地址：<code>{current_tenant.withdraw_address}</code>\n"
            f"⏳ 当前状态：<code>待系统审核打款</code>\n\n"
            f"<i>财务人员将在 24 小时内完成链上核对与人工打款，请耐心等待。</i>",
            parse_mode="HTML"
        )
        
        # d. 💡 向超管预警
        admin_tg_id = getattr(sys_config, "super_admin_tg_id", None)
        if admin_tg_id:
            alert_msg = (
                f"🚨 <b>【新的代理利润提现申请预警】</b>\n\n"
                f"🏢 <b>租户 ID</b>：#{current_tenant.id}\n"
                f"💰 <b>提现金额</b>：<code>{format_amount(amount)}</code> TRX\n"
                f"🔗 <b>收款地址</b>：<code>{current_tenant.withdraw_address}</code>\n\n"
                f"⚠️ 请大老板核对财务后，手动向该地址转账，并在超管面板标记为已打款！"
            )
            try:
                await message.bot.send_message(chat_id=admin_tg_id, text=alert_msg, parse_mode="HTML")
            except Exception:
                pass

    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 提现系统异常，资金已安全回滚：{str(e)}")

# ----------------- 【第二阶段】机器人设置、加价设置与提现地址 -----------------

class TenantSettingsFSM(StatesGroup):
    wait_withdraw_address = State()
    wait_bot_token = State()
    wait_markup_65k = State()
    wait_markup_131k = State()

# 1. 拦截底部键盘的【⚙️机器设置】 (注意：文字必须和底部物理键盘严格对应)
@tenant_router.message(StateFilter('*'), F.text == "⚙️ 机器设置")
async def tenant_bot_settings(message: Message, current_tenant: Tenant, state: FSMContext):
    await state.clear()
    
    masked_token = f"{current_tenant.bot_token[:10]}...{current_tenant.bot_token[-5:]}" if current_tenant.bot_token else "未设置"
    withdraw_addr = current_tenant.withdraw_address if current_tenant.withdraw_address else "未设置"
    
    text = (
        "⚙️ <b>子机器人参数与核心设置</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>当前挂载 Token</b>：\n<code>{masked_token}</code>\n"
        f"📥 <b>当前利润提现地址</b>：\n<code>{withdraw_addr}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 请选择您要配置的项目："
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 设置/修改提现地址", callback_data="tenant_set_withdraw_addr")],
        [InlineKeyboardButton(text="🔄 更换专属机器人 Token", callback_data="tenant_change_bot_token")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# 2. 拦截修改提现地址的回调动作，进入 FSM 等待状态
@tenant_router.callback_query(F.data == "tenant_set_withdraw_addr")
async def trigger_set_withdraw_addr(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TenantSettingsFSM.wait_withdraw_address)
    
    text = (
        "✏️ 请输入您的波场 TRX 提现地址\n"
        "⚠️ <i>必须是以大写 T 开头的 34 位有效波场地址！</i>\n"
        "<i>发送 /cancel 取消操作</i>"
    )
    try:
        await call.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass


# 3. 🌟【核心修复】拦截用户的地址输入，处理数据库逻辑与容错
@tenant_router.message(TenantSettingsFSM.wait_withdraw_address)
async def process_set_withdraw_addr(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    # 防呆 1：如果用户发来的不是纯文本（比如发了图片/贴纸），阻止报错
    if not message.text:
        return await message.answer("❌ 请输入有效的文本格式地址！")

    if message.text.strip() == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    addr = message.text.strip()
    
    # 防呆 2：基础格式校验
    if not addr.startswith("T") or len(addr) != 34:
        return await message.answer("❌ 地址格式不正确！必须是以大写 T 开头的 34 位波场地址。请重新输入：")
        
    # 防呆 3：严密的数据库容错，防止静默崩溃石沉大海
    try:
        await session.execute(
            update(Tenant).where(Tenant.id == current_tenant.id).values(withdraw_address=addr)
        )
        await session.commit()
        
        # 只有在数据库 commit 成功后，才清除状态机并反馈
        await state.clear()
        await message.answer(
            f"✅ <b>提现地址设置成功！</b>\n\n您当前的提现收款地址已更新为：\n<code>{addr}</code>", 
            parse_mode="HTML"
        )
        
    except Exception as e:
        # 捕捉异常并回滚，给用户明确的红字警告
        await session.rollback()
        await message.answer(f"❌ 设置失败，发生数据库系统异常，请联系母平台超管！\n错误日志：{str(e)}")
        await state.clear()


@tenant_router.callback_query(F.data == "tenant_change_bot_token")
async def trigger_change_bot_token(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TenantSettingsFSM.wait_bot_token)
    text = "✏️ 请前往 @BotFather 获取新的 <code>HTTP API Token</code> 并发送给我：\n⚠️ <i>更换 Token 后系统会自动重启子机器人，请确保输入无误。</i>\n<i>发送 /cancel 取消操作</i>"
    try:
        await call.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass

@tenant_router.message(TenantSettingsFSM.wait_bot_token)
async def process_change_bot_token(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    new_token = message.text.strip()
    if ":" not in new_token:
        return await message.answer("❌ Token 格式不正确，请重新发送：")
        
    # 检查Token是否被其他租户占用
    existing = await session.scalar(select(Tenant).where(Tenant.bot_token == new_token, Tenant.id != current_tenant.id))
    if existing:
        return await message.answer("❌ 该 Token 已被其他机器人使用，请重新获取全新的 Token！")

    try:
        # 1. 更新数据库
        await session.execute(update(Tenant).where(Tenant.id == current_tenant.id).values(bot_token=new_token))
        await session.commit()
        
        # 2. 动态卸载并重新挂载机器人 (无缝热更)
        await message.answer("⏳ 正在验证并重新挂载新机器人，请稍候...")
        await bot_manager.unmount_bot(current_tenant.id)
        is_success = await bot_manager.mount_bot(current_tenant.id, new_token)
        
        if is_success:
            await state.clear()
            await message.answer("🎉 <b>Token 更换成功！</b>您的新机器人现已全面上线接客！", parse_mode="HTML")
        else:
            await state.clear()
            await message.answer("⚠️ <b>Token 已更新入库，但验证失败未能启动！</b>\n请检查该 Token 是否正确或是否被其他程序占用，随时可重新修改。", parse_mode="HTML")
            
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 发生异常，更换失败：{str(e)}")


@tenant_router.message(StateFilter('*'), F.text == "💰 加价设置")
async def tenant_markup_settings(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    await state.clear()
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    
    netts_65k = Decimal(str(getattr(sys_config, 'netts_cost_65k', 0.0) or 0.0))
    netts_131k = Decimal(str(getattr(sys_config, 'netts_cost_131k', 0.0) or 0.0))
    draw_65k = Decimal(str(sys_config.base_cost_65k or 0.0))
    draw_131k = Decimal(str(sys_config.base_cost_131k or 0.0))
    
    agent_cost_65k = netts_65k + draw_65k
    agent_cost_131k = netts_131k + draw_131k
    markup_65k = Decimal(str(current_tenant.markup_65k or 0.0))
    markup_131k = Decimal(str(current_tenant.markup_131k or 0.0))
    user_price_65k = agent_cost_65k + markup_65k
    user_price_131k = agent_cost_131k + markup_131k
    
    text = (
        "📈 <b>销售价格与加价设置中心</b>\n━━━━━━━━━━━━━━━━━━\n"
        "🔋 <b>[65K 能量]</b>\n"
        f"├─ 平台给您的拿货价: <code>{format_amount(agent_cost_65k)}</code> TRX\n"
        f"├─ 您设定的加价利润: <code>{format_amount(markup_65k)}</code> TRX\n"
        f"└─ <b>最终展示给用户的售价: <code>{format_amount(user_price_65k)}</code> TRX</b>\n\n"
        "🔋 <b>[131K 能量]</b>\n"
        f"├─ 平台拿货价: <code>{format_amount(agent_cost_131k)}</code> TRX\n"
        f"├─ 您设定的利润: <code>{format_amount(markup_131k)}</code> TRX\n"
        f"└─ <b>最终展示售价: <code>{format_amount(user_price_131k)}</code> TRX</b>\n"
        "━━━━━━━━━━━━━━━━━━\n👇 请选择您要调整的利润部分："
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ 修改 65K 能量加价", callback_data="tenant_edit_markup_65k")],
        [InlineKeyboardButton(text="✏️ 修改 131K 能量加价", callback_data="tenant_edit_markup_131k")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@tenant_router.callback_query(F.data == "tenant_edit_markup_65k")
async def trigger_markup_65k(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TenantSettingsFSM.wait_markup_65k)
    text = "✏️ 请输入您想在 <b>65K 能量</b> 基础之上赚取的纯利润 (TRX) \n例如: 0.5\n<i>发送 /cancel 取消操作</i>"
    try:
        await call.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass

@tenant_router.message(TenantSettingsFSM.wait_markup_65k)
async def process_markup_65k(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    try:
        new_markup = Decimal(message.text.strip())
        if new_markup < 0: raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误，请输入大于或等于 0 的有效数字：")
        
    await session.execute(update(Tenant).where(Tenant.id == current_tenant.id).values(markup_65k=new_markup))
    await session.commit()
    await state.clear()
    await message.answer(f"✅ <b>65K 加价修改成功！</b>\n当前 65K 纯利润设定为: <b>{format_amount(new_markup)} TRX</b>", parse_mode="HTML")

@tenant_router.callback_query(F.data == "tenant_edit_markup_131k")
async def trigger_markup_131k(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TenantSettingsFSM.wait_markup_131k)
    text = "✏️ 请输入您想在 <b>131K 能量</b> 基础之上赚取的纯利润 (TRX) \n例如: 1.0\n<i>发送 /cancel 取消操作</i>"
    try:
        await call.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass

@tenant_router.message(TenantSettingsFSM.wait_markup_131k)
async def process_markup_131k(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    try:
        new_markup = Decimal(message.text.strip())
        if new_markup < 0: raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误，请输入大于或等于 0 的有效数字：")
        
    await session.execute(update(Tenant).where(Tenant.id == current_tenant.id).values(markup_131k=new_markup))
    await session.commit()
    await state.clear()
    await message.answer(f"✅ <b>131K 加价修改成功！</b>\n当前 131K 纯利润设定为: <b>{format_amount(new_markup)} TRX</b>", parse_mode="HTML")


# ==================== 3. 租户激活专用路由 (给新客自助挂载用) ====================
# 【架构重点】：这个路由允许 guest(未购买) 或 user(普通散客) 访问，用于身份升级！
tenant_activation_router = Router(name="tenant_activation_router")
tenant_activation_router.message.filter(RoleFilter(["guest", "user"]))

class TenantActivationStates(StatesGroup):
    wait_code = State()
    wait_token = State()

@tenant_activation_router.message(Command("activate"))
async def start_activation(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🚀 <b>欢迎激活 SaaS 代理机器人</b>\n\n🔑 请输入您购买的激活码（例如 SAAS-VIP-XXXX）：", parse_mode="HTML")
    await state.set_state(TenantActivationStates.wait_code)

@tenant_activation_router.message(TenantActivationStates.wait_code)
async def process_activation_code(message: Message, state: FSMContext, session: AsyncSession):
    code_input = message.text.strip()
    
    stmt = select(ActivationCode).where(ActivationCode.code == code_input, ActivationCode.is_used == False)
    db_code = await session.scalar(stmt)
    
    if not db_code:
        return await message.answer("❌ 无效的激活码，或该激活码已被使用！请重新输入，或发 /cancel 取消。")
    
    await state.update_data(valid_code=code_input)
    await state.set_state(TenantActivationStates.wait_token)
    
    await message.answer(
        "✅ <b>激活码校验成功！</b>\n\n"
        "🤖 下一步：请前往 @BotFather 创建一个新机器人，并将获取到的 <code>HTTP API Token</code> 发送给我：",
        parse_mode="HTML"
    )

@tenant_activation_router.message(TenantActivationStates.wait_token)
async def process_bot_token(message: Message, state: FSMContext, session: AsyncSession):
    token = message.text.strip()
    if ":" not in token:
        return await message.answer("❌ Token 格式不正确，请重新发送！")
    
    data = await state.get_data()
    valid_code = data.get("valid_code")
    user_id = message.from_user.id
    
    existing_tenant = await session.scalar(select(Tenant).where(Tenant.owner_tg_id == user_id))
    if existing_tenant:
        return await message.answer("⚠️ 您当前已经是代理，无需重复激活！", reply_markup=build_tenant_keyboard(True))

    try:
        # 👇 【修改处 1】：删除了多余的 "datetime." 前缀，直接使用 datetime.utcnow() 和 timedelta()
        expire_date = datetime.utcnow() + timedelta(days=30)
        
        new_tenant = Tenant(
            owner_tg_id=user_id,
            bot_token=token,
            is_active=True,
            expire_time=expire_date
        )
        session.add(new_tenant)
        await session.flush()
        tenant_id = new_tenant.id
        
        stmt = update(ActivationCode).where(ActivationCode.code == valid_code).values(
            is_used=True,
            used_by_tg_id=str(user_id),
            # 👇 【修改处 2】：删除了多余的 "datetime." 前缀
            used_at=datetime.utcnow()
        )
        await session.execute(stmt)
        await session.commit()
        
        is_success = await bot_manager.mount_bot(tenant_id, token)
        
        if is_success:
            await state.clear()
            await message.answer(
                "🎉 <b>恭喜！您的代理专属机器人已激活并启动！</b>\n\n"
                "您现在已获得代理权限，请点击下方 /start 刷新菜单，进入【个人中心】进行加价配置！",
                parse_mode="HTML"
            )
        else:
            await message.answer("⚠️ 账号已激活，但您的 Bot Token 验证失败，机器人未能启动。\n请确认 Token 是否正确，稍后在后台【机器人设置】中修改。")
            await state.clear()
            
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 激活失败，系统发生错误：{str(e)}")
        
# =====================================================================
# ✨ 新增模块 A：🛒 续费机器 (生成专业账单与去重偏移)
# =====================================================================
@tenant_router.message(StateFilter('*'), F.text == "🛒 续费机器")
async def tenant_renew_bot_handler(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    await state.clear()
    
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    clone_fee_str = getattr(sys_config, "clone_fee_config", "{}")
    
    try:
        clone_data = json.loads(clone_fee_str)
    except Exception:
        clone_data = {"_is_open": True}

    is_open = clone_data.get("_is_open", True)
    if not is_open:
        return await message.answer("⚠️ <b>抱歉，目前系统暂停机器续费服务。</b>\n请稍后重试或联系母平台客服咨询。", parse_mode="HTML")

    # 1. 计算剩余有效时间
    now = datetime.utcnow()
    expire_time = current_tenant.expire_time
    if expire_time > now:
        remaining_days = (expire_time - now).days
    else:
        remaining_days = 0

    # 2. 提取并排序套餐
    clone_pkgs = {k: v for k, v in clone_data.items() if k != "_is_open"}
    sorted_clone = sorted(clone_pkgs.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)

    if not sorted_clone:
        return await message.answer("⚠️ <b>当前暂无可用的续费套餐数据，请联系母平台客服。</b>", parse_mode="HTML")

    text = (
        "🛒 <b>机器人续费中心</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⏱ <b>当前到期时间：</b> \n<code>{expire_time.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
        f"⏳ <b>剩余有效天数：</b> <b>{remaining_days}</b> 天\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>请点击下方按钮选择续费套餐：</b>"
    )

    kb_builder = InlineKeyboardBuilder()
    for day in sorted_clone:
        price = clone_pkgs[day]
        day_label = f"{day}天" if str(day).isdigit() else str(day)
        kb_builder.row(InlineKeyboardButton(text=f"💎 续费 {day_label} - {float(price):.1f} USDT", callback_data=f"tenant_renew:{day}"))

    await message.answer(text, reply_markup=kb_builder.as_markup(), parse_mode="HTML")

# 拦截续费套餐点击，生成去重偏移账单
@tenant_router.callback_query(F.data.startswith("tenant_renew:"))
async def tenant_renew_pkg_callback(call: CallbackQuery, session: AsyncSession):
    parts = call.data.split(":")
    if len(parts) != 2: return await call.answer()
    day = parts[1]
    
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    clone_fee_str = getattr(sys_config, "clone_fee_config", "{}")
    
    try: 
        fee_data = json.loads(clone_fee_str)
    except Exception: 
        fee_data = {}
    
    price = fee_data.get(day)
    if not price:
        await call.answer()
        try: await call.message.edit_text("❌ 该续费套餐不存在或已下架，请重新选择。")
        except TelegramBadRequest: pass
        return
        
    day_label = f"{day}天" if str(day).isdigit() else str(day)
    
    # 🌟 核心容错与金额去重算法
    try:
        now = datetime.utcnow()
        valid_time_limit = now - timedelta(minutes=10)
        base_price = Decimal(str(price))
        
        # 查询过去 10 分钟内 PENDING 的续费/开通订单防撞车
        stmt = select(SaaSOrder.price).where(
            SaaSOrder.status == 'PENDING',
            SaaSOrder.created_at >= valid_time_limit
        )
        occupied_prices_scalars = await session.scalars(stmt)
        occupied_prices = set(round(float(p), 2) for p in occupied_prices_scalars.all())
        
        final_price_float = float(base_price)
        while round(final_price_float, 2) in occupied_prices:
            final_price_float += 0.01
        
        final_price = Decimal(str(round(final_price_float, 2)))

        # 入库 (order_type 设为 clone，配合 /test_pay 走老客户时间累加逻辑)
        new_order = SaaSOrder(
            tg_user_id=call.from_user.id,
            order_type='clone',
            days=str(day),
            price=final_price,
            status='PENDING'
        )
        session.add(new_order)
        await session.commit()
        await session.refresh(new_order) 
        
        await call.answer()
        
        master_wallet = getattr(sys_config, "master_receive_address", "未配置全站收款地址")
        text = (
            "🧾 <b>续费订单确认</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🔖 <b>订单编号：</b> <code>#{new_order.id}</code>\n"
            f"📦 <b>续费服务：</b> 机器授权 ({day_label})\n"
            f"⏳ <b>有效时间：</b> 10 分钟\n\n"
            "💳 <b>收款地址 (TRC20)：</b>\n"
            f"<code>{master_wallet}</code>\n"
            "<i>(👆 点击上方地址可一键复制)</i>\n\n"
            "💰 <b>需支付精确金额：</b>\n"
            f"<code>{final_price:.2f}</code> <b>USDT</b> (或等额 TRX)\n"
            "<i>(👆 点击上方数字可一键复制)</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>重要安全提醒：</b>\n"
            "系统采用智能尾数识别，请<b>务必、一分不差地</b>支付上方显示的带小数点的精确金额！多付或少付均无法自动核销，且概不退还！\n\n"
            "<i>支付完成后，系统将在 1 分钟内自动为您增加授权天数。若订单超时请重新获取账单。</i>"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ 关闭账单", callback_data="close_tenant_bill")]
        ])
        
        try:
            await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            pass

    except Exception as e:
        await session.rollback()
        await call.answer("❌ 续费账单生成失败，系统数据库异常，请联系管理员！", show_alert=True)

@tenant_router.callback_query(F.data == "close_tenant_bill")
async def close_tenant_bill_handler(call: CallbackQuery):
    await call.answer()
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass


# =====================================================================
# ✨ 新增模块 B：📊 账户流水 (动态账本查询)
# =====================================================================
@tenant_router.message(StateFilter('*'), F.text == "📊 账户流水")
async def tenant_transactions_handler(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    await state.clear()
    
    # 1. 抓取近期盈利订单 (成功售出的能量单，从中赚取了 tenant_markup)
    eo_stmt = select(EnergyOrder).where(
        EnergyOrder.tenant_id == current_tenant.id,
        EnergyOrder.status == 'SUCCESS',
        EnergyOrder.tenant_markup > 0
    ).order_by(EnergyOrder.created_at.desc()).limit(15)
    energy_orders = (await session.scalars(eo_stmt)).all()
    
    # 2. 抓取近期提现申请
    wo_stmt = select(WithdrawOrder).where(
        WithdrawOrder.tenant_id == current_tenant.id
    ).order_by(WithdrawOrder.created_at.desc()).limit(15)
    withdraw_orders = (await session.scalars(wo_stmt)).all()
    
    # 3. 内存聚合账本
    transactions = []
    for eo in energy_orders:
        transactions.append({
            "time": eo.created_at,
            "type": "💸 派单分润",
            "amount": f"+{format_amount(eo.tenant_markup)}",
            "status": "已入账",
            "detail": "散客购单抽水"
        })
        
    status_map = {"PENDING": "⏳审核中", "PAID": "✅已打款", "REJECTED": "❌已驳回"}
    for wo in withdraw_orders:
        transactions.append({
            "time": wo.created_at,
            "type": "🏦 利润提现",
            "amount": f"-{format_amount(wo.amount)}",
            "status": status_map.get(wo.status, wo.status),
            "detail": f"尾号 {wo.target_address[-4:] if wo.target_address else '未知'}"
        })
        
    # 按时间降序排序，取最新的 10 笔混合流水
    transactions.sort(key=lambda x: x["time"], reverse=True)
    top_txs = transactions[:10]
    
    if not top_txs:
        return await message.answer("📊 <b>账户流水明细</b>\n━━━━━━━━━━━━━━━━━━\n📭 当前暂无任何资金变动流水记录。", parse_mode="HTML")
        
    text = "📊 <b>近期账户流水明细 (Top 10)</b>\n━━━━━━━━━━━━━━━━━━\n"
    for tx in top_txs:
        t_str = tx["time"].strftime("%m-%d %H:%M")
        text += f"▪️ <b>{tx['type']}</b> | <code>{tx['amount']}</code> TRX\n"
        text += f"   🕒 {t_str} | {tx['status']} | {tx['detail']}\n\n"
        
    text += "━━━━━━━━━━━━━━━━━━\n<i>注：账本仅按时间倒序展示最近发生的 10 笔重要资金变动。</i>"
    
    await message.answer(text, parse_mode="HTML")
    
# =====================================================================
# ✨ 新增模块 C：⚡ 开通特价 (增值功能插件购买流)
# =====================================================================
# =====================================================================
# ✨ 新增模块 C：⚡ 开通特价 (增值功能插件购买流)
# =====================================================================
@tenant_router.message(StateFilter('*'), F.text == "⚡ 开通特价")
async def tenant_special_feature_handler(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    await state.clear()
    
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    spec_fee_str = getattr(sys_config, "special_auth_config", "{}")

    try:
        spec_data = json.loads(spec_fee_str)
    except Exception:
        spec_data = {}

    sorted_spec = sorted(spec_data.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)

    if not sorted_spec:
        return await message.answer("⚠️ <b>当前暂无可用的特价插件套餐数据，请联系母平台客服。</b>", parse_mode="HTML")

    # 1. 计算剩余有效时间（对齐续费机器的逻辑）
    now = datetime.utcnow()
    expire_time = current_tenant.expire_time
    if expire_time > now:
        remaining_days = (expire_time - now).days
    else:
        remaining_days = 0

    # 2. 判断租户当前状态并对齐 UI
    if current_tenant.has_special_energy_right:
        status_text = (
            "✅ <b>当前状态：已开通</b>\n"
            f"⏱ <b>当前到期时间：</b> \n<code>{expire_time.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"⏳ <b>剩余有效天数：</b> <b>{remaining_days}</b> 天\n"
            "<i>(您可以继续购买以下套餐，延长或叠加您的特价特权服务)</i>"
        )
    else:
        status_text = "🔴 <b>当前状态：未开通</b>\n<i>(开通后您将获得极低拿货底价以及绝对静默的发货特权)</i>"

    text = (
        "⚡ <b>特价功能增值插件中心</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{status_text}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>请点击下方按钮选择套餐：</b>"
    )

    kb_builder = InlineKeyboardBuilder()
    for day in sorted_spec:
        price = spec_data[day]
        day_label = f"{day}天" if str(day).isdigit() else str(day)
        # 复用续费的购买回调流，将类型标记为 'special'
        kb_builder.row(InlineKeyboardButton(text=f"🔥 开通 {day_label} - {float(price):.1f} USDT", callback_data=f"tenant_buy_plugin:special:{day}"))

    await message.answer(text, reply_markup=kb_builder.as_markup(), parse_mode="HTML")

# 拦截插件购买套餐点击，生成去重偏移账单
@tenant_router.callback_query(F.data.startswith("tenant_buy_plugin:special:"))
async def tenant_buy_plugin_callback(call: CallbackQuery, session: AsyncSession):
    parts = call.data.split(":")
    if len(parts) != 3: return await call.answer()
    day = parts[2]
    
    sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    spec_fee_str = getattr(sys_config, "special_auth_config", "{}")
    
    try: 
        fee_data = json.loads(spec_fee_str)
    except Exception: 
        fee_data = {}
    
    price = fee_data.get(day)
    if not price:
        await call.answer()
        try: await call.message.edit_text("❌ 该增值套餐不存在或已下架，请重新选择。")
        except TelegramBadRequest: pass
        return
        
    day_label = f"{day}天" if str(day).isdigit() else str(day)
    
    # 🌟 核心容错与金额去重算法
    try:
        now = datetime.utcnow()
        valid_time_limit = now - timedelta(minutes=10)
        base_price = Decimal(str(price))
        
        # 查询过去 10 分钟内 PENDING 的订单防撞车
        stmt = select(SaaSOrder.price).where(
            SaaSOrder.status == 'PENDING',
            SaaSOrder.created_at >= valid_time_limit
        )
        occupied_prices_scalars = await session.scalars(stmt)
        occupied_prices = set(round(float(p), 2) for p in occupied_prices_scalars.all())
        
        final_price_float = float(base_price)
        while round(final_price_float, 2) in occupied_prices:
            final_price_float += 0.01
        
        final_price = Decimal(str(round(final_price_float, 2)))

        # 入库 (order_type 设为 special)
        new_order = SaaSOrder(
            tg_user_id=call.from_user.id,
            order_type='special',
            days=str(day),
            price=final_price,
            status='PENDING'
        )
        session.add(new_order)
        await session.commit()
        await session.refresh(new_order) 
        
        await call.answer()
        
        master_wallet = getattr(sys_config, "master_receive_address", "未配置全站收款地址")
        text = (
            "🧾 <b>特价插件订单确认</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🔖 <b>订单编号：</b> <code>#{new_order.id}</code>\n"
            f"📦 <b>开通服务：</b> 增值功能插件 ({day_label})\n"
            f"⏳ <b>有效时间：</b> 10 分钟\n\n"
            "💳 <b>收款地址 (TRC20)：</b>\n"
            f"<code>{master_wallet}</code>\n"
            "<i>(👆 点击上方地址可一键复制)</i>\n\n"
            "💰 <b>需支付精确金额：</b>\n"
            f"<code>{final_price:.2f}</code> <b>USDT</b> (或等额 TRX)\n"
            "<i>(👆 点击上方数字可一键复制)</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>重要安全提醒：</b>\n"
            "系统采用智能尾数识别，请<b>务必、一分不差地</b>支付上方显示的带小数点的精确金额！\n\n"
            "<i>支付完成后，系统将在 1 分钟内自动为您激活特价功能特权。</i>"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ 关闭账单", callback_data="close_tenant_bill")]
        ])
        
        try:
            await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            pass

    except Exception as e:
        await session.rollback()
        await call.answer("❌ 插件账单生成失败，系统数据库异常，请联系管理员！", show_alert=True)

# =====================================================================
# ✨ 模块 D：⚙️ 特价设置 (地址与双档位一口价管理)
# =====================================================================
@tenant_router.message(StateFilter('*'), F.text == "⚙️ 特价设置")
async def tenant_special_settings_handler(message: Message, current_tenant: Tenant, state: FSMContext):
    await state.clear()
    
    if not current_tenant.has_special_energy_right:
        return await message.answer("⚠️ <b>您尚未开通特价功能！</b>\n请先点击键盘上的【⚡ 开通特价】了解并订阅该服务。", parse_mode="HTML")
        
    special_addr = current_tenant.special_energy_address if current_tenant.special_energy_address else "未设置"
    price_65k = float(getattr(current_tenant, 'special_price_65k', 0.0) or 0.0)
    price_131k = float(getattr(current_tenant, 'special_price_131k', 0.0) or 0.0)
    duration = getattr(current_tenant, 'special_energy_duration', '1h')
    
    str_65k = f"{price_65k:.2f} TRX" if price_65k > 0 else "未开启"
    str_131k = f"{price_131k:.2f} TRX" if price_131k > 0 else "未开启"
    
    dur_5m_status = " 🟢 运行中" if duration == '5m' else ""
    dur_1h_status = " 🟢 运行中" if duration == '1h' else ""
    
    text = (
        "⚙️ <b>特价功能核心设置</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📥 <b>特价静默发货地址</b>：\n<code>{special_addr}</code>\n\n"
        f"💰 <b>65K 特价静默价格</b>：<code>{str_65k}</code>\n"
        f"💰 <b>131K 特价静默价格</b>：<code>{str_131k}</code>\n"
        f"⏱ <b>时效 5 分钟</b>：{dur_5m_status}\n"
        f"⏱ <b>时效 1 小时</b>：{dur_1h_status}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 请选择您要配置的项目："
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 设置/修改特价地址", callback_data="tenant_set_special_addr")],
        [
            InlineKeyboardButton(text="💰 设 65K 价格", callback_data="tenant_set_special_65k"),
            InlineKeyboardButton(text="💰 设 131K 价格", callback_data="tenant_set_special_131k")
        ],
        [
            InlineKeyboardButton(text="⏳ 设为 5 分钟", callback_data="tenant_set_dur_5m"),
            InlineKeyboardButton(text="⏳ 设为 1 小时", callback_data="tenant_set_dur_1h")
        ]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@tenant_router.callback_query(F.data.in_(["tenant_set_dur_5m", "tenant_set_dur_1h"]))
async def cb_set_special_duration(call: CallbackQuery, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    """处理租户动态修改特价能量时效"""
    new_dur = '5m' if call.data == "tenant_set_dur_5m" else '1h'
    await session.execute(
        update(Tenant).where(Tenant.id == current_tenant.id).values(special_energy_duration=new_dur)
    )
    await session.commit()
    # 同步更新内存对象供下文渲染
    current_tenant.special_energy_duration = new_dur
    
    try:
        await call.message.delete()
    except Exception:
        pass
    # 重新渲染主界面并提示
    await tenant_special_settings_handler(call.message, current_tenant, state)
    await call.answer(f"✅ 时效已成功切换为 {'5 分钟' if new_dur == '5m' else '1 小时'}！", show_alert=False)
# --- 1. 设置特价发货地址 ---
@tenant_router.callback_query(F.data == "tenant_set_special_addr")
async def trigger_set_special_addr(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TenantSpecialFSM.wait_special_address)
    text = "✏️ 请输入您的特价能量【绝对静默】发货波场地址\n⚠️ <i>必须是以大写 T 开头的 34 位有效波场地址！</i>\n<i>发送 /cancel 取消操作</i>"
    try:
        await call.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass

@tenant_router.message(TenantSpecialFSM.wait_special_address)
async def process_set_special_addr(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    if not message.text:
        return await message.answer("❌ 请输入有效的文本格式地址！")
    if message.text.strip() == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    addr = message.text.strip()
    if not addr.startswith("T") or len(addr) != 34:
        return await message.answer("❌ 地址格式不正确！必须是以大写 T 开头的 34 位波场地址。请重新输入：")
        
    try:
        await session.execute(update(Tenant).where(Tenant.id == current_tenant.id).values(special_energy_address=addr))
        await session.commit()
        await state.clear()
        await message.answer(f"✅ <b>特价发货地址设置成功！</b>\n\n您的专属绝对静默收货地址已更新为：\n<code>{addr}</code>", parse_mode="HTML")
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 设置失败，发生数据库系统异常。\n错误日志：{str(e)}")
        await state.clear()

# --- 2. 设置特价静默出售价格 (一口价) ---
@tenant_router.callback_query(F.data == "tenant_set_special_65k")
async def trigger_set_special_65k(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TenantSpecialFSM.wait_special_price_65k)
    text = "✏️ 请输入您的 <b>65K 能量</b> 特价静默出售一口价 (TRX)\n<i>💡 提示：输入 0 表示关闭/不售卖此档位</i>\n例如: 0.5\n<i>发送 /cancel 取消操作</i>"
    try: await call.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest: pass

@tenant_router.message(TenantSpecialFSM.wait_special_price_65k)
async def process_set_special_65k(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    if not message.text: return await message.answer("❌ 请输入有效的纯数字！")
    if message.text.strip() == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    try:
        new_price = Decimal(message.text.strip())
        if new_price < 0: raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误，请输入大于或等于 0 的有效数字：")
        
    try:
        await session.execute(update(Tenant).where(Tenant.id == current_tenant.id).values(special_price_65k=new_price))
        await session.commit()
        await state.clear()
        status_text = f"<b>{float(new_price):.2f} TRX</b>" if new_price > 0 else "<b>已关闭</b>"
        await message.answer(f"✅ <b>65K 档位设置成功！</b>\n当前 65K 零售一口价设定为: {status_text}", parse_mode="HTML")
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 设置失败，发生数据库系统异常。\n错误日志：{str(e)}")
        await state.clear()

# --- 3. 设置 131K 独立一口价 ---
@tenant_router.callback_query(F.data == "tenant_set_special_131k")
async def trigger_set_special_131k(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TenantSpecialFSM.wait_special_price_131k)
    text = "✏️ 请输入您的 <b>131K 能量</b> 特价静默出售一口价 (TRX)\n<i>💡 提示：输入 0 表示关闭/不售卖此档位</i>\n例如: 1.0\n<i>发送 /cancel 取消操作</i>"
    try: await call.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest: pass

@tenant_router.message(TenantSpecialFSM.wait_special_price_131k)
async def process_set_special_131k(message: Message, current_tenant: Tenant, session: AsyncSession, state: FSMContext):
    if not message.text: return await message.answer("❌ 请输入有效的纯数字！")
    if message.text.strip() == "/cancel":
        await state.clear()
        return await message.answer("✅ 操作已取消。")
        
    try:
        new_price = Decimal(message.text.strip())
        if new_price < 0: raise ValueError
    except Exception:
        return await message.answer("❌ 格式错误，请输入大于或等于 0 的有效数字：")
        
    try:
        await session.execute(update(Tenant).where(Tenant.id == current_tenant.id).values(special_price_131k=new_price))
        await session.commit()
        await state.clear()
        status_text = f"<b>{float(new_price):.2f} TRX</b>" if new_price > 0 else "<b>已关闭</b>"
        await message.answer(f"✅ <b>131K 档位设置成功！</b>\n当前 131K 零售一口价设定为: {status_text}", parse_mode="HTML")
    except Exception as e:
        await session.rollback()
        await message.answer(f"❌ 设置失败，发生数据库系统异常。\n错误日志：{str(e)}")
        await state.clear()
