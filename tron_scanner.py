# tron_scanner.py

import asyncio
import aiohttp
import logging
import random
import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from config import MASTER_BOT_TOKEN
from models import (
    AsyncSessionLocal, 
    ProcessedTx, MicroDepositOrder, SaaSOrder,
    SystemConfig, User, Tenant, TronApiNode, EnergyOrder
)
# 导入真实发货接口
from netts_api import fire_netts_silent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 🛡️ 官方 USDT (TRC20) 智能合约地址，绝对防假币！
USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# ==================== 1. 独立工具库 ====================
def hex_to_base58(hex_addr: str) -> str:
    """
    原生纯 Python 实现的 Hex 转 Base58Check (波场标准)
    完全零外部依赖，摆脱环境报错！
    """
    if not hex_addr or not hex_addr.startswith("41") or len(hex_addr) != 42:
        return hex_addr
        
    try:
        # 1. 16进制转 bytes
        addr_bytes = bytes.fromhex(hex_addr)
        
        # 2. 计算双重 SHA256
        hash1 = hashlib.sha256(addr_bytes).digest()
        hash2 = hashlib.sha256(hash1).digest()
        
        # 3. 追加前4字节校验和
        full_payload = addr_bytes + hash2[:4]
        
        # 4. 原生 Base58 编码数学算法
        ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        num = int.from_bytes(full_payload, 'big')
        
        res = []
        while num > 0:
            num, rem = divmod(num, 58)
            res.append(ALPHABET[rem])
            
        return "".join(reversed(res))
    except Exception:
        return hex_addr
        
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

async def fetch_tron_transactions(address: str, session: AsyncSession, min_timestamp: int = None) -> dict:
    """带节点池轮询、自适应熔断的原生 TRX API 请求引擎 (加装时间滑动窗口)"""
    stmt = select(TronApiNode).where(TronApiNode.is_active == True)
    nodes = (await session.execute(stmt)).scalars().all()
    
    if not nodes:
        class DummyNode:
            id, api_key, rpc_url = None, None, "https://api.trongrid.io"
        nodes = [DummyNode()]
    else:
        random.shuffle(nodes)
        
    # 🚨 扩容视野：单次拉取提升至 200 条，防止高并发瞬间挤出盲区
    params = {
        "limit": "200",
        "visible": "true" 
    }
    # 动态附加游标：只拉取该时间戳之后的新交易
    if min_timestamp:
        params["min_timestamp"] = str(min_timestamp)
        
    async with aiohttp.ClientSession() as client:
        for node in nodes:
            current_key = node.api_key
            base_url = node.rpc_url.rstrip('/') if node.rpc_url else "https://api.trongrid.io"
            url = f"{base_url}/v1/accounts/{address}/transactions"
            
            headers = {"Accept": "application/json"}
            if current_key:
                headers["TRON-PRO-API-KEY"] = current_key.strip()
                
            try:
                async with client.get(url, headers=headers, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        tx_list = data.get("data", [])
                        logging.info(f"🌐 [API探测-TRX] 地址 {address[:8]}... -> 状态 200, 拿到 {len(tx_list)} 条记录")
                        
                        if node.id is not None:
                            node.fail_count = 0
                            node.last_used_at = datetime.utcnow()
                            await session.commit()
                        return data
                    elif response.status in [429, 502, 503, 504]:
                        if node.id is not None:
                            node.fail_count += 1
                            if node.fail_count >= 10:
                                node.is_active = False
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

async def fetch_usdt_transactions(address: str, session: AsyncSession, min_timestamp: int = None) -> dict:
    """带节点池轮询、防假币的 TRC20 (USDT) API 请求引擎 (加装时间滑动窗口)"""
    stmt = select(TronApiNode).where(TronApiNode.is_active == True)
    nodes = (await session.execute(stmt)).scalars().all()
    
    if not nodes:
        class DummyNode:
            id, api_key, rpc_url = None, None, "https://api.trongrid.io"
        nodes = [DummyNode()]
    else:
        random.shuffle(nodes)
        
    params = {
        "limit": "200",
        "contract_address": USDT_CONTRACT_ADDRESS,
        "only_to": "true",
        "visible": "true"
    }
    if min_timestamp:
        params["min_timestamp"] = str(min_timestamp)
        
    async with aiohttp.ClientSession() as client:
        for node in nodes:
            current_key = node.api_key
            base_url = node.rpc_url.rstrip('/') if node.rpc_url else "https://api.trongrid.io"
            url = f"{base_url}/v1/accounts/{address}/transactions/trc20"
            
            headers = {"Accept": "application/json"}
            if current_key:
                headers["TRON-PRO-API-KEY"] = current_key.strip()
                
            try:
                async with client.get(url, headers=headers, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        tx_list = data.get("data", [])
                        logging.info(f"🌐 [API探测-USDT] 地址 {address[:8]}... -> 状态 200, 拿到 {len(tx_list)} 条记录")
                        
                        if node.id is not None:
                            node.fail_count = 0
                            node.last_used_at = datetime.utcnow()
                            await session.commit()
                        return data
                    elif response.status in [429, 502, 503, 504]:
                        if node.id is not None:
                            node.fail_count += 1
                            if node.fail_count >= 10:
                                node.is_active = False
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

# =====================================================================
# 辅助核销发货函数：特价静默派发与状态流转
# =====================================================================
async def dispatch_special_energy(target_address: str, amount: int, order_id: int, session_maker):
    """特价能量静默发货引擎：发货并更新订单状态 (失败不退款)"""
    success = await fire_netts_silent(target_address, amount)
    
    # 异步另起会话更新状态，防止阻塞主循环
    async with session_maker() as session:
        final_status = 'SUCCESS' if success else 'FAILED_SILENT'
        from sqlalchemy import update
        await session.execute(
            update(EnergyOrder).where(EnergyOrder.id == order_id).values(status=final_status)
        )
        await session.commit()


# ==================== 4. 后台全自动扫块主循环 ====================

# =====================================================================
# 辅助核销发货函数：特价静默派发与状态流转
# =====================================================================
async def dispatch_special_energy(target_address: str, amount: int, order_id: int, session_maker):
    """特价能量静默发货引擎：发货并更新订单状态 (失败不退款)"""
    success = await fire_netts_silent(target_address, amount)
    
    # 异步另起会话更新状态，防止阻塞主循环
    async with session_maker() as session:
        final_status = 'SUCCESS' if success else 'FAILED_SILENT'
        from sqlalchemy import update
        await session.execute(
            update(EnergyOrder).where(EnergyOrder.id == order_id).values(status=final_status)
        )
        await session.commit()

# ==================== 4. 后台全自动扫块主循环 ====================

# ==================== 4. 后台全自动扫块主循环 ====================

async def run_scanner(bot, session_maker):
    """后台扫块主循环：双轨游标阵列轮询、悲观锁防双花"""
    logging.info("🚀 [Scanner] 波场全自动多地址阵列游标扫块引擎已启动...")
    
    loop_count = 0
    # 🧠 SRE：建立内存级游标字典 (记录每个地址上次扫到的最晚时间戳，毫秒级)
    # 初始化为 15 分钟前，避免程序重启时重复扫描远古数据
    init_ts = int((datetime.utcnow() - timedelta(minutes=15)).timestamp() * 1000)
    cursor_trx = {}
    cursor_usdt = {}
    
    while True:
        loop_count += 1
        try:
            async with session_maker() as session:
                config_stmt = select(SystemConfig).where(SystemConfig.id == 1)
                sys_config = (await session.execute(config_stmt)).scalar_one_or_none()
                
                if not sys_config or not sys_config.master_receive_address:
                    await asyncio.sleep(10)
                    continue
                    
                master_addr = sys_config.master_receive_address

                watch_addresses = [master_addr]
                if sys_config.global_special_address:
                    watch_addresses.append(sys_config.global_special_address)
                    
                active_tenants = (await session.execute(
                    select(Tenant).where(
                        Tenant.is_active == True, 
                        Tenant.has_special_energy_right == True,
                        Tenant.special_energy_address.is_not(None)
                    )
                )).scalars().all()
                
                tenant_addr_map = {t.special_energy_address: t for t in active_tenants if t.special_energy_address}
                watch_addresses.extend(list(tenant_addr_map.keys()))
                watch_addresses = list(set(watch_addresses)) # 去重

                if loop_count % 10 == 0:
                    logging.info(f"💓 [Heartbeat] 阵列雷达平稳运行，共监听 {len(watch_addresses)} 个独立地址 (已轮询 {loop_count} 次)")

                # 🚀 核心修复：单体循环遍历所有地址，每个地址均执行 TRX + USDT 双引擎扫描！
                for current_addr in watch_addresses:
                    # ========================================================
                    # 引擎 A：原生 TRX 进账滑动监听
                    # ========================================================
                    min_ts_trx = cursor_trx.get(current_addr, init_ts)
                    trx_response = await fetch_tron_transactions(current_addr, session, min_ts_trx)
                    
                    if trx_response and "data" in trx_response:
                        trx_txs = trx_response["data"]
                        batch_processed_hashes = set()
                        
                        # 💡 修复一：滑动窗口推进！提取这批数据中的最大时间戳，保证下次只查最新数据
                        if trx_txs:
                            timestamps = [tx.get("block_timestamp") for tx in trx_txs if tx.get("block_timestamp")]
                            if timestamps:
                                max_ts = max(timestamps)
                                cursor_trx[current_addr] = max_ts + 1  # 游标向后推 1 毫秒，防重读
                                logging.info(f"⏱️ [游标推进] 地址 {current_addr[:8]} 最新扫描时间戳已推进至: {max_ts}")
    
                        for tx in trx_txs:
                            tx_hash = tx.get("txID")
                            if not tx_hash or tx_hash in batch_processed_hashes:
                                continue
                            
                            # 1. 拆解合约数据
                            contract = tx.get("raw_data", {}).get("contract", [{}])[0]
                            c_type = contract.get("type")
                            param = contract.get("parameter", {}).get("value", {})
                            raw_to_address = param.get("to_address", "")
                            raw_from_address = param.get("owner_address", "")

                            # 💡 核心修复：纯内置库实现 Hex 转 Base58，彻底废弃外部依赖
                            to_address = hex_to_base58(raw_to_address)
                            from_address = hex_to_base58(raw_from_address)

                            # 🚨 探针 1：打印经过格式归一化转换后的真实地址
                            logging.info(f"🔍 [解析调试] 扫到交易哈希: {tx_hash}, 类型: {c_type}, 到账地址: {to_address} (原Hex: {raw_to_address})")

                            if c_type != "TransferContract":
                                logging.warning(f"🚫 [拦截] 交易类型不符或非目标合约 ({c_type})")
                                continue

                            # 检查资金流向 (此时双方都已经统一为 T 开头的 Base58 格式)
                            if str(to_address).strip() != str(current_addr).strip():
                                logging.warning(f"🚫 [拦截] 资金流向不符 (链上: {to_address} != 正在监听: {current_addr})")
                                continue
                                
                            if str(from_address).strip() == str(current_addr).strip():
                                continue

                            # 防重放查询
                            exist_tx = (await session.execute(select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash))).scalar_one_or_none()
                            if exist_tx:
                                continue
                                
                            batch_processed_hashes.add(tx_hash)

                            # 提取并转换 TRX 金额
                            try:
                                raw_amount_sun = int(param.get("amount", 0))
                                actual_trx_amount = Decimal(str(raw_amount_sun)) / Decimal("1000000")
                            except Exception as e:
                                logging.error(f"❌ [金额解析失败] TXID={tx_hash}: {e}")
                                continue
                                
                            if actual_trx_amount <= 0:
                                continue

                            logging.info(f"✅ [初筛通过] 发现有效原生入账: TXID={tx_hash}, 金额={actual_trx_amount} TRX, 准备分流...")

                            # ===================== 分流 1：主钱包微小尾数充值盲配 =====================
                            if current_addr == master_addr:
                                order_stmt = select(MicroDepositOrder).where(
                                    MicroDepositOrder.expected_amount == actual_trx_amount,
                                    MicroDepositOrder.status == "PENDING"
                                ).order_by(MicroDepositOrder.created_at.asc()).with_for_update()
                                
                                matched_orders = (await session.execute(order_stmt)).scalars().all()
                                
                                if matched_orders:
                                    matched_order = matched_orders[0]
                                    matched_order.status = "SUCCESS"
                                    
                                    user = (await session.execute(select(User).where(User.id == matched_order.user_id).with_for_update())).scalar_one_or_none()
                                    tenant = (await session.execute(select(Tenant).where(Tenant.id == matched_order.tenant_id).with_for_update())).scalar_one_or_none()
                                    
                                    if user and tenant:
                                        if user.tg_user_id == tenant.owner_tg_id:
                                            tenant.deposit_balance = tenant.deposit_balance + matched_order.expected_amount
                                            role_text = "代理本金"
                                            display_balance = float(tenant.deposit_balance)
                                        else:
                                            user.balance = user.balance + matched_order.expected_amount
                                            role_text = "可用余额"
                                            display_balance = float(user.balance)
                                        
                                        await session.merge(ProcessedTx(tx_hash=tx_hash))
                                        
                                        try:
                                            await session.commit()
                                            success_msg = (
                                                f"🎉 <b>充值成功极速到账！</b>\n\n"
                                                f"💰 <b>充值金额</b>：<code>{actual_trx_amount:g}</code> TRX\n"
                                                f"💳 <b>当前{role_text}</b>：<code>{display_balance:g}</code> TRX\n"
                                                f"🔗 <b>交易凭证</b>：<code>{tx_hash}</code>"
                                            )
                                            try:
                                                await bot.send_message(chat_id=int(user.tg_user_id), text=success_msg, parse_mode="HTML")
                                            except Exception:
                                                pass
                                        except Exception as db_err:
                                            await session.rollback()
                                            logging.error(f"❌ 充值持久化失败: {db_err}")
                                            continue
                                    else:
                                        await session.rollback()
                                else:
                                    logging.warning(f"🚫 [拦截] 充值盲配失败: 未找到金额为 {actual_trx_amount} 的 PENDING 订单。")
                                    await session.rollback()
                                    await session.merge(ProcessedTx(tx_hash=tx_hash))
                                    await session.commit()

                            # ===================== 分流 2：租户专属特价静默直转 =====================
                            elif current_addr in tenant_addr_map:
                                tenant_info = tenant_addr_map[current_addr]
                                tenant = (await session.execute(
                                    select(Tenant).where(Tenant.id == tenant_info.id).with_for_update()
                                )).scalar_one_or_none()
                                
                                if tenant and tenant.is_active and not tenant.is_banned:
                                    float_actual = float(actual_trx_amount)
                                    float_65k = float(tenant.special_price_65k or 0)
                                    float_131k = float(tenant.special_price_131k or 0)
                                    
                                    order_type = None
                                    energy_amount = 0
                                    
                                    if float_65k > 0 and float_actual == float_65k:
                                        order_type = 'DIRECT_SPECIAL_65K'
                                        energy_amount = 65000
                                    elif float_131k > 0 and float_actual == float_131k:
                                        order_type = 'DIRECT_SPECIAL_131K'
                                        energy_amount = 131000
                                        
                                    if order_type:
                                        netts_cost = Decimal(str(sys_config.netts_cost_65k)) if "65K" in order_type else Decimal(str(sys_config.netts_cost_131k))
                                        draw_cost = Decimal(str(sys_config.base_cost_65k)) if "65K" in order_type else Decimal(str(sys_config.base_cost_131k))
                                        deduction_cost = netts_cost + draw_cost
                                        
                                        if deduction_cost > 0 and tenant.deposit_balance >= deduction_cost:
                                            tenant.deposit_balance = tenant.deposit_balance - deduction_cost
                                            
                                            new_order = EnergyOrder(
                                                tenant_id=tenant.id,
                                                order_type=order_type,
                                                target_address=from_address,
                                                admin_base_cost=deduction_cost,
                                                tenant_markup=Decimal(str(actual_trx_amount)) - deduction_cost,
                                                total_user_deducted=actual_trx_amount,
                                                status='PROCESSING'
                                            )
                                            session.add(new_order)
                                            await session.merge(ProcessedTx(tx_hash=tx_hash))
                                            
                                            try:
                                                await session.commit()
                                                await session.refresh(new_order)
                                                logging.info(f"🎉 [特价派发] 代理本金暗扣成功！已拉起 Netts 发货任务。")
                                                asyncio.create_task(dispatch_special_energy(from_address, energy_amount, new_order.id, session_maker))
                                            except Exception as e:
                                                await session.rollback()
                                                logging.error(f"❌ 订单生成失败: {e}")
                                        else:
                                            logging.warning(f"🚫 [拦截] 代理本金不足以扣除成本 {deduction_cost} TRX，已拒绝静默派发。")
                                            await session.rollback()
                                            await session.merge(ProcessedTx(tx_hash=tx_hash))
                                            await session.commit()
                                    else:
                                        logging.warning(f"🚫 [拦截] 金额不匹配 (链上: {float_actual} TRX, 期望特价 65K:{float_65k}, 131K:{float_131k})。")
                                        await session.rollback()
                                        await session.merge(ProcessedTx(tx_hash=tx_hash))
                                        await session.commit()
                                else:
                                    logging.warning(f"🚫 [拦截] 未找到对应收款代理商(或已被封禁)！")
                                    await session.rollback()
                                    await session.merge(ProcessedTx(tx_hash=tx_hash))
                                    await session.commit()


                    # ========================================================
                    # 引擎 B：USDT-TRC20 进账滑动监听 (💡 性能优化：仅查主钱包)
                    # ========================================================
                    if current_addr == master_addr:
                        min_ts_usdt = cursor_usdt.get(master_addr, init_ts)
                        usdt_response = await fetch_usdt_transactions(master_addr, session, min_ts_usdt)
                        
                        if usdt_response and "data" in usdt_response:
                            usdt_txs = usdt_response["data"]
                            usdt_batch_hashes = set()
                            
                            if usdt_txs:
                                max_ts = max([tx.get("block_timestamp", min_ts_usdt) for tx in usdt_txs])
                                cursor_usdt[master_addr] = max_ts + 1
                            
                            for tx in usdt_txs:
                                tx_hash = tx.get("transaction_id")
                                if not tx_hash or tx_hash in usdt_batch_hashes: continue
                                
                                # 防出账与假币过滤
                                if tx.get("to") != master_addr or tx.get("token_info", {}).get("address") != USDT_CONTRACT_ADDRESS: 
                                    continue

                                exist_usdt_tx = (await session.execute(select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash))).scalar_one_or_none()
                                if exist_usdt_tx: continue
                                    
                                usdt_batch_hashes.add(tx_hash)
                                actual_usdt = Decimal(str(tx.get("value", "0"))) / Decimal("1000000")
                                if actual_usdt <= 0: continue
                                
                                logging.info(f"🔍 [追踪-1/5] 解析到新入账: TXID={tx_hash}, 金额={actual_usdt}, 类型=USDT, 收款方={master_addr}")
                                
                                # SaaS 订单盲配
                                saas_stmt = select(SaaSOrder).where(
                                    SaaSOrder.status == "PENDING", 
                                    SaaSOrder.price == actual_usdt
                                ).order_by(SaaSOrder.created_at.asc()).with_for_update()
                                
                                matched_saas_list = (await session.execute(saas_stmt)).scalars().all()
                                
                                if matched_saas_list:
                                    if len(matched_saas_list) > 1:
                                        logging.warning(f"⚠️ [防撞击警告] USDT 金额 {actual_usdt} 存在多笔 PENDING，匹配订单 #{matched_saas_list[0].id}。")
                                    matched_saas = matched_saas_list[0]
                                    matched_saas.status = "PAID"
                                    
                                    await session.merge(ProcessedTx(tx_hash=tx_hash))
                                    try:
                                        await session.commit()
                                        pkg_name = "独立专属子机器人授权" if matched_saas.order_type == "clone" else "增值功能插件"
                                        success_text = f"🎉 <b>支付成功，您的授权已到账！</b>\n\n🛍️ <b>开通服务</b>：{pkg_name} ({matched_saas.days}天)\n💵 <b>核销金额</b>：<code>{actual_usdt:g}</code> USDT\n🔗 <b>交易哈希</b>：<code>{tx_hash}</code>\n\n🚀 <b>下一步：请立刻前往主菜单点击对应选项，绑定 Token 或开启特权！</b>"
                                        try: await bot.send_message(chat_id=int(matched_saas.tg_user_id), text=success_text, parse_mode="HTML")
                                        except Exception: pass
                                    except Exception as db_err: 
                                        await session.rollback()
                                        logging.error(f"❌ [Scanner] SaaS USDT 持久化失败: {db_err}")
                                else:
                                    # 吞噬无效的无关 USDT 转账
                                    await session.rollback()
                                    await session.merge(ProcessedTx(tx_hash=tx_hash))
                                    await session.commit()

                    # 阵列防封避退：每个地址查询之间休眠 0.2 秒
                    await asyncio.sleep(0.2)

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
