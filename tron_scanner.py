# tron_scanner.py

import asyncio
import aiohttp
import logging
import hashlib
import time
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from config import MASTER_BOT_TOKEN
from models import (
    AsyncSessionLocal, 
    ProcessedTx, MicroDepositOrder, SaaSOrder,
    SystemConfig, User, Tenant, TronApiNode, EnergyOrder, BlockScanPointer
)

# 导入真实发货接口 (底层已支持动态 duration 分流 URL)
from netts_api import fire_netts_silent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 🛡️ 官方 USDT (TRC20) 智能合约地址，绝对防假币！
USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRX_PRECISION = Decimal("0.000001")


def rotate_nodes_by_hour(nodes: list) -> list:
    """按小时轮换 Tron API 节点；当前节点失败时，后续循环会继续尝试下一个。"""
    if len(nodes) <= 1:
        return nodes

    # 先按数据库 ID 固定顺序排列，避免每次查询顺序变化导致轮换不稳定。
    sorted_nodes = sorted(nodes, key=lambda node: node.id or 0)
    current_hour = int(time.time() // 3600)
    start_index = current_hour % len(sorted_nodes)
    return sorted_nodes[start_index:] + sorted_nodes[:start_index]


def as_trx_decimal(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(TRX_PRECISION)


def pointer_name(asset_type: str, address: str) -> str:
    digest = hashlib.sha1(address.encode("utf-8")).hexdigest()[:24]
    return f"{asset_type.lower()}:{digest}"


async def get_scan_pointer(session: AsyncSession, asset_type: str, address: str, fallback_ts: int) -> int:
    cached = GLOBAL_CURSOR_TRX if asset_type == "TRX" else GLOBAL_CURSOR_USDT
    if address in cached:
        return cached[address]

    pointer = await session.scalar(
        select(BlockScanPointer).where(BlockScanPointer.job_name == pointer_name(asset_type, address))
    )
    if pointer:
        cached[address] = int(pointer.last_scanned_block)
        return cached[address]

    cached[address] = fallback_ts
    return fallback_ts


async def save_scan_pointer(session: AsyncSession, asset_type: str, address: str, timestamp_ms: int) -> None:
    if timestamp_ms <= 0:
        return

    cached = GLOBAL_CURSOR_TRX if asset_type == "TRX" else GLOBAL_CURSOR_USDT
    cached[address] = timestamp_ms

    job_name = pointer_name(asset_type, address)
    pointer = await session.scalar(
        select(BlockScanPointer).where(BlockScanPointer.job_name == job_name).with_for_update()
    )
    if pointer:
        pointer.last_scanned_block = timestamp_ms
        pointer.address = address
        pointer.asset_type = asset_type
    else:
        session.add(BlockScanPointer(
            job_name=job_name,
            last_scanned_block=timestamp_ms,
            address=address,
            asset_type=asset_type
        ))


# ==================== 1. 独立工具库 ====================
def hex_to_base58(hex_addr: str) -> str:
    if not hex_addr or not hex_addr.startswith("41") or len(hex_addr) != 42:
        return hex_addr
        
    try:
        addr_bytes = bytes.fromhex(hex_addr)
        hash1 = hashlib.sha256(addr_bytes).digest()
        hash2 = hashlib.sha256(hash1).digest()
        full_payload = addr_bytes + hash2[:4]
        
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
    url = f"https://api.telegram.org/bot{MASTER_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": user_id, "text": text, "parse_mode": "HTML"}
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"推送TG消息失败: {e}")

# ==================== 2. 外部业务发货接口 ====================

async def handle_balance_purchase(user_id: int, address: str, amount_type: str, cost: Decimal, order_id: int, tenant_id: int, tenant_profit: Decimal):
    amount = 65000 if "65K" in amount_type else 131000

    async with AsyncSessionLocal() as session:
        success = await fire_netts_silent(address, amount)
        
        if success:
            logging.info(f"✅ [余额购买] Netts 真实发货成功: {address}")
            await session.execute(
                update(EnergyOrder).where(EnergyOrder.id == order_id).values(status='SUCCESS')
            )
            if tenant_profit > 0:
                await session.execute(
                    update(Tenant).where(Tenant.id == tenant_id).values(profit_balance=Tenant.profit_balance + tenant_profit)
                )
            await session.commit()
            await send_tg_message(user_id, f"✅ <b>能量派发成功！</b>\n\n🎯 目标地址：<code>{address}</code>\n⚡ 额度已就绪，请尽情转账！")
            
        else:
            logging.error(f"❌ [余额购买] Netts 发货失败，执行原子级退款回滚: {address}")
            await session.execute(
                update(EnergyOrder).where(EnergyOrder.id == order_id).values(status='FAILED_REFUNDED')
            )
            stmt = update(User).where(User.tg_user_id == user_id).values(
                balance=User.balance + cost,
                total_spent_trx=User.total_spent_trx - cost,
                total_orders=User.total_orders - 1
            )
            await session.execute(stmt)
            await session.commit()
            await send_tg_message(user_id, "❌ <b>能量派发失败</b>\n\n上游网络波动或库存不足，派发中断。<b>扣除的 TRX 已全额安全退回至您的账户</b>，请稍后重试！")


async def dispatch_special_energy(target_address: str, amount: int, order_id: int, session_maker, duration: str = "1h"):
    success = await fire_netts_silent(target_address, amount, duration)
    
    async with session_maker() as session:
        try:
            order = (await session.execute(
                select(EnergyOrder).where(EnergyOrder.id == order_id).with_for_update()
            )).scalar_one_or_none()
            
            if not order:
                logging.error(f"❌ [发货回执] 致命异常：找不到订单 #{order_id}，无法更新状态或退款！")
                return

            if order.status != 'PROCESSING':
                logging.info(f"ℹ️ [发货回执] 订单 #{order_id} 当前状态为 {order.status}，跳过迟到回执，避免重复记账。")
                return

            if success:
                order.status = 'SUCCESS'
                await session.commit()
                logging.info(f"🎉 [发货回执] 订单 #{order_id} 能量下发成功({duration})，资金流转闭环完成！")
            else:
                logging.warning(f"⚠️ [发货回执] 订单 #{order_id} 发货失败，立即启动代理商本金退款程序...")
                order.status = 'FAILED_REFUNDED'
                
                tenant = (await session.execute(
                    select(Tenant).where(Tenant.id == order.tenant_id).with_for_update()
                )).scalar_one_or_none()
                
                if tenant:
                    refund_amount = order.admin_base_cost
                    tenant.deposit_balance = tenant.deposit_balance + refund_amount
                    logging.info(f"✅ [发货回执] 退款成功！已向租户 #{tenant.id} 的进货本金池退回 {refund_amount} TRX。")
                else:
                    logging.error(f"❌ [发货回执] 找不到租户 #{order.tenant_id}，退款失败，发生死账！")
                    
                await session.commit()
                
        except Exception as e:
            await session.rollback()
            logging.error(f"❌ [发货回执] 状态流转或退款落盘发生严重异常: {e}", exc_info=True)


async def dispatch_global_special_energy(target_address: str, amount: int, order_id: int, session_maker, duration: str = "5m"):
    success = await fire_netts_silent(target_address, amount, duration)

    async with session_maker() as session:
        try:
            order = await session.scalar(
                select(EnergyOrder).where(EnergyOrder.id == order_id).with_for_update()
            )
            if not order:
                logging.error(f"❌ [全局直营回执] 找不到订单 #{order_id}，无法更新状态。")
                return

            if order.status != 'PROCESSING':
                logging.info(f"ℹ️ [全局直营回执] 订单 #{order_id} 当前状态为 {order.status}，跳过迟到回执。")
                return

            order.status = 'SUCCESS' if success else 'FAILED_SILENT'
            await session.commit()
            logging.info(f"{'✅' if success else '❌'} [全局直营回执] 订单 #{order_id} 状态已更新为 {order.status}。")
        except Exception as e:
            await session.rollback()
            logging.error(f"❌ [全局直营回执] 状态落盘失败: {e}", exc_info=True)


# ==================== 3. 动态节点池与链上数据请求 ====================

async def fetch_tron_paginated(
    address: str,
    session: AsyncSession,
    endpoint: str,
    base_params: dict,
    min_timestamp: int = None,
    max_pages: int = 5
) -> dict:
    stmt = select(TronApiNode).where(TronApiNode.is_active == True)
    nodes = (await session.execute(stmt)).scalars().all()
    
    if not nodes:
        class DummyNode:
            id, api_key, rpc_url = None, None, "https://api.trongrid.io"
        nodes = [DummyNode()]
    else:
        nodes = rotate_nodes_by_hour(nodes)
        
    params = dict(base_params)
    params["limit"] = "200"
    params["visible"] = "true"
    if min_timestamp:
        params["min_timestamp"] = str(min_timestamp)

    async with aiohttp.ClientSession() as client:
        for node in nodes:
            current_key = node.api_key
            base_url = node.rpc_url.rstrip('/') if node.rpc_url else "https://api.trongrid.io"
            url = f"{base_url}/v1/accounts/{address}/{endpoint}"

            headers = {"Accept": "application/json"}
            if current_key:
                headers["TRON-PRO-API-KEY"] = current_key.strip()

            all_rows = []
            next_fingerprint = None
            try:
                for _ in range(max_pages):
                    page_params = dict(params)
                    if next_fingerprint:
                        page_params["fingerprint"] = next_fingerprint

                    async with client.get(url, headers=headers, params=page_params, timeout=10) as response:
                        if response.status == 200:
                            data = await response.json()
                            all_rows.extend(data.get("data", []))
                            meta = data.get("meta", {}) or {}
                            next_fingerprint = meta.get("fingerprint")
                            if not next_fingerprint:
                                if node.id is not None:
                                    node.fail_count = 0
                                    node.last_used_at = datetime.utcnow()
                                    await session.commit()
                                return {"data": all_rows, "success": data.get("success", True), "meta": meta}
                            continue

                        if response.status in [429, 502, 503, 504]:
                            if node.id is not None:
                                node.fail_count += 1
                                if node.fail_count >= 10:
                                    node.is_active = False
                                await session.commit()
                            break

                if all_rows:
                    if node.id is not None:
                        node.fail_count = 0
                        node.last_used_at = datetime.utcnow()
                        await session.commit()
                    return {"data": all_rows, "success": True, "meta": {"truncated": bool(next_fingerprint)}}
            except Exception:
                if node.id is not None:
                    node.fail_count += 1
                    if node.fail_count >= 10:
                        node.is_active = False
                    await session.commit()
                continue
    return {"data": [], "success": False}


async def fetch_tron_transactions(address: str, session: AsyncSession, min_timestamp: int = None) -> dict:
    return await fetch_tron_paginated(
        address=address,
        session=session,
        endpoint="transactions",
        base_params={},
        min_timestamp=min_timestamp
    )


async def fetch_usdt_transactions(address: str, session: AsyncSession, min_timestamp: int = None) -> dict:
    return await fetch_tron_paginated(
        address=address,
        session=session,
        endpoint="transactions/trc20",
        base_params={
        "contract_address": USDT_CONTRACT_ADDRESS,
        "only_to": "true"
        },
        min_timestamp=min_timestamp
    )


# ==================== 4. 后台全自动扫块主循环 ====================

GLOBAL_CURSOR_TRX = {}
GLOBAL_CURSOR_USDT = {}

async def run_scanner(bot, session_maker):
    logging.info("🚀 [Scanner] 波场全自动多地址阵列游标扫块引擎已启动...")
    
    loop_count = 0
    
    while True:
        loop_count += 1
        try:
            fallback_ts = int((time.time() - 120) * 1000)

            async with session_maker() as session:
                config_stmt = select(SystemConfig).where(SystemConfig.id == 1)
                sys_config = (await session.execute(config_stmt)).scalar_one_or_none()
                
                if not sys_config or not sys_config.master_receive_address:
                    if loop_count % 10 == 1:
                        logging.warning("⚠️ [Scanner] 未配置主收款地址，挂起等待中...")
                    await asyncio.sleep(10)
                    continue
                    
                master_addr = str(sys_config.master_receive_address).strip()

                watch_addresses = [master_addr]
                if sys_config.global_special_address:
                    watch_addresses.append(str(sys_config.global_special_address).strip())
                    
                active_tenants = (await session.execute(
                    select(Tenant).where(
                        Tenant.is_active == True, 
                        Tenant.has_special_energy_right == True,
                        Tenant.special_energy_address.is_not(None)
                    )
                )).scalars().all()
                
                tenant_addr_map = {str(t.special_energy_address).strip(): t.id for t in active_tenants if t.special_energy_address}
                watch_addresses.extend(list(tenant_addr_map.keys()))
                watch_addresses = list(set(watch_addresses))

                if loop_count % 10 == 0:
                    pass # 静默心跳

                # ================= 引擎 A：原生 TRX =================
                for current_addr in watch_addresses:
                    current_min_ts_trx = await get_scan_pointer(session, "TRX", current_addr, fallback_ts)
                    trx_response = await fetch_tron_transactions(current_addr, session, current_min_ts_trx)
                    
                    if trx_response and "data" in trx_response:
                        trx_txs = trx_response["data"]
                        batch_processed_hashes = set()
                        max_ts_in_batch = current_min_ts_trx
    
                        for tx in trx_txs:
                            tx_hash = tx.get("txID", tx.get("transaction_id", "Unknown"))
                            if tx_hash == "Unknown" or tx_hash in batch_processed_hashes:
                                continue
                            
                            block_ts = tx.get("block_timestamp", 0)
                            
                            if block_ts > max_ts_in_batch:
                                max_ts_in_batch = block_ts
                                
                            if current_min_ts_trx > 0 and block_ts <= current_min_ts_trx:
                                continue
                                
                            current_ts_ms = int(time.time() * 1000)
                            if block_ts > 0 and (current_ts_ms - block_ts) > 60000:
                                continue

                            contract = tx.get("raw_data", {}).get("contract", [{}])[0]
                            c_type = contract.get("type")
                            param = contract.get("parameter", {}).get("value", {})
                            
                            raw_to_address = param.get("to_address", "")
                            raw_from_address = param.get("owner_address", "")

                            to_address = hex_to_base58(raw_to_address)
                            from_address = hex_to_base58(raw_from_address)

                            if c_type != "TransferContract":
                                continue

                            if str(to_address).strip() != str(current_addr).strip() or str(from_address).strip() == str(current_addr).strip():
                                continue

                            exist_tx = (await session.execute(select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash))).scalar_one_or_none()
                            if exist_tx:
                                continue
                                
                            batch_processed_hashes.add(tx_hash)

                            try:
                                actual_trx_amount = Decimal(str(int(param.get("amount", 0)))) / Decimal("1000000")
                            except Exception:
                                continue
                                
                            if actual_trx_amount <= 0:
                                continue

                            logging.info(f"✅ [初筛通过] 发现有效原生入账: TXID={tx_hash}, 金额={actual_trx_amount} TRX")

                            # 🔀 分流 1：主钱包微小尾数充值
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
                                            role_text, display_balance = "代理本金", float(tenant.deposit_balance)
                                        else:
                                            user.balance = user.balance + matched_order.expected_amount
                                            role_text, display_balance = "可用余额", float(user.balance)
                                        
                                        await session.merge(ProcessedTx(tx_hash=tx_hash))
                                        try:
                                            await session.commit()
                                            success_msg = f"🎉 <b>充值成功极速到账！</b>\n\n💰 <b>充值金额</b>：<code>{actual_trx_amount:g}</code> TRX\n💳 <b>当前{role_text}</b>：<code>{display_balance:g}</code> TRX\n🔗 <b>交易凭证</b>：<code>{tx_hash}</code>"
                                            try: await bot.send_message(chat_id=int(user.tg_user_id), text=success_msg, parse_mode="HTML")
                                            except Exception: pass
                                        except Exception as db_err:
                                            await session.rollback()
                                            continue
                                    else:
                                        await session.rollback()
                                else:
                                    await session.rollback()

                            # 🔀 分流 2：租户专属特价静默直转
                            elif current_addr in tenant_addr_map:
                                target_tenant_id = tenant_addr_map[current_addr]
                                tenant = (await session.execute(
                                    select(Tenant).where(Tenant.id == target_tenant_id).with_for_update()
                                )).scalar_one_or_none()
                                
                                if tenant and tenant.is_active and not tenant.is_banned:
                                    exact_actual = as_trx_decimal(actual_trx_amount)
                                    price_65k = as_trx_decimal(tenant.special_price_65k)
                                    price_131k = as_trx_decimal(tenant.special_price_131k)
                                    
                                    order_type = None
                                    energy_amount = 0
                                    
                                    if price_65k > 0 and exact_actual == price_65k:
                                        order_type = 'DIRECT_SPECIAL_65K'
                                        energy_amount = 65000
                                    elif price_131k > 0 and exact_actual == price_131k:
                                        order_type = 'DIRECT_SPECIAL_131K'
                                        energy_amount = 131000
                                        
                                    if order_type:
                                        await session.refresh(sys_config)
                                        netts_cost = Decimal(str(sys_config.netts_cost_65k)) if "65K" in order_type else Decimal(str(sys_config.netts_cost_131k))
                                        draw_cost = Decimal(str(sys_config.base_cost_65k)) if "65K" in order_type else Decimal(str(sys_config.base_cost_131k))
                                        deduction_cost = netts_cost + draw_cost
                                        
                                        if deduction_cost > 0 and tenant.deposit_balance >= deduction_cost:
                                            tenant.deposit_balance = tenant.deposit_balance - deduction_cost
                                            tenant_dur = getattr(tenant, "special_energy_duration", "1h")
                                            
                                            new_order = EnergyOrder(
                                                tenant_id=tenant.id, order_type=order_type, target_address=from_address,
                                                admin_base_cost=deduction_cost, tenant_markup=Decimal(str(actual_trx_amount)) - deduction_cost,
                                                total_user_deducted=actual_trx_amount, status='PROCESSING'
                                            )
                                            session.add(new_order)
                                            await session.merge(ProcessedTx(tx_hash=tx_hash))
                                            
                                            try:
                                                await session.commit()
                                                await session.refresh(new_order)
                                                logging.info(f"🎉 [特价派发] 代理本金暗扣成功！已拉起 Netts 发货任务 (时效: {tenant_dur})。")
                                                asyncio.create_task(dispatch_special_energy(from_address, energy_amount, new_order.id, session_maker, duration=tenant_dur))
                                            except Exception as e:
                                                await session.rollback()
                                        else:
                                            await session.rollback()
                                    else:
                                        await session.rollback()
                                else:
                                    await session.rollback()

                            # 🔀 分流 3：全局超管兜底特价直转 
                            elif sys_config.global_special_address and current_addr == str(sys_config.global_special_address).strip():
                                exact_actual = as_trx_decimal(actual_trx_amount)
                                price_65k = as_trx_decimal(sys_config.special_base_cost_65k)
                                price_131k = as_trx_decimal(sys_config.special_base_cost_131k)
                                
                                energy_amount = 0
                                
                                if price_65k > 0 and exact_actual == price_65k:
                                    order_type = 'DIRECT_SPECIAL_65K'
                                    energy_amount = 65000
                                elif price_131k > 0 and exact_actual == price_131k:
                                    order_type = 'DIRECT_SPECIAL_131K'
                                    energy_amount = 131000
                                    
                                if energy_amount > 0:
                                    new_order = EnergyOrder(
                                        tenant_id=0,
                                        order_type=order_type,
                                        target_address=from_address,
                                        admin_base_cost=Decimal("0"),
                                        tenant_markup=actual_trx_amount,
                                        total_user_deducted=actual_trx_amount,
                                        status='PROCESSING'
                                    )
                                    session.add(new_order)
                                    await session.merge(ProcessedTx(tx_hash=tx_hash))
                                    try:
                                        await session.commit()
                                        await session.refresh(new_order)
                                        logging.info(f"🎉 [特价派发] 💰 超管直营订单落盘！强制时效: 5m。")
                                        asyncio.create_task(dispatch_global_special_energy(from_address, energy_amount, new_order.id, session_maker, "5m"))
                                    except Exception as e:
                                        await session.rollback()
                                else:
                                    await session.rollback()
                            else:
                                await session.rollback()

                        if max_ts_in_batch > current_min_ts_trx:
                            await save_scan_pointer(session, "TRX", current_addr, max_ts_in_batch)
                            await session.commit()

                    await asyncio.sleep(0.5)

                # ================= 引擎 B：USDT-TRC20 =================
                current_min_ts_usdt = await get_scan_pointer(session, "USDT", master_addr, fallback_ts)
                usdt_response = await fetch_usdt_transactions(master_addr, session, current_min_ts_usdt)
                
                if usdt_response and "data" in usdt_response:
                    usdt_txs = usdt_response["data"]
                    usdt_batch_hashes = set()
                    max_ts_usdt_batch = current_min_ts_usdt
                    
                    for tx in usdt_txs:
                        tx_hash = tx.get("transaction_id", tx.get("txID", "Unknown"))
                        if tx_hash == "Unknown" or tx_hash in usdt_batch_hashes: 
                            continue
                        
                        block_ts = tx.get("block_timestamp", 0)
                        
                        if block_ts > max_ts_usdt_batch:
                            max_ts_usdt_batch = block_ts
                            
                        if block_ts <= current_min_ts_usdt:
                            continue
                            
                        current_ts_ms = int(time.time() * 1000)
                        if block_ts > 0 and (current_ts_ms - block_ts) > 60000:
                            continue
                        
                        if tx.get("to") != master_addr or tx.get("token_info", {}).get("address") != USDT_CONTRACT_ADDRESS: 
                            continue

                        exist_usdt_tx = (await session.execute(select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash))).scalar_one_or_none()
                        if exist_usdt_tx: 
                            continue
                            
                        usdt_batch_hashes.add(tx_hash)
                        actual_usdt = Decimal(str(tx.get("value", "0"))) / Decimal("1000000")
                        if actual_usdt <= 0: 
                            continue
                        
                        logging.info(f"🔍 [追踪-USDT] 解析到新入账: TXID={tx_hash}, 金额={actual_usdt}")
                        valid_saas_time = datetime.utcnow() - timedelta(minutes=10)

                        saas_stmt = select(SaaSOrder).where(
                            SaaSOrder.status == "PENDING", 
                            SaaSOrder.price == actual_usdt,
                            SaaSOrder.created_at >= valid_saas_time
                        ).order_by(SaaSOrder.created_at.asc()).with_for_update()
                        
                        matched_saas_list = (await session.execute(saas_stmt)).scalars().all()
                        
                        if matched_saas_list:
                            matched_saas = matched_saas_list[0]
                            matched_saas.status = "PAID"

                            try:
                                paid_days = int(str(matched_saas.days))
                            except (TypeError, ValueError):
                                logging.error(f"❌ [Scanner] SaaS 订单 #{matched_saas.id} days 非法: {matched_saas.days}")
                                await session.rollback()
                                continue

                            if matched_saas.order_type == "clone":
                                logging.info(
                                    f"🎉 [授权到账] 用户 {matched_saas.tg_user_id} 已付款 {paid_days} 天克隆授权，等待提交 Bot Token 激活。"
                                )
                            elif matched_saas.order_type == "special":
                                tenant_check = await session.execute(
                                    select(Tenant).where(Tenant.owner_tg_id == matched_saas.tg_user_id).with_for_update()
                                )
                                existing_tenant = tenant_check.scalar_one_or_none()

                                if not existing_tenant:
                                    logging.error(f"❌ [Scanner] 用户 {matched_saas.tg_user_id} 没有租户，无法发放特价插件权限。")
                                    await session.rollback()
                                    continue

                                now = datetime.utcnow()
                                existing_tenant.has_special_energy_right = True
                                existing_tenant.is_active = True
                                if existing_tenant.expire_time and existing_tenant.expire_time > now:
                                    existing_tenant.expire_time += timedelta(days=paid_days)
                                else:
                                    existing_tenant.expire_time = now + timedelta(days=paid_days)
                                logging.info(f"🎉 [授权下发] 已为租户 #{existing_tenant.id} 开通/续费特价插件 {paid_days} 天。")
                            
                            await session.merge(ProcessedTx(tx_hash=tx_hash))
                            try:
                                await session.commit()
                                pkg_name = "独立专属子机器人授权" if matched_saas.order_type == "clone" else "增值功能插件"
                                if matched_saas.order_type == "clone":
                                    next_step = "🚀 <b>下一步：请发送“🤖 克隆机器人”，提交 BotFather Token 完成开通。</b>"
                                else:
                                    next_step = "🚀 <b>下一步：请重新发送 /start 刷新代理商主菜单。</b>"
                                success_text = f"🎉 <b>支付成功，您的授权已到账！</b>\n\n🛍️ <b>开通服务</b>：{pkg_name} ({matched_saas.days}天)\n💵 <b>核销金额</b>：<code>{actual_usdt:g}</code> USDT\n🔗 <b>交易哈希</b>：<code>{tx_hash}</code>\n\n{next_step}"
                                try: await bot.send_message(chat_id=int(matched_saas.tg_user_id), text=success_text, parse_mode="HTML")
                                except Exception: pass
                            except Exception as db_err: 
                                await session.rollback()
                                logging.error(f"❌ [Scanner] SaaS USDT 持久化失败: {db_err}")
                        else:
                            await session.rollback()

                    if max_ts_usdt_batch > current_min_ts_usdt:
                        await save_scan_pointer(session, "USDT", master_addr, max_ts_usdt_batch)
                        await session.commit()

                await asyncio.sleep(1)

        except Exception as e:
            logging.error(f"❌ [Scanner] 扫块循环发生严重异常: {e}", exc_info=True)
            
        await asyncio.sleep(3)


# ==================== 5. 点火开关 ====================
if __name__ == "__main__":
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    
    async def standalone_main():
        logging.info("🚀 准备点火！波场双轨扫块独立引擎启动中...")
        master_bot = Bot(token=MASTER_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        try:
            await run_scanner(master_bot, AsyncSessionLocal)
        finally:
            await master_bot.session.close()

    try:
        asyncio.run(standalone_main())
    except KeyboardInterrupt:
        logging.info("🛑 收到用户强制退出信号，扫块引擎已安全关停！")
