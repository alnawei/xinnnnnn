# tron_scanner.py

import asyncio
import aiohttp
import logging
import random
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update

from config import MASTER_BOT_TOKEN
from models import (
    AsyncSessionLocal, 
    ProcessedTx, MicroDepositOrder, SaaSOrder,
    SystemConfig, User, Tenant, TronApiNode
)

# 导入真实发货接口
from netts_api import fire_netts_silent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 🛡️ 官方 USDT (TRC20) 智能合约地址，绝对防假币！
USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# ==================== 1. 独立工具库 ====================

async def send_tg_message(user_id: int, text: str):
    """独立脚本专用的 TG 消息推送器，不依赖 aiogram dispatcher"""
    url = f"https://api.telegram.org/bot{MASTER_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": user_id, "text": text, "parse_mode": "HTML"}
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"推送TG消息失败: {e}")

# ==================== 2. 能量派发与退款闭环 ====================

async def handle_balance_purchase(
    user_id: int, 
    address: str, 
    amount_type: str, 
    cost: Decimal,
    order_id: int,           # 新增：接收订单ID
    tenant_id: int,          # 新增：接收代理商ID
    tenant_profit: Decimal   # 新增：接收代理商应得纯利
):
    """【散户余额购单】强一致性闭环：发货、改状态、代理分润、失败退款。"""
    amount = 65000 if "65K" in amount_type else 131000

    async with AsyncSessionLocal() as session:
        # 1. 调用真实 Netts 网络发货
        success = await fire_netts_silent(address, amount)
        
        if success:
            logging.info(f"✅ [余额购买] Netts 真实发货成功: {address}")
            
            # A. 状态闭环：订单状态改为 SUCCESS
            await session.execute(
                update(EnergyOrder).where(EnergyOrder.id == order_id).values(status='SUCCESS')
            )
            
            # B. 代理分润：钱必须落袋为安 (发货成功了才给代理结算利润！)
            if tenant_profit > 0:
                await session.execute(
                    update(Tenant).where(Tenant.id == tenant_id).values(profit_balance=Tenant.profit_balance + tenant_profit)
                )
                
            await session.commit()
            await send_tg_message(user_id, f"✅ <b>能量派发成功！</b>\n\n🎯 目标地址：<code>{address}</code>\n⚡ 额度已就绪，请尽情转账！")
            
        else:
            logging.error(f"❌ [余额购买] Netts 发货失败，执行原子级退款回滚: {address}")
            
            # A. 状态闭环：订单改为 FAILED_REFUNDED
            await session.execute(
                update(EnergyOrder).where(EnergyOrder.id == order_id).values(status='FAILED_REFUNDED')
            )
            
            # B. 防并发穿仓退款：原路退回 TRX 资金并扣减消费次数
            stmt = update(User).where(User.tg_user_id == user_id).values(
                balance=User.balance + cost,
                total_spent_trx=User.total_spent_trx - cost,
                total_orders=User.total_orders - 1
            )
            await session.execute(stmt)
            await session.commit()
            
            await send_tg_message(user_id, "❌ <b>能量派发失败</b>\n\n上游网络波动或库存不足，派发中断。<b>扣除的 TRX 已全额安全退回至您的账户</b>，请稍后重试！")

async def process_silent_purchase(tx_hash: str, address: str, amount: Decimal, session: AsyncSession):
    """【静默打款购单】只请求发货，不触发退款与通知"""
    energy_amount = 65000 if amount < 5 else 131000
    
    success = await fire_netts_silent(address, energy_amount)
    if success:
        logging.info(f"✅ [静默打款] Netts 真实发货成功，TXID: {tx_hash}")
    else:
        logging.error(f"🚨 [静默打款] 钱已收但发货失败 (库存不足/网络异常)！此模式不予自动退款。TXID: {tx_hash}")

# ==================== 3. 动态节点池与链上数据请求 ====================

async def fetch_tron_transactions(address: str, session: AsyncSession) -> dict:
    """带节点池轮询、自适应熔断与重试机制的原生 TRX API 请求函数"""
    stmt = select(TronApiNode).where(TronApiNode.is_active == True)
    nodes = (await session.execute(stmt)).scalars().all()
    
    if not nodes:
        class DummyNode:
            id, api_key, rpc_url = None, None, "https://api.trongrid.io"
        nodes = [DummyNode()]
    else:
        random.shuffle(nodes)
        
    async with aiohttp.ClientSession() as client:
        for node in nodes:
            current_key = node.api_key
            base_url = node.rpc_url.rstrip('/') if node.rpc_url else "https://api.trongrid.io"
            url = f"{base_url}/v1/accounts/{address}/transactions"
            
            headers = {"Accept": "application/json"}
            if current_key:
                headers["TRON-PRO-API-KEY"] = current_key.strip()
                
# 🚨 核心修复：必须强制传入 visible=true 参数！
            # 否则波场节点默认返回 '41' 开头的十六进制地址，导致后续 to_address 匹配永远为 False！
            params = {
                "visible": "true",
                "only_to": "true",
                "limit": "50"
            }
            
            try:
                # 将 params 参数塞入 GET 请求，并拉长超时时间防挂起
                async with client.get(url, headers=headers, params=params, timeout=10) as response:
                    if response.status == 200:
                        if node.id is not None:
                            node.fail_count = 0
                            node.last_used_at = datetime.utcnow()
                            await session.commit()
                        return await response.json()
                        
                    elif response.status in [429, 502, 503, 504]:
                        if node.id is not None:
                            node.fail_count += 1
                            if node.fail_count >= 10:
                                node.is_active = False
                                logging.warning(f"🚨 [节点熔断] TRX 节点 #{node.id} 连续失败 10 次已软熔断！")
                            await session.commit()
                        continue
            except (asyncio.TimeoutError, aiohttp.ClientError):
                if node.id is not None:
                    node.fail_count += 1
                    if node.fail_count >= 10:
                        node.is_active = False
                    await session.commit()
                continue
                
    return {"data": [], "success": False, "error": "All nodes failed"}

async def fetch_usdt_transactions(address: str, session: AsyncSession) -> dict:
    """带节点池轮询、防假币过滤的 TRC20 (USDT) API 请求引擎"""
    stmt = select(TronApiNode).where(TronApiNode.is_active == True)
    nodes = (await session.execute(stmt)).scalars().all()
    
    if not nodes:
        class DummyNode:
            id, api_key, rpc_url = None, None, "https://api.trongrid.io"
        nodes = [DummyNode()]
    else:
        random.shuffle(nodes)
        
    params = {
        "limit": "50",
        "contract_address": USDT_CONTRACT_ADDRESS,
        "only_to": "true" 
    }
        
    async with aiohttp.ClientSession() as client:
        for node in nodes:
            current_key = node.api_key
            base_url = node.rpc_url.rstrip('/') if node.rpc_url else "https://api.trongrid.io"
            url = f"{base_url}/v1/accounts/{address}/transactions/trc20"
            
            headers = {"Accept": "application/json"}
            if current_key:
                headers["TRON-PRO-API-KEY"] = current_key.strip()
                
            try:
                async with client.get(url, headers=headers, params=params, timeout=5) as response:
                    if response.status == 200:
                        if node.id is not None:
                            node.fail_count = 0
                            node.last_used_at = datetime.utcnow()
                            await session.commit()
                        return await response.json()
                        
                    elif response.status in [429, 502, 503, 504]:
                        if node.id is not None:
                            node.fail_count += 1
                            if node.fail_count >= 10:
                                node.is_active = False
                                logging.warning(f"🚨 [节点熔断] USDT 节点 #{node.id} 连续失败 10 次已软熔断！")
                            await session.commit()
                        continue
            except Exception:
                if node.id is not None:
                    node.fail_count += 1
                    if node.fail_count >= 10:
                        node.is_active = False
                    await session.commit()
                continue
                
    return {"data": [], "success": False}


# ==================== 4. 后台全自动扫块主循环 ====================

# ==================== 4. 后台全自动扫块主循环 ====================

# ==================== 4. 后台全自动扫块主循环 ====================

# ==================== 4. 后台全自动扫块主循环 ====================

# ==================== 4. 后台全自动扫块主循环 ====================

async def run_scanner(bot, session_maker):
    """后台扫块主循环：双轨制引擎、悲观锁防双花、自动核销分发"""
    logging.info("🚀 [Scanner] 波场全自动双引擎扫块程序已启动...")
    
    loop_count = 0  # 心跳轮询计数器
    
    while True:
        loop_count += 1
        try:
            # 独立 Context Manager：保证每次循环结束自动归还连接，消灭 Event loop is closed
            async with session_maker() as session:
                config_stmt = select(SystemConfig).where(SystemConfig.id == 1)
                sys_config = (await session.execute(config_stmt)).scalar_one_or_none()
                
                if not sys_config or not sys_config.master_receive_address:
                    if loop_count % 10 == 1:
                        logging.warning("⚠️ [Scanner] 未配置主收款地址，挂起等待中...")
                    await asyncio.sleep(10)
                    continue
                    
                master_addr = sys_config.master_receive_address

                if loop_count % 10 == 0:
                    logging.info(f"💓 [Heartbeat] 扫块引擎平稳运行中，监听主地址: {master_addr[:8]}... (已轮询 {loop_count} 次)")

                # ========================================================
                # 引擎 A：原生 TRX 进账监听 (100% 对齐 test_txid.py)
                # ========================================================
                trx_response = await fetch_tron_transactions(master_addr, session)
                
                if trx_response and "data" in trx_response:
                    trx_txs = trx_response["data"]
                    batch_processed_hashes = set()
                    
                    for tx in trx_txs:
                        tx_hash = tx.get("txID")
                        if not tx_hash or tx_hash in batch_processed_hashes:
                            continue
                        
                        # 1. 严格解析波场智能合约类型
                        contract = tx.get("raw_data", {}).get("contract", [{}])[0]
                        if contract.get("type") != "TransferContract":
                            continue

                        # 2. 提取转账参数
                        param = contract.get("parameter", {}).get("value", {})
                        owner_address = param.get("owner_address")
                        
                        # 🛡️ 安全拦截：如果是主钱包自己往外提现转账，直接忽略防套现
                        # (放弃 to_address 强匹配，避开 Hex 和 Base58 地址编码陷阱)
                        if owner_address == master_addr:
                            continue

                        # 3. 精确提取 TRX 原生金额并转换精度
                        raw_amount_sun = param.get("amount", 0)
                        actual_trx_amount = Decimal(str(raw_amount_sun)) / Decimal("1000000")
                        
                        if actual_trx_amount <= 0:
                            continue

                        # 💡 强制输出重大发现探针！(证明交易已成功进入盲配区)
                        logging.info(f"👉 [重大发现] 监听到原生TRX转账，金额: {actual_trx_amount}, TXID: {tx_hash}, 准备进入盲配逻辑...")

                        # 4. 检查哈希防重放
                        exist_tx = (await session.execute(
                            select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash)
                        )).scalar_one_or_none()
                        
                        if exist_tx:
                            logging.info(f"   -> ⚠️ 该 TXID 之前已核销过，自动跳过。")
                            continue
                            
                        batch_processed_hashes.add(tx_hash)

                        # 5. 悲观锁并发防穿仓：盲配 PENDING 订单
                        order_stmt = select(MicroDepositOrder).where(
                            MicroDepositOrder.expected_amount == actual_trx_amount,
                            MicroDepositOrder.status == "PENDING"
                        ).with_for_update()
                        
                        matched_order = (await session.execute(order_stmt)).scalar_one_or_none()

                        if matched_order:
                            logging.info(f"   -> 🎉 【匹配成功】: 找到对应的 PENDING 充值订单 #{matched_order.id}，开始入账！")
                            # a. 状态闭环：MicroDepositOrder 枚举为 'SUCCESS'
                            matched_order.status = "SUCCESS"
                            
                            # b. 获取对应的用户和租户记录
                            user = (await session.execute(
                                select(User).where(User.id == matched_order.user_id).with_for_update()
                            )).scalar_one_or_none()
                            
                            tenant = (await session.execute(
                                select(Tenant).where(Tenant.id == matched_order.tenant_id).with_for_update()
                            )).scalar_one_or_none()
                            
                            if user and tenant:
                                # 智能路由：根据发起人身份判断进货本金 vs 散客余额
                                if user.tg_user_id == tenant.owner_tg_id:
                                    tenant.deposit_balance = tenant.deposit_balance + matched_order.expected_amount
                                    display_balance = float(tenant.deposit_balance)
                                    role_text = "代理本金"
                                else:
                                    user.balance = user.balance + matched_order.expected_amount
                                    display_balance = float(user.balance)
                                    role_text = "可用余额"
                                
                                # c. 写入 tx_hash 记录，完成防双花闭环
                                session.add(ProcessedTx(tx_hash=tx_hash))
                                
                                # ⚠️ 强一致性提交事务：强制物理落盘！
                                try:
                                    await session.commit()
                                    logging.info(f"✅ [Scanner] 数据库已成功持久化，核销金额: {actual_trx_amount} TRX")
                                except Exception as db_err:
                                    await session.rollback()
                                    logging.error(f"❌ [Scanner] 数据库持久化失败，已回滚: {db_err}")
                                    continue
                                
                                # d. 物理隔离的消息推送防崩气囊 (try-except 护体)
                                success_msg = (
                                    "🎉 <b>充值成功极速到账！</b>\n\n"
                                    f"💰 <b>充值金额</b>：<code>{actual_trx_amount:g}</code> TRX\n"
                                    f"💳 <b>当前{role_text}</b>：<code>{display_balance:g}</code> TRX\n"
                                    f"🔗 <b>交易凭证</b>：<code>{tx_hash}</code>\n\n"
                                    "<i>⚡ 资金已就绪，祝您使用愉快！</i>"
                                )
                                try:
                                    await bot.send_message(chat_id=int(user.tg_user_id), text=success_msg, parse_mode="HTML")
                                except Exception as notify_err:
                                    logging.warning(f"⚠️ [Scanner] 充值通知发送失败 (TG ID: {user.tg_user_id}): {notify_err}")
                            else:
                                await session.rollback()
                                logging.error(f"❌ [Scanner] 订单 #{matched_order.id} 匹配成功，但未找到用户实体，已回滚。")
                        else:
                            logging.info(f"   -> ❌ 【盲配失败】: 数据库中不存在金额为 {actual_trx_amount} 的 PENDING 订单。")

                # ========================================================
                # 引擎 B：USDT-TRC20 进账监听 (用于 SaaS 商铺授权) 保持不变
                # ========================================================
                usdt_response = await fetch_usdt_transactions(master_addr, session)
                
                if usdt_response and "data" in usdt_response:
                    usdt_txs = usdt_response["data"]
                    usdt_batch_hashes = set()
                    
                    for tx in usdt_txs:
                        tx_hash = tx.get("transaction_id")
                        if not tx_hash or tx_hash in usdt_batch_hashes:
                            continue
                            
                        # 方向与假币强校验
                        if tx.get("to") != master_addr:
                            continue
                        if tx.get("token_info", {}).get("address") != USDT_CONTRACT_ADDRESS:
                            continue

                        exist_usdt_tx = (await session.execute(
                            select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash)
                        )).scalar_one_or_none()
                        
                        if exist_usdt_tx:
                            continue
                            
                        usdt_batch_hashes.add(tx_hash)
                            
                        raw_value = tx.get("value", "0")
                        actual_usdt = Decimal(str(raw_value)) / Decimal("1000000")
                        if actual_usdt <= 0:
                            continue
                            
                        # 悲观锁锁定 SaaS 订单
                        saas_stmt = select(SaaSOrder).where(
                            SaaSOrder.status == "PENDING",
                            SaaSOrder.price == actual_usdt
                        ).with_for_update()
                        
                        matched_saas = (await session.execute(saas_stmt)).scalar_one_or_none()
                        
                        if matched_saas:
                            matched_saas.status = "PAID"
                            session.add(ProcessedTx(tx_hash=tx_hash))
                            
                            try:
                                await session.commit()
                                logging.info(f"✅ [Scanner] SaaS USDT 订单核销成功！金额: {actual_usdt} U")
                            except Exception as db_err:
                                await session.rollback()
                                logging.error(f"❌ [Scanner] SaaS USDT 订单持久化失败: {db_err}")
                                continue
                            
                            pkg_name = "独立专属子机器人授权" if matched_saas.order_type == "clone" else "增值功能插件"
                            success_text = (
                                f"🎉 <b>支付成功，您的授权已到账！</b>\n\n"
                                f"🛍️ <b>开通服务</b>：{pkg_name} ({matched_saas.days}天)\n"
                                f"💵 <b>核销金额</b>：<code>{actual_usdt:g}</code> USDT\n"
                                f"🔗 <b>交易凭证</b>：<code>{tx_hash}</code>\n\n"
                                f"🚀 <b>下一步：请立刻前往主菜单点击对应选项，绑定 Token 或开启特权！</b>"
                            )
                            try:
                                await bot.send_message(chat_id=int(matched_saas.tg_user_id), text=success_text, parse_mode="HTML")
                            except Exception as notify_err:
                                logging.warning(f"⚠️ [Scanner] SaaS 订单通知发送失败: {notify_err}")

        except Exception as e:
            logging.error(f"❌ [Scanner] 扫块循环发生严重异常: {e}", exc_info=True)
            
        await asyncio.sleep(6)

# =====================================================================
# 🚀 独立守护进程点火开关 (Standalone Entry Point)
# =====================================================================
if __name__ == "__main__":
    import asyncio
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    
    # 从配置文件和模型中引入点火所需的必需品
    from config import MASTER_BOT_TOKEN
    from models import AsyncSessionLocal
    
    async def standalone_main():
        logging.info("🚀 准备点火！波场双轨扫块独立引擎启动中...")
        # 1. 独立初始化用于发送到账通知的 Bot 实例
        master_bot = Bot(token=MASTER_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        
        try:
            # 2. 拉起主循环，注入 Bot 与数据库会话工厂
            await run_scanner(master_bot, AsyncSessionLocal)
        finally:
            # 优雅退出，释放 aiohttp 会话
            logging.info("🧹 正在释放扫块引擎资源...")
            await master_bot.session.close()

    try:
        asyncio.run(standalone_main())
    except KeyboardInterrupt:
        logging.info("🛑 收到用户强制退出信号，扫块引擎已安全关停！")
