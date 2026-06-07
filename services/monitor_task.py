# services/monitor_task.py

import asyncio
import logging
from decimal import Decimal
from sqlalchemy import select

# 导入真实的项目模型
from models import Tenant, SystemConfig

async def run_financial_monitor(session_maker, bot, netts_service=None):
    """带状态记忆的智能财务巡航监控 (强类型防哑火排障版)"""
    import logging
    logging.info("📡 [Financial Monitor] 智能财务防轰炸监控雷达已启动...")
    
    netts_already_alerted = False
    netts_api_error_alerted = False  
    tenant_alert_states = {}  

    while True:
        try:
            async with session_maker() as session:
                config_stmt = select(SystemConfig).where(SystemConfig.id == 1)
                config = (await session.execute(config_stmt)).scalar_one_or_none()
                
                if not config:
                    logging.warning("⚠️ [监控警告] 未找到 SystemConfig 配置记录，跳过本次巡航。")
                    await asyncio.sleep(60)
                    continue
                
                super_admin_id = getattr(config, 'super_admin_tg_id', None)
                netts_threshold = float(getattr(config, 'netts_alert_threshold', 50.0))
                tenant_threshold = float(getattr(config, 'tenant_alert_threshold', 15.0))

                # ==========================================
                # 🚨 任务一：超管 Netts 真实余额与网络故障预警
                # ==========================================
                if not super_admin_id or str(super_admin_id).strip() in ["", "None"]:
                    logging.warning("⚠️ [Financial Monitor] 未配置 super_admin_tg_id，无法发送预警！")
                else:
                    try:
                        # 🛡️ 强制转换为整型，Aiogram 强烈依赖 INT 类型的 Chat ID 发送消息
                        admin_chat_id = int(str(super_admin_id).strip())
                        
                        if not netts_service:
                            raise ValueError("netts_service 服务未注入")
                            
                        netts_balance = await netts_service.get_balance() 
                        
                        if netts_api_error_alerted:
                            netts_api_error_alerted = False
                            try:
                                await bot.send_message(chat_id=admin_chat_id, text="🟢 <b>【网络恢复通知】</b> Netts 接口已恢复通讯，雷达重新挂载！", parse_mode="HTML")
                            except Exception as tg_err:
                                logging.error(f"❌ [Monitor] 恢复通知发送失败 (ID: {admin_chat_id}): {tg_err}", exc_info=True)

                        current_netts_bal = float(netts_balance)
                        
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
                                    # 独立拦截消息发送异常，防吞噬
                                    await bot.send_message(chat_id=admin_chat_id, text=alert_msg, parse_mode="HTML")
                                    netts_already_alerted = True
                                    logging.info("🚨 [Monitor] Netts 余额不足报警已成功发送！")
                                except Exception as e:
                                    logging.error(f"❌ [Monitor] 致命故障：向超管 ({admin_chat_id}) 推送预警消息失败！", exc_info=True)
                        else:
                            if netts_already_alerted:
                                netts_already_alerted = False
                                try:
                                    await bot.send_message(chat_id=admin_chat_id, text="✅ <b>【资金恢复正常】</b> Netts 能量池余额已充足，预警雷达重新挂载！", parse_mode="HTML")
                                    logging.info("✅ [Monitor] Netts 余额恢复正常，警报已解除。")
                                except Exception as e:
                                    logging.error(f"❌ [Monitor] 恢复消息推送失败: {e}", exc_info=True)
                                
                    except ValueError as ve:
                        logging.error(f"❌ [Monitor] ID 数据类型异常，super_admin_tg_id 必须是纯数字: {ve}")
                    except Exception as netts_err:
                        # 拦截 API 异常
                        if not netts_api_error_alerted:
                            logging.error(f"❌ [Monitor] Netts 请求异常: {netts_err}")
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
                    
                    if current_balance < Decimal(str(tenant_threshold)):
                        if not tenant_alert_states.get(tenant_id, False):
                            try:
                                tenant_chat_id = int(str(tenant_tg_id).strip())
                                reminder_msg = (
                                    f"🔔 <b>【商铺本金不足提醒】</b>\n\n"
                                    f"🤖 您的全自动能量机器人 (租户ID: #{tenant.id}) 进货本金已跌破预警线！\n"
                                    f"💳 当前可用本金：<code>{float(current_balance):g}</code> TRX\n"
                                    f"🛑 最低预警线：<code>{tenant_threshold:g}</code> TRX\n\n"
                                    f"<i>为保证自动发货，请及时充值。恢复前不再重复提醒。</i>"
                                )
                                await bot.send_message(chat_id=tenant_chat_id, text=reminder_msg, parse_mode="HTML")
                                tenant_alert_states[tenant_id] = True
                                await asyncio.sleep(0.5)
                            except Exception as tg_err:
                                logging.warning(f"⚠️ 无法向租户 #{tenant.id} 发送催收通知: {tg_err}")
                    else:
                        if tenant_alert_states.get(tenant_id, False):
                            tenant_alert_states[tenant_id] = False

        except Exception as e:
            logging.error(f"❌ [Financial Monitor] 财务监控巡航任务出现全局异常: {e}", exc_info=True)
            
        await asyncio.sleep(1800)
