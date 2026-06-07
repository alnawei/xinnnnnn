# netts_api.py
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
                    logging.error(f"[Netts] 获取成本价失败，HTTP 状态码: {resp.status}")
                    return None
    except Exception as e:
        logging.error(f"[Netts] 网络探针异常: {e}")
        return None

async def fire_netts_silent(address: str, amount: int) -> bool:
    """
    🚨 静默发货引擎 (核心组件) 
    作为 SaaS 底层，无论发货成功与否，绝不抛出任何 Exception 阻断扫块中枢的主循环。
    """
    headers = {
        "X-API-KEY": NETTS_API_KEY,
        "X-Real-IP": SERVER_IP,
        "Content-Type": "application/json"
    }
    payload = {
        "address": address,
        "amount": amount
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(NETTS_ORDER_URL, json=payload, headers=headers, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # 兼容 Netts 的 JSON 响应，具体判断根据实际回调进行调整
                    if data.get("success") is True or data.get("code") == 0 or "order_id" in data:
                        logging.info(f"[Netts] 成功派发 -> 地址: {address} | 能量: {amount}")
                        return True
                    else:
                        logging.warning(f"[Netts] 订单被拒，响应数据: {data}")
                        return False
                else:
                    logging.error(f"[Netts] HTTP 请求异常: {resp.status}")
                    return False
    except Exception as e:
        logging.error(f"[Netts] 发货网络故障: {e}")
        return False
async def get_balance() -> float:
    """
    📡 财务雷达接口：向 Netts 发起网络请求获取当前账户真实 TRX 余额。
    """
    headers = {
        "X-API-KEY": NETTS_API_KEY,
        "X-Real-IP": SERVER_IP
    }
    
    # ⚠️ TODO: 等您拿到 Netts 官方查余额的真实 URL 后，请替换下方的假 URL 
    # NETTS_BALANCE_URL = "https://api.netts.com/v1/user/balance"
    
    try:
        # ===== 真实请求逻辑 (暂时注释，等填入真实 URL 后解开) =====
        # async with aiohttp.ClientSession() as session:
        #     async with session.get(NETTS_BALANCE_URL, headers=headers, timeout=10) as resp:
        #         if resp.status == 200:
        #             data = await resp.json()
        #             # 假设官方返回 {"code": 0, "data": {"balance": 150.5}}
        #             return float(data.get("data", {}).get("balance", 0.0))
        #         else:
        #             logging.error(f"[Netts] 获取余额失败，HTTP 状态码: {resp.status}")
        #             return 0.0
        
        # ===== 当前测试期的伪代码返回 (让监控雷达能跑通测试) =====
        return 45.0  # 故意返回低于 50 的数字，方便您测试触发超管 TG 报警
        
    except Exception as e:
        logging.error(f"[Netts] 余额探测网络异常: {e}")
        return 0.0
