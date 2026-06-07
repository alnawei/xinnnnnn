# tasks.py
import asyncio
import logging
from decimal import Decimal, ROUND_UP
from sqlalchemy import update

from models import SystemConfig
from netts_api import get_netts_pricing

# ==================== 1. 专职算账函数 (负责拉取并计算只进不舍金额) ====================
async def fetch_netts_prices() -> tuple[Decimal, Decimal] | None:
    """从上游拉取最新定价，并使用财务级 [只进不舍] 规则，强制保留两位小数计算 65K 和 131K 的底价"""
    try:
        data = await get_netts_pricing()
        if data and (data.get("success") is True or data.get("code") == 0):
            periods = data.get("data", {}).get("services", {}).get("energy_1h", {}).get("periods", [])
            current_price_sun = None
            for p in periods:
                if p.get("is_current") is True:
                    current_price_sun = Decimal(str(p.get("price")))
                    break
            
            if current_price_sun is not None:
                raw_65k = (current_price_sun / Decimal("1000000")) * Decimal("65000")
                cost_65k = raw_65k.quantize(Decimal('0.01'), rounding=ROUND_UP)
                
                raw_131k = (current_price_sun / Decimal("1000000")) * Decimal("131000")
                cost_131k = raw_131k.quantize(Decimal('0.01'), rounding=ROUND_UP)
                
                return cost_65k, cost_131k
    except Exception as e:
        logging.error(f"拉取上游底价异常: {e}")
    return None


# ==================== 2. 价格守护协程 (负责死循环调用算账函数，写入数据库) ====================
async def auto_update_netts_price(session_maker):
    """
    🌐 Netts 上游底价自动同步守护协程 (写入专用采购成本字段)
    """
    logging.info("🚀 Netts 上游采购进货成本守护任务已独立启动...")
    
    while True:
        try:
            # 1. 自动调用算账函数，只进不舍获取最新成本
            prices = await fetch_netts_prices()
            
            if prices is not None:
                netts_65k, netts_131k = prices
                
                # 2. 异步刷写数据库 SystemConfig (仅更新 netts_cost，保留 base_cost)
                async with session_maker() as session:
                    await session.execute(
                        update(SystemConfig)
                        .where(SystemConfig.id == 1)
                        .values(
                            netts_cost_65k=netts_65k, 
                            netts_cost_131k=netts_131k
                        )
                    )
                    await session.commit()
                    
                logging.info(f"🔄 [进货价格巡航] 自动同步成功：65K成本={netts_65k:.2f} TRX, 131K成本={netts_131k:.2f} TRX")
                
        except Exception as e:
            logging.error(f"❌ [进货价格巡航] 遭遇异常 (已隔离继续运行): {str(e)}")
            
        await asyncio.sleep(60)
