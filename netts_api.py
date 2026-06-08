# netts_api.py
import asyncio
import aiohttp
import logging
from config import NETTS_API_KEY, NETTS_ORDER_URL, NETTS_PRICING_URL, SERVER_IP

async def get_netts_pricing() -> dict:
    """
    询价引擎：向 Netts 发起网络请求获取当前各时段价格。
    如果网络异常或超时，返回 None。
    """
    headers = {
        "X-API-KEY": NETTS_API_KEY,
        "X-Real-IP": SERVER_IP
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(NETTS_PRICING_URL, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logging.error(f"[Netts API] 获取成本价失败，HTTP 状态码: {resp.status}")
                    return None
    except Exception as e:
        logging.error(f"[Netts API] 网络探针异常: {e}")
        return None


async def fire_netts_silent(address: str, amount: int) -> bool:
    """
    🚨 静默发货引擎 (供 tron_scanner.py 扫块底层使用) 
    作为 SaaS 底层，无论发货成功与否，绝不抛出任何 Exception 阻断扫块中枢的主循环。
    """
    import json
    headers = {
        "X-API-KEY": NETTS_API_KEY,
        "X-Real-IP": SERVER_IP,
        "Content-Type": "application/json"
    }
        # 💡 核心修复 3：严格对齐官方文档，目标地址字段必须叫 receiveAddress！
    payload = {
        "receiveAddress": address,
        "amount": amount
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(NETTS_ORDER_URL, json=payload, headers=headers, timeout=12) as resp:
                # 💡 核心修复 1：强制截获并打印上游原始字符串响应，彻底打破黑盒！
                raw_text = await resp.text()
                logging.info(f"🌐 [Netts API 原始响应] HTTP {resp.status} | 内容: {raw_text}")
                
                if resp.status == 200:
                    try:
                        data = json.loads(raw_text)
                    except json.JSONDecodeError:
                        logging.error("❌ [Netts API] 返回数据不是合法 JSON，发货失败。")
                        return False
                        
                    # 💡 核心修复 2：严格判定业务成功码 (依据 Netts v2 接口规范 code == 0 为成功)
                    if data.get("code") == 0 or data.get("success") is True or data.get("status") == "success":
                        logging.info(f"✅ [Netts API] 静默发货成功 -> 地址: {address} | 能量: {amount}")
                        return True
                    else:
                        logging.warning(f"❌ [Netts API] 订单被上游拒绝！错误详情: {data.get('msg', data.get('message', '未知业务拦截'))}")
                        return False
                else:
                    logging.error(f"❌ [Netts API] HTTP 请求异常，状态码: {resp.status}")
                    return False
    except Exception as e:
        logging.error(f"❌ [Netts API] 发货网络故障: {e}")
        return False


# =========================================================
# 追加功能：供 C 端直接调用的精细化发货接口与查余额接口
# =========================================================

async def delegate_energy(target_address: str, amount: int, duration: str = "1H") -> dict:
    """
    调用 Netts 上游 API 进行能量派发 (带详细错误回执，供 routers/user.py 前台退款判定)
    """
    headers = {
        "X-API-KEY": NETTS_API_KEY,
        "X-Real-IP": SERVER_IP,
        "Content-Type": "application/json"
    }
    
    payload = {
        "address": target_address,
        "amount": amount,
        # 如果上游支持 duration 字段请保留，否则可按需裁减
        "period": duration 
    }

    try:
        async with aiohttp.ClientSession() as session:
            # 设定 15 秒超时，防止上游假死拖垮机器人协程
            async with session.post(NETTS_ORDER_URL, json=payload, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success") is True or data.get("code") == 0 or "order_id" in data:
                        logging.info(f"[Netts API] C端直购派发成功 -> 地址: {target_address} | 能量: {amount}")
                        return {"success": True, "msg": "派发成功"}
                    else:
                        error_msg = data.get("msg") or data.get("message") or str(data)
                        logging.warning(f"[Netts API] C端直购被拒: {error_msg}")
                        return {"success": False, "msg": f"上游拦截: {error_msg}"}
                else:
                    return {"success": False, "msg": f"上游接口 HTTP 状态码异常: {resp.status}"}
                    
    except Exception as e:
        logging.error(f"❌ [Netts API] 能量派发请求发生异常: {e}")
        return {"success": False, "msg": f"网络请求超时或异常: {str(e)}"}


async def get_balance() -> float:
    """
    📡 财务雷达接口：向 Netts v2 API 发起网络请求获取当前账户真实 TRX 余额。
    供 monitor_task.py 财务巡航使用。
    """
    headers = {
        "X-API-KEY": NETTS_API_KEY,
        "X-Real-IP": SERVER_IP
    }
    
    # 基于文档指定的 V2 获取用户信息接口
    NETTS_USERINFO_URL = "https://netts.io/apiv2/userinfo"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(NETTS_USERINFO_URL, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    if data.get("status") == "success":
                        # 根据 v2 API 文档，余额存储在 stats -> balance 中
                        balance = data.get("stats", {}).get("balance", 0.0)
                        logging.info(f"📊 [Netts API] 成功获取真实余额: {balance} TRX")
                        return float(balance)
                    else:
                        error_msg = data.get("message", "未知业务错误")
                        logging.error(f"❌ [Netts API] 余额查询被拒: {error_msg}")
                        return 0.0
                elif resp.status == 401:
                    logging.error("❌ [Netts API] 余额查询 HTTP 401：API Key 无效或服务器 IP 未加入白名单！")
                    return 0.0
                elif resp.status == 429:
                    logging.warning("⚠️ [Netts API] 余额查询 HTTP 429：触发官方频率限制 (Rate Limit)！")
                    return 0.0
                else:
                    logging.error(f"❌ [Netts API] 获取余额失败，HTTP 状态码: {resp.status}")
                    return 0.0
                    
    except asyncio.TimeoutError:
        logging.error("❌ [Netts API] 余额探测网络请求超时 (TimeoutError)")
        return 0.0
    except aiohttp.ClientError as ce:
        logging.error(f"❌ [Netts API] 余额探测发生网络连接异常: {ce}")
        return 0.0
    except Exception as e:
        logging.error(f"❌ [Netts API] 余额探测发生未捕获异常: {e}")
        return 0.0
