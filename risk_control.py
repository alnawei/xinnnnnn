# risk_control.py
import aiohttp
import logging

async def check_tron_address_activated(address: str, api_key: str = None) -> bool:
    """
    【核心防御/备用探针】检测目标地址是否已在波场链上激活（是否有过交易/余额）
    可用于未来版本：如果在付款前查出是未激活地址，可额外多收 2 TRX。
    """
    url = f"https://api.trongrid.io/v1/accounts/{address}"
    headers = {
        "Accept": "application/json"
    }
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key.strip()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # 如果 data 数组为空，说明波场节点上没这个账户，是未激活的新地址
                    return len(data.get("data", [])) > 0
                else:
                    logging.warning(f"[激活检测] 节点返回 HTTP {resp.status}，为防卡死默认放行")
    except Exception as e:
        logging.error(f"[激活检测] 探针网络异常: {e}，默认放行")
        
    # Fail-Open: 节点爆炸时默认放行，优先保障正常业务流转
    return True
