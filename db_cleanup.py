# db_cleanup.py
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import delete

# 导入项目中现成的异步引擎和会话池、以及防重放模型
from models import engine, AsyncSessionLocal, ProcessedTx

# 配置独立的标准输出日志
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)

async def cleanup_historical_txs():
    """独立运维任务：物理抹除 15 天前的历史防双花哈希记录"""
    logging.info("🧹 [运维清理] 正在连接数据库，准备执行防重放历史数据清理...")
    
    try:
        async with AsyncSessionLocal() as session:
            # 计算 15 天前的时间阈值
            threshold_time = datetime.utcnow() - timedelta(days=15)
            
            logging.info(f"🔍 [运维清理] 正在检索 created_at < {threshold_time.strftime('%Y-%m-%d %H:%M:%S')} (UTC) 的记录...")
            
            # 构建批量物理删除 SQL
            stmt = delete(ProcessedTx).where(ProcessedTx.created_at < threshold_time)
            
            # 执行删除
            result = await session.execute(stmt)
            
            # 必须 commit 才会真实落盘生效
            await session.commit()
            
            deleted_count = result.rowcount
            if deleted_count > 0:
                logging.info(f"✅ [运维清理] 瘦身成功！物理删除了 {deleted_count} 条 15 天前的历史交易记录。")
            else:
                logging.info("✨ [运维清理] 数据库很干净，没有需要清理的 15 天前历史记录。")
                
    except Exception as e:
        logging.error(f"❌ [运维清理] 数据库清理任务发生严重异常: {e}", exc_info=True)
    finally:
        # 作为独立脚本，执行完毕后必须优雅释放底层引擎连接池
        await engine.dispose()
        logging.info("💤 [运维清理] 数据库连接池已释放，进程安全退出。")

if __name__ == "__main__":
    # 启动独立事件循环执行任务
    asyncio.run(cleanup_historical_txs())
