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
                
            try:
                async with client.get(url, headers=headers, timeout=5) as response:
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

async def run_scanner(bot, session_maker: sessionmaker):
    """后台扫块主循环：抓取、悲观锁防双花碰撞、核销、通知"""
    logging.info("🚀 [Scanner] 波场全自动双引擎扫块程序已启动...")
    
    while True:
        try:
            async with session_maker() as session:
                config_stmt = select(SystemConfig).where(SystemConfig.id == 1)
                sys_config = (await session.execute(config_stmt)).scalar_one_or_none()
                
                if not sys_config or not sys_config.master_receive_address:
                    await asyncio.sleep(10)
                    continue
                    
                master_addr = sys_config.master_receive_address

                # ========================================================
                # 引擎 A：原生 TRX 进账监听 (用于微小尾数充值)
                # ========================================================
                trx_response = await fetch_tron_transactions(master_addr, session)
                
                if trx_response and "data" in trx_response:
                    trx_txs = trx_response["data"]
                    batch_processed_hashes = set()
                    
                    for tx in trx_txs:
                        tx_hash = tx.get("txID")
                        if not tx_hash or tx_hash in batch_processed_hashes:
                            continue
                        
                        # 🛡️ 防御 1：严格校验合约类型
                        contract = tx.get("raw_data", {}).get("contract", [{}])[0]
                        if contract.get("type") != "TransferContract":
                            continue

                        # 🛡️ 防御 2：严格校验资金流向，防出账欺骗
                        param = contract.get("parameter", {}).get("value", {})
                        if param.get("to_address") != master_addr:
                            continue

                        # 检查哈希防重放
                        exist_tx = (await session.execute(
                            select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash)
                        )).scalar_one_or_none()
                        
                        if exist_tx:
                            continue
                            
                        batch_processed_hashes.add(tx_hash)

                        raw_amount_sun = param.get("amount", 0)
                        actual_trx_amount = Decimal(str(raw_amount_sun)) / Decimal("1000000")
                        if actual_trx_amount <= 0:
                            continue

                        # 🛡️ 防御 3：悲观锁并发防穿仓
                        order_stmt = select(MicroDepositOrder).where(
                            MicroDepositOrder.expected_amount == actual_trx_amount,
                            MicroDepositOrder.status == "PENDING"
                        ).with_for_update()
                        
                        matched_order = (await session.execute(order_stmt)).scalar_one_or_none()

                        if matched_order:
                            matched_order.status = "SUCCESS"
                            
                            user = (await session.execute(
                                select(User).where(User.id == matched_order.user_id).with_for_update()
                            )).scalar_one_or_none()
                            
                            if user:
                                user.balance = user.balance + matched_order.expected_amount
                                session.add(ProcessedTx(tx_hash=tx_hash))
                                await session.commit()
                                
                                success_msg = (
                                    "🎉 <b>充值成功到账！</b>\n\n"
                                    f"💰 <b>充值金额</b>：<code>{actual_trx_amount:g}</code> TRX\n"
                                    f"💳 <b>当前余额</b>：<code>{float(user.balance):g}</code> TRX\n"
                                    f"🔗 <b>交易哈希</b>：<code>{tx_hash}</code>\n\n"
                                    "<i>⚡ 您现在可以畅快使用平台的自动租用服务了！</i>"
                                )
                                try:
                                    await bot.send_message(chat_id=user.tg_user_id, text=success_msg, parse_mode="HTML")
                                except Exception as e:
                                    logging.warning(f"⚠️ TRX 到账通知发送失败: {e}")


                # ========================================================
                # 引擎 B：USDT-TRC20 进账监听 (用于 SaaS 商铺授权购买)
                # ========================================================
                usdt_response = await fetch_usdt_transactions(master_addr, session)
                
                if usdt_response and "data" in usdt_response:
                    usdt_txs = usdt_response["data"]
                    usdt_batch_hashes = set()
                    
                    for tx in usdt_txs:
                        tx_hash = tx.get("transaction_id")
                        if not tx_hash or tx_hash in usdt_batch_hashes:
                            continue
                            
                        # 🛡️ 防御 1：方向双重校验 (防转出欺骗)
                        to_addr = tx.get("to")
                        if to_addr != master_addr:
                            continue
                            
                        # 🛡️ 防御 2：官方 USDT 合约强校验 (防假 U 攻击)
                        token_addr = tx.get("token_info", {}).get("address")
                        if token_addr != USDT_CONTRACT_ADDRESS:
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
                            
                        # 🛡️ 防御 3：悲观锁并发防穿仓核销
                        saas_stmt = select(SaaSOrder).where(
                            SaaSOrder.status == "PENDING",
                            SaaSOrder.price == actual_usdt
                        ).with_for_update()
                        
                        matched_saas = (await session.execute(saas_stmt)).scalar_one_or_none()
                        
                        if matched_saas:
                            matched_saas.status = "PAID"
                            session.add(ProcessedTx(tx_hash=tx_hash))
                            await session.commit()
                            
                            pkg_name = "独立专属子机器人授权" if matched_saas.order_type == "clone" else "增值功能插件"
                            success_text = (
                                f"🎉 <b>支付成功，您的授权已到账！</b>\n\n"
                                f"🛍️ <b>开通服务</b>：{pkg_name} ({matched_saas.days}天)\n"
                                f"💵 <b>核销金额</b>：<code>{actual_usdt:g}</code> USDT\n"
                                f"🔗 <b>交易哈希</b>：<code>{tx_hash}</code>\n\n"
                                f"🚀 <b>下一步：请立刻前往主菜单点击对应选项，绑定 Token 或开启特权！</b>"
                            )
                            try:
                                await bot.send_message(chat_id=matched_saas.tg_user_id, text=success_text, parse_mode="HTML")
                                logging.info(f"✅ [Scanner] SaaS USDT 订单核销成功！(TG ID: {matched_saas.tg_user_id} | 金额: {actual_usdt} U)")
                            except Exception as e:
                                logging.warning(f"⚠️ [Scanner] SaaS 订单通知发送失败: {e}")

        except Exception as e:
            logging.error(f"❌ [Scanner] 扫块循环发生严重异常: {e}")
            
        # 安全退避：严格遵守 API 频率限制，每 6 秒双引轮询一次
        await asyncio.sleep(6)
