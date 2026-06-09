SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

-- ========================================================
-- 1. 表的结构 `system_configs` (系统核心全局配置表)
-- ========================================================
CREATE TABLE `system_configs` (
  `id` int(11) NOT NULL,
  `master_receive_address` varchar(34) NOT NULL COMMENT '主收款钱包地址',
  `global_special_address` varchar(34) DEFAULT NULL COMMENT '超管全局兜底特价地址',
  `clone_bot_price_usdt` decimal(10,2) DEFAULT NULL COMMENT '克隆机器人统一标价',
  `base_cost_65k` decimal(18,6) DEFAULT NULL COMMENT '平台 65K 能量抽水固定成本',
  `base_cost_131k` decimal(18,6) DEFAULT NULL COMMENT '平台 131K 能量抽水固定成本',
  `special_energy_base_cost` decimal(18,6) DEFAULT NULL,
  `unactivated_fee_trx` decimal(18,6) DEFAULT NULL COMMENT '波场未激活账户附加费',
  `is_special_energy_global_enabled` tinyint(1) DEFAULT NULL COMMENT '全局特价总开关',
  `show_customer_service` tinyint(1) DEFAULT NULL,
  `customer_service_link` varchar(255) DEFAULT NULL,
  `global_welcome_template` text DEFAULT NULL,
  `min_withdraw_amount` decimal(18,6) DEFAULT NULL,
  `zombie_tenant_days` int(11) DEFAULT NULL,
  `clone_fee_config` varchar(255) DEFAULT '30-29.9,365-299',
  `tron_api_keys` varchar(1024) DEFAULT '',
  `special_auth_config` varchar(1024) DEFAULT '{}',
  `netts_cost_65k` decimal(18,6) DEFAULT 0.000000 COMMENT '上游 Netts 65K 进货底价',
  `netts_cost_131k` decimal(18,6) DEFAULT 0.000000 COMMENT '上游 Netts 131K 进货底价',
  `special_base_cost_65k` decimal(18,6) DEFAULT 0.000000 COMMENT '超管直营自营售价 65K',
  `special_base_cost_131k` decimal(18,6) DEFAULT 0.000000 COMMENT '超管直营自营售价 131K',
  `super_admin_tg_id` varchar(50) DEFAULT NULL COMMENT '总资产超级管理员 TG ID',
  `netts_alert_threshold` decimal(18,2) DEFAULT 50.00 COMMENT '上游余额报警阈值',
  `tenant_alert_threshold` decimal(18,2) DEFAULT 15.00 COMMENT '代理商本金报警阈值',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 2. 表的结构 `tenants` (核心多租户/代理商商户表)
-- ========================================================
CREATE TABLE `tenants` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `owner_tg_id` bigint(20) NOT NULL COMMENT '代理商老板的 TG ID (支持超长ID)',
  `bot_token` varchar(128) NOT NULL COMMENT '独家子机器人的 API Token',
  `deposit_balance` decimal(18,6) DEFAULT 0.000000 COMMENT '代理商充值的进货本金池',
  `profit_balance` decimal(18,6) DEFAULT 0.000000 COMMENT '代理商赚取的利润分润池',
  `withdraw_address` varchar(34) DEFAULT NULL COMMENT '代理商绑定的提现波场地址',
  `is_active` tinyint(1) DEFAULT 0 COMMENT '机器人是否在有效期内运行',
  `expire_time` datetime NOT NULL COMMENT '授权到期时间',
  `last_active_time` datetime DEFAULT NULL,
  `markup_65k` decimal(18,6) DEFAULT NULL COMMENT '常规散户 65K 溢价利润加点',
  `markup_131k` decimal(18,6) DEFAULT NULL COMMENT '常规散户 131K 溢价利润加点',
  `has_special_energy_right` tinyint(1) DEFAULT 0 COMMENT '超管赋予的开通特价设置权开关',
  `show_special_energy` tinyint(1) DEFAULT 0,
  `special_energy_address` varchar(34) DEFAULT NULL COMMENT '代理商专属特价静默直转地址',
  `markup_special` decimal(18,6) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `is_banned` tinyint(1) DEFAULT 0 COMMENT '是否拉黑该代理商机器人',
  `special_price_65k` decimal(18,6) DEFAULT 0.000000 COMMENT '代理自定 65K 直转特价绝对值',
  `special_price_131k` decimal(18,6) DEFAULT 0.000000 COMMENT '代理自定 131K 直转特价绝对值',
  `special_energy_duration` varchar(10) NOT NULL DEFAULT '1h' COMMENT '特价能量派发时效 (5m 或 1h)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `owner_tg_id` (`owner_tg_id`),
  UNIQUE KEY `bot_token` (`bot_token`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 3. 表的结构 `users` (全网散户/消费者账户表)
-- ========================================================
CREATE TABLE `users` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tenant_id` int(11) NOT NULL COMMENT '所属代理商商户 ID',
  `tg_user_id` bigint(20) NOT NULL COMMENT '散户的 TG 账号 ID',
  `tg_first_name` varchar(128) DEFAULT NULL,
  `balance` decimal(18,6) DEFAULT 0.000000 COMMENT '散户在平台里的预存充值 TRX 余额',
  `withdraw_address` varchar(34) DEFAULT NULL,
  `total_orders` int(11) DEFAULT 0 COMMENT '总消费订单数',
  `total_spent_trx` decimal(18,6) DEFAULT 0.000000 COMMENT '总消费流水',
  `default_receive_address_id` bigint(20) DEFAULT NULL COMMENT '默认绑定的接收能量地址',
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `tenant_id` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 4. 表的结构 `energy_orders` (核心能量发货订单流水账本)
-- ========================================================
CREATE TABLE `energy_orders` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tenant_id` int(11) NOT NULL COMMENT '扣除进货本金的代理商 ID',
  `user_id` bigint(20) DEFAULT NULL COMMENT '操作的散户 ID',
  `order_type` enum('BALANCE_65K','BALANCE_131K','DIRECT_SPECIAL','DIRECT_SPECIAL_65K','DIRECT_SPECIAL_131K') NOT NULL COMMENT '场景分流分类',
  `target_address` varchar(34) NOT NULL COMMENT '能量接收方波场地址',
  `admin_base_cost` decimal(18,6) DEFAULT NULL COMMENT '记录当时扣除代理商的刚性总进货成本 (退款全靠它)',
  `tenant_markup` decimal(18,6) DEFAULT NULL COMMENT '代理商这笔单子赚取的净利润差价',
  `is_unactivated_fee_charged` tinyint(1) DEFAULT NULL,
  `total_user_deducted` decimal(18,6) DEFAULT NULL COMMENT '散户实际转入或扣除的总 TRX 金额',
  `status` enum('PENDING','SUCCESS','FAILED_REFUNDED','FAILED_SILENT','PROCESSING') NOT NULL DEFAULT 'PENDING' COMMENT '派发与退款状态机',
  `tx_hash` varchar(64) DEFAULT NULL COMMENT '上游发货成功返回的真实链上哈希',
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 5. 表的结构 `micro_deposit_orders` (微小尾数入账盲配充值流水表)
-- ========================================================
CREATE TABLE `micro_deposit_orders` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tenant_id` int(11) NOT NULL,
  `user_id` bigint(20) NOT NULL,
  `base_amount` int(11) NOT NULL COMMENT '用户打算充值的整数金额',
  `fractional_amount` decimal(4,3) NOT NULL COMMENT '系统分配的防撞击微小尾数',
  `expected_amount` decimal(10,3) NOT NULL COMMENT '精确的应到账金额 (雷达盲配唯一凭证)',
  `status` enum('PENDING','SUCCESS','EXPIRED') DEFAULT 'PENDING',
  `tx_hash` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `expired_at` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 6. 表的结构 `saas_orders` (代理商开通授权/克隆机器人订单表)
-- ========================================================
CREATE TABLE `saas_orders` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tg_user_id` bigint(20) NOT NULL,
  `order_type` varchar(32) NOT NULL COMMENT 'clone 机器人 或 plugin 插件购买',
  `days` varchar(32) NOT NULL COMMENT '开通时长天数',
  `price` decimal(10,2) NOT NULL COMMENT '支付的 USDT 金额',
  `status` varchar(32) DEFAULT 'PENDING',
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 7. 表的结构 `processed_txs` (全局防双花/防重复核销唯一哈希黑名单)
-- ========================================================
CREATE TABLE `processed_txs` (
  `tx_hash` varchar(64) NOT NULL COMMENT '已经核销过的波场链上 TXID',
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`tx_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 8. 表的结构 `activation_codes` (卡密授权码激活系统表)
-- ========================================================
CREATE TABLE `activation_codes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `code` varchar(32) NOT NULL,
  `duration_days` int(11) NOT NULL,
  `includes_special_energy` tinyint(1) DEFAULT NULL,
  `is_used` tinyint(1) DEFAULT 0,
  `used_by_tg_id` varchar(50) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `used_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 9. 表的结构 `block_scan_pointers` (老版块扫描高水位线存根，备用)
-- ========================================================
CREATE TABLE `block_scan_pointers` (
  `job_name` varchar(64) NOT NULL,
  `last_scanned_block` bigint(20) NOT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`job_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 10. 表的结构 `tron_api_nodes` (自适应熔断高可用节点池)
-- ========================================================
CREATE TABLE `tron_api_nodes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `api_key` varchar(128) NOT NULL,
  `rpc_url` varchar(255) DEFAULT 'https://api.trongrid.io',
  `weight` int(11) DEFAULT 1,
  `is_active` tinyint(1) DEFAULT 1,
  `fail_count` int(11) DEFAULT 0,
  `last_used_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 11. 表的结构 `user_receive_addresses` (散户地址快捷薄管理器)
-- ========================================================
CREATE TABLE `user_receive_addresses` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `user_id` bigint(20) NOT NULL,
  `address` varchar(34) NOT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 12. 表的结构 `withdraw_orders` (代理商分润提现财务审批单)
-- ========================================================
CREATE TABLE `withdraw_orders` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tenant_id` int(11) NOT NULL,
  `amount` decimal(18,6) NOT NULL,
  `target_address` varchar(34) NOT NULL,
  `status` enum('PENDING','PAID','REJECTED') DEFAULT 'PENDING',
  `tx_hash` varchar(64) DEFAULT NULL,
  `reject_reason` varchar(255) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `handled_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ========================================================
-- 13. 建立强一致性外键约束 (防脏数据破坏删库)
-- ========================================================
ALTER TABLE `users`
  ADD CONSTRAINT `users_ibfk_1` FOREIGN KEY (`tenant_id`) REFERENCES `tenants` (`id`) ON DELETE CASCADE;

ALTER TABLE `user_receive_addresses`
  ADD CONSTRAINT `user_receive_addresses_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
