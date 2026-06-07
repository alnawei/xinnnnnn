-- =========================================================
-- Telegram 多租户波场能量 SaaS 系统 - 生产级终极 DDL
-- 审计版本：v2.0 (Merge Live & Studio Template)
-- 字符集：utf8mb4 (完美支持 Telegram 各种 Emoji 表情)
-- 引擎：InnoDB (支持强事务，保障资产流转安全)
-- =========================================================

-- 关闭外键检查以允许无缝 Drop 和覆盖建表
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS `saas_tron_energy` 
DEFAULT CHARACTER SET utf8mb4 
COLLATE utf8mb4_general_ci;

USE `saas_tron_energy`;

-- ---------------------------------------------------------
-- 1. 系统全局配置表 (system_configs)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `system_configs`;
CREATE TABLE `system_configs` (
  `id` INT NOT NULL DEFAULT 1 COMMENT '单例控制，固定为1',
  
  -- 平台收款基建
  `master_receive_address` VARCHAR(34) NOT NULL COMMENT '全站主收款地址 - 用于尾数充值与SaaS收款',
  `global_special_address` VARCHAR(34) DEFAULT NULL COMMENT '绝对静默地址 - 未开通特权租户的兜底特价地址',
  
  -- 定价与底价设置
  `clone_bot_price_usdt` DECIMAL(10, 2) NOT NULL DEFAULT 0.00 COMMENT '开通/续费机器人的定价 (USDT, 旧版兼容)',
  `base_cost_65k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '超管扣除租户的平台抽水: 65K (TRX)',
  `base_cost_131k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '超管扣除租户的平台抽水: 131K (TRX)',
  `netts_cost_65k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '65K上游采购进货成本 (TRX)',
  `netts_cost_131k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '131K上游采购进货成本 (TRX)',
  `special_base_cost_65k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '65K全局特价售价 (TRX)',
  `special_base_cost_131k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '131K全局特价售价 (TRX)',
  `special_energy_base_cost` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '超管扣除租户的底价: 特价能量 (TRX, 旧版兼容)',
  `unactivated_fee_trx` DECIMAL(18, 6) NOT NULL DEFAULT 2.000000 COMMENT '未激活波场地址的额外激活费 (TRX)',
  
  -- 增值与套餐配置
  `clone_fee_config` VARCHAR(255) NOT NULL DEFAULT '30-29.9,365-299' COMMENT '克隆多套餐配置字符串',
  `special_auth_config` VARCHAR(1024) NOT NULL DEFAULT '{}' COMMENT '特价插件功能多套餐配置',
  
  -- 全局显隐与文案控制
  `is_special_energy_global_enabled` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '超管全局开关：特价功能 (1=显, 0=隐)',
  `show_customer_service` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '超管全局显隐控制：联系官方客服',
  `customer_service_link` VARCHAR(255) DEFAULT NULL COMMENT '全局客服链接',
  `global_welcome_template` TEXT COMMENT '全平台统一欢迎语模板',
  
  -- 财务与风控门槛
  `min_withdraw_amount` DECIMAL(18, 6) NOT NULL DEFAULT 100.000000 COMMENT '最低提现门槛 (TRX)',
  `zombie_tenant_days` INT NOT NULL DEFAULT 30 COMMENT '僵尸租户自动清理阈值 (天)',
  `tron_api_keys` VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '用于存储 TronGrid API 节点池（兼容旧版或备用）',
  
  -- 🚨 财务监控与安全预警 (最新增量字段)
  `super_admin_tg_id` VARCHAR(50) DEFAULT NULL COMMENT '超管接收报警与通知的纯数字 TG ID',
  `netts_alert_threshold` DECIMAL(18, 2) NOT NULL DEFAULT 50.00 COMMENT '超管 Netts 进货池余额报警阈值 (TRX)',
  `tenant_alert_threshold` DECIMAL(18, 2) NOT NULL DEFAULT 15.00 COMMENT '特价商铺(租户)本金不足催收阈值 (TRX)',

  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统全局配置核心表';

-- ---------------------------------------------------------
-- 2. 波场 API 高可用节点池 (tron_api_nodes)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `tron_api_nodes`;
CREATE TABLE `tron_api_nodes` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `api_key` VARCHAR(128) NOT NULL COMMENT 'TronGrid API Key',
  `rpc_url` VARCHAR(255) NOT NULL DEFAULT 'https://api.trongrid.io' COMMENT '节点请求RPC地址',
  `weight` INT NOT NULL DEFAULT 10 COMMENT '轮询权重',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用/未熔断',
  `fail_count` INT NOT NULL DEFAULT 0 COMMENT '连续失败次数，用于自动熔断',
  `last_used_at` DATETIME DEFAULT NULL COMMENT '最后活跃时间',
  PRIMARY KEY (`id`),
  KEY `idx_active` (`is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='波场API高可用节点池';

-- ---------------------------------------------------------
-- 3. 链上扫块指针表 (block_scan_pointers)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `block_scan_pointers`;
CREATE TABLE `block_scan_pointers` (
  `job_name` VARCHAR(64) NOT NULL COMMENT '扫块任务标识',
  `last_scanned_block` BIGINT NOT NULL COMMENT '最后成功处理的区块高度',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`job_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='链上扫块断点指针表';

-- ---------------------------------------------------------
-- 4. 核心租户表 (tenants)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `tenants`;
CREATE TABLE `tenants` (
  `id` INT NOT NULL AUTO_INCREMENT COMMENT '租户内网自增ID',
  `owner_tg_id` BIGINT NOT NULL COMMENT '租户老板的纯数字 TG ID',
  `bot_token` VARCHAR(128) NOT NULL COMMENT '租户专属 Telegram 机器人 Token',
  
  -- 资产分层隔离
  `deposit_balance` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '充值本金余额 (用于接单扣成本)',
  `profit_balance` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '赚取差价获利 (可提现)',
  `withdraw_address` VARCHAR(34) DEFAULT NULL COMMENT '租户 TRC20 提现收款地址',
  
  -- 生命周期与风控
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否存活(1=正常, 0=未激活/被冻结)',
  `is_banned` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '风控强封禁状态(1=封禁)',
  `expire_time` DATETIME NOT NULL COMMENT '机器人主程序授权到期时间',
  `last_active_time` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '防僵尸探测：最后交互时间',
  
  -- 基础能量加价
  `markup_65k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '65K能量加价利润',
  `markup_131k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '131K能量加价利润',
  
  -- 增值功能：特价能量设置
  `has_special_energy_right` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否已购买/开通特价功能特权',
  `show_special_energy` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '租户自主开关：C端展示特价面板',
  `special_energy_address` VARCHAR(34) DEFAULT NULL COMMENT '租户专属特价静默收款地址',
  `markup_special` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '旧版特价单档位加价 (废弃保留)',
  `special_price_65k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '65K 特价绝对售价 (0代表未开启)',
  `special_price_131k` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '131K 特价绝对售价 (0代表未开启)',
  
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_token` (`bot_token`),
  UNIQUE KEY `uk_owner_tg` (`owner_tg_id`),
  KEY `idx_status_expire` (`is_active`, `expire_time`),
  KEY `idx_has_special` (`has_special_energy_right`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='代理商(租户)核心表';

-- ---------------------------------------------------------
-- 5. C端消费者表 (users)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `tenant_id` INT NOT NULL COMMENT '归属租户ID',
  `tg_user_id` BIGINT NOT NULL COMMENT '散客消费者的纯数字 TG ID',
  `tg_first_name` VARCHAR(128) DEFAULT NULL COMMENT '用户昵称，用于欢迎语渲染',
  
  -- 资产与流水记录
  `balance` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '用户余额 (TRX)',
  `withdraw_address` VARCHAR(34) DEFAULT NULL COMMENT '备用或未来开放散户提现的地址',
  `total_orders` INT NOT NULL DEFAULT 0 COMMENT '在该租户下的累计下单数',
  `total_spent_trx` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '在该租户下的累计消费总额 (TRX)',
  
  -- 关系映射
  `default_receive_address_id` BIGINT DEFAULT NULL COMMENT '散客默认能量接收地址ID',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_tg_user` (`tenant_id`, `tg_user_id`),
  CONSTRAINT `fk_users_tenant` FOREIGN KEY (`tenant_id`) REFERENCES `tenants` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='消费者(散户)记录表';

-- ---------------------------------------------------------
-- 6. C端能量接收地址表 (user_receive_addresses)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `user_receive_addresses`;
CREATE TABLE `user_receive_addresses` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT NOT NULL COMMENT '归属的 C端 User ID',
  `address` VARCHAR(34) NOT NULL COMMENT '波场接收地址',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  
  PRIMARY KEY (`id`),
  KEY `idx_user_id` (`user_id`),
  CONSTRAINT `fk_addr_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='散户能量接收地址簿';

-- ---------------------------------------------------------
-- 7. 微小尾数充值订单表 (micro_deposit_orders)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `micro_deposit_orders`;
CREATE TABLE `micro_deposit_orders` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `tenant_id` INT NOT NULL COMMENT '充值归属的租户',
  `user_id` BIGINT NOT NULL COMMENT '充值的普通消费者ID',
  
  `base_amount` INT NOT NULL COMMENT '整数部分 (例：5)',
  `fractional_amount` DECIMAL(4, 3) NOT NULL COMMENT '动态分配的 2~3 位小数 (例：0.125)',
  `expected_amount` DECIMAL(10, 3) NOT NULL COMMENT '需精确打款的总额 (支持3位小数并发防撞库)',
  
  `status` ENUM('PENDING', 'SUCCESS', 'EXPIRED') NOT NULL DEFAULT 'PENDING' COMMENT '状态',
  `tx_hash` VARCHAR(64) DEFAULT NULL COMMENT '到账成功后的链上哈希',
  
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expired_at` DATETIME NOT NULL COMMENT '订单失效时间',
  
  PRIMARY KEY (`id`),
  -- 🚨 并发防串账虚拟列与唯一约束：确保同租户下的 PENDING 订单金额绝不撞车
  `pending_uk_helper` VARCHAR(50) AS (IF(`status` = 'PENDING', CONCAT(`tenant_id`, '-', CAST(`expected_amount` AS CHAR)), NULL)) VIRTUAL,
  UNIQUE KEY `uk_pending_tenant_amount` (`pending_uk_helper`),
  
  KEY `idx_tenant_user` (`tenant_id`, `user_id`),
  KEY `idx_status_expired` (`status`, `expired_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='微小尾数智能排重充值订单表';

-- ---------------------------------------------------------
-- 8. 提现审核订单表 (withdraw_orders)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `withdraw_orders`;
CREATE TABLE `withdraw_orders` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `tenant_id` INT NOT NULL COMMENT '申请提现的租户ID',
  `amount` DECIMAL(18, 6) NOT NULL COMMENT '提现金额',
  `target_address` VARCHAR(34) NOT NULL COMMENT '快照收款地址',
  
  `status` ENUM('PENDING', 'PAID', 'REJECTED') NOT NULL DEFAULT 'PENDING' COMMENT '审核状态',
  `tx_hash` VARCHAR(64) DEFAULT NULL COMMENT '链上打款TXID',
  `reject_reason` VARCHAR(255) DEFAULT NULL COMMENT '驳回原因',
  
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `handled_at` DATETIME DEFAULT NULL COMMENT '超管审批操作时间',
  
  PRIMARY KEY (`id`),
  KEY `idx_tenant_id` (`tenant_id`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='代理利润提现审核工单表';

-- ---------------------------------------------------------
-- 9. 核心交易派发订单表 (energy_orders)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `energy_orders`;
CREATE TABLE `energy_orders` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `tenant_id` INT NOT NULL COMMENT '出单所属租户',
  `user_id` BIGINT DEFAULT NULL COMMENT '散客用户ID (绝对静默打款则为空)',
  
  `order_type` ENUM('BALANCE_65K', 'BALANCE_131K', 'DIRECT_SPECIAL') NOT NULL COMMENT '订单场景分类',
  `target_address` VARCHAR(34) NOT NULL COMMENT '能量接收方波场地址',
  
  -- 费用明细结构 (精准对账)
  `admin_base_cost` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '超管收取的综合成本 (进货+抽水)',
  `tenant_markup` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '租户设定的纯利润 (进入profit_balance)',
  `is_unactivated_fee_charged` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否被加收了未激活附加费',
  `total_user_deducted` DECIMAL(18, 6) NOT NULL DEFAULT 0.000000 COMMENT '从散户侧扣除的总 TRX',
  
  -- 流转状态控制 (最新包含 PROCESSING 处理中状态)
  `status` ENUM('PENDING', 'SUCCESS', 'FAILED_REFUNDED', 'FAILED_SILENT', 'PROCESSING') NOT NULL DEFAULT 'PENDING' COMMENT '派发状态',
  `tx_hash` VARCHAR(64) DEFAULT NULL COMMENT '上游派发 TXID',
  
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  
  PRIMARY KEY (`id`),
  KEY `idx_tenant_created` (`tenant_id`, `created_at`),
  KEY `idx_user_created` (`user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能量派发流水与清算账本';

-- ---------------------------------------------------------
-- 10. 全网链上防双花拦截表 (processed_txs)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `processed_txs`;
CREATE TABLE `processed_txs` (
  `tx_hash` VARCHAR(64) NOT NULL COMMENT '波场链唯一TXID',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '扫块登记时间',
  PRIMARY KEY (`tx_hash`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='全局扫块防重放防双花缓存表';

-- ---------------------------------------------------------
-- 11. 代理激活码表 (activation_codes)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `activation_codes`;
CREATE TABLE `activation_codes` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `code` VARCHAR(32) NOT NULL COMMENT '授权卡密',
  `duration_days` INT NOT NULL DEFAULT 30 COMMENT '授权有效天数',
  `includes_special_energy` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否包含增值功能(特权)',
  `is_used` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否已核销',
  `used_by_tg_id` VARCHAR(50) DEFAULT NULL COMMENT '核销人的 TG ID',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `used_at` DATETIME DEFAULT NULL COMMENT '核销时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='超管后台下发的授权激活码表';

-- ---------------------------------------------------------
-- 12. SaaS 订阅开通/USDT 收银台订单表 (saas_orders)
-- ---------------------------------------------------------
DROP TABLE IF EXISTS `saas_orders`;
CREATE TABLE `saas_orders` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `tg_user_id` BIGINT NOT NULL COMMENT '下单买家的 TG ID',
  `order_type` VARCHAR(32) NOT NULL COMMENT '商品类型 (clone=主程序, special=增值插件)',
  `days` VARCHAR(32) NOT NULL COMMENT '购买天数',
  `price` DECIMAL(10, 2) NOT NULL COMMENT '带有防撞库尾数的实付 USDT 金额',
  `status` VARCHAR(32) NOT NULL DEFAULT 'PENDING' COMMENT '状态: PENDING/PAID/ACTIVATED',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_status_order_type` (`status`, `order_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SaaS授权/USDT尾数支付订单表';

-- 恢复外键安全检查
SET FOREIGN_KEY_CHECKS = 1;
