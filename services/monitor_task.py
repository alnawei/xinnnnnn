# services/monitor_task.py

import asyncio
import logging
from decimal import Decimal
from sqlalchemy import select

# 导入项目模型
from models import Tenant, SystemConfig

async def run_financial_monitor(session_maker, bot, netts_service=None):
    """生产级智能财务巡航监控 (无痕静默版)"""
    logging.info("📡 [Financial Monitor] 智能财务防轰炸监控雷达已启动，轮询周期: 60秒...")
    
    # 🧠 状态记忆本：用于“防轰炸气囊”
    netts_already_alerted = False
    netts_api_error_alerted = False  
    tenant_alert_states = {}  

    while True:
        try:
            async with session_maker() as session:
                # 0. 动态获取系统最新配置
                config_stmt = select(SystemConfig).where(SystemConfig.id == 1)
                config = (await session.execute(config_stmt)).scalar_one_or_none()
                
                if not config:
                    await asyncio.sleep(60)
                    continue
                
                # 精准提取预警参数
                super_admin_id = getattr(config, 'super_admin_tg_id', None)
                netts_threshold = float(getattr(config, 'netts_alert_threshold', 50.0))
                tenant_threshold = float(getattr(config, 'tenant_alert_threshold', 15.0))

                # ==========================================
                # 🚨 任务一：超管 Netts 真实余额智能预警
                # ==========================================
                if super_admin_id and str(super_admin_id).strip() not in ["", "None"]:
                    try:
                        admin_chat_id = int(str(super_admin_id).strip())
                        
                        if netts_service:
                            # 调用真实网关获取余额
                            netts_balance = await netts_service.get_balance() 
                            
                            # 优雅处理网络异常返回的 None 值
                            if netts_balance is None:
                                raise ValueError("网关响应为空")

                            current_netts_bal = float(netts_balance)

                            # 若之前断网报过警，现在恢复了，则发送恢复通知
                            if netts_api_error_alerted:
                                netts_api_error_alerted = False
                                try:
                                    await bot.send_message(chat_id=admin_chat_id, text="🟢 <b>【网络恢复通知】</b> Netts 接口已恢复通讯，雷达重新挂载！", parse_mode="HTML")
                                except Exception:
                                    pass

                            # 核心判定：跌破防线
                            if current_netts_bal < netts_threshold:
                                if not netts_already_alerted:
                                    alert_msg = (
                                        f"🔴 <b>【最高级别财务预警】</b>\n\n"
                                        f"⚠️ <b>您的 Netts 上游能量池余额已跌破预警线！</b>\n"
                                        f"💳 当前真实余额：<code>{current_netts_bal}</code> TRX\n"
                                        f"🛑 当前配置阈值：<code>{netts_threshold}</code> TRX\n\n"
                                        f"<i>系统已开启静默，在充值恢复前不会再次打扰。</i>"
                                    )
                                    try:
                                        await bot.send_message(chat_id=admin_chat_id, text=alert_msg, parse_mode="HTML")
                                        netts_already_alerted = True
                                        logging.warning("🚨 [Monitor] Netts 余额不足，预警短信已送达超管！")
                                    except Exception as e:
                                        logging.error(f"❌ [Monitor] 发送预警失败: {e}")
                                # 如果 netts_already_alerted == True，则严格保持静默，不刷屏
                            
                            else:
                                # 核心判定：资金充足，如果处于预警状态则解除警报
                                if netts_already_alerted:
                                    netts_already_alerted = False
                                    try:
                                        await bot.send_message(
                                            chat_id=admin_chat_id, 
                                            text="✅ <b>【资金恢复正常】</b> Netts 能量池余额已充足，预警雷达重新挂载！", 
                                            parse_mode="HTML"
                                        )
                                        logging.info("✅ [Monitor] 余额已恢复，解除静默气囊。")
                                    except Exception:
                                        pass
                                        
                    except Exception as netts_err:
                        # 智能降级：仅在网络第一次断线时静默记录日志，不轰炸控制台
                        if not netts_api_error_alerted:
                            logging.error(f"⚠️ [Monitor] Netts 请求异常进入静默期: {netts_err}")
                            netts_api_error_alerted = True

                # ==========================================
                # 🔔 任务二：特价租户余额防打扰催收
                # ==========================================
                active_tenants_stmt = select(Tenant).where(
                    Tenant.is_active == True,
                    Tenant.has_special_energy_right == True
                )
                active_tenants = (await session.execute(active_tenants_stmt)).scalars().all()

                for tenant in active_tenants:
                    tenant_id = tenant.id
                    tenant_tg_id = tenant.owner_tg_id
                    current_balance = tenant.deposit_balance
                    
                    if current_balance is not None and current_balance < Decimal(str(tenant_threshold)):
                        if not tenant_alert_states.get(tenant_id, False):
                            try:
                                tenant_chat_id = int(str(tenant_tg_id).strip())
                                reminder_msg = (
                                    f"🔔 <b>【商铺本金不足提醒】</b>\n\n"
                                    f"🤖 您的全自动能量机器人进货本金已跌破预警线！\n"
                                    f"💳 当前可用本金：<code>{float(current_balance):g}</code> TRX\n"
                                    f"🛑 最低预警线：<code>{tenant_threshold:g}</code> TRX\n\n"
                                    f"<i>为保证自动发货，请及时充值。恢复前不再重复提醒。</i>"
                                )
                                await bot.send_message(chat_id=tenant_chat_id, text=reminder_msg, parse_mode="HTML")
                                tenant_alert_states[tenant_id] = True
                                # 节流阀：防并发下线请求瞬间打爆 Telegram API
                                await asyncio.sleep(0.5)
                            except Exception:
                                pass
                    else:
                        # 余额恢复：重置该租户的报警气囊
                        if tenant_alert_states.get(tenant_id, False):
                            tenant_alert_states[tenant_id] = False

        except Exception as e:
            # 最外层防崩兜底拦截
            logging.error(f"❌ [Financial Monitor] 巡航期间发生未捕获异常: {e}", exc_info=True)
            
        # 挂起协程，每 30分钟 执行一次全站轮询
        await asyncio.sleep(1800)
