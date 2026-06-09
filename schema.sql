-- xinnnnnn full schema
-- Fresh-install schema for MySQL/MariaDB.
-- Includes the fixes from 001_fix_deposit_precision_and_user_unique.sql.

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

CREATE TABLE `activation_codes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `code` varchar(32) NOT NULL,
  `duration_days` int(11) NOT NULL DEFAULT 30,
  `includes_special_energy` tinyint(1) DEFAULT 0,
  `is_used` tinyint(1) DEFAULT 0,
  `used_by_tg_id` varchar(50) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `used_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `block_scan_pointers` (
  `job_name` varchar(64) NOT NULL,
  `last_scanned_block` bigint(20) NOT NULL,
  `address` varchar(64) DEFAULT NULL,
  `asset_type` varchar(16) DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`job_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `system_configs` (
  `id` int(11) NOT NULL,
  `master_receive_address` varchar(34) NOT NULL,
  `global_special_address` varchar(34) DEFAULT NULL,
  `clone_bot_price_usdt` decimal(10,2) DEFAULT 0.00,
  `base_cost_65k` decimal(18,6) DEFAULT 0.000000,
  `base_cost_131k` decimal(18,6) DEFAULT 0.000000,
  `netts_cost_65k` decimal(18,6) DEFAULT 0.000000,
  `netts_cost_131k` decimal(18,6) DEFAULT 0.000000,
  `special_base_cost_65k` decimal(18,6) DEFAULT 0.000000,
  `special_base_cost_131k` decimal(18,6) DEFAULT 0.000000,
  `unactivated_fee_trx` decimal(18,6) DEFAULT 2.000000,
  `clone_fee_config` varchar(255) DEFAULT '30-29.9,365-299',
  `is_special_energy_global_enabled` tinyint(1) DEFAULT 1,
  `show_customer_service` tinyint(1) DEFAULT 1,
  `customer_service_link` varchar(255) DEFAULT NULL,
  `global_welcome_template` text DEFAULT NULL,
  `min_withdraw_amount` decimal(18,6) DEFAULT 100.000000,
  `zombie_tenant_days` int(11) DEFAULT 30,
  `tron_api_keys` varchar(1024) DEFAULT '',
  `special_auth_config` varchar(1024) DEFAULT '{}',
  `super_admin_tg_id` varchar(50) DEFAULT NULL,
  `netts_alert_threshold` decimal(18,2) DEFAULT 50.00,
  `tenant_alert_threshold` decimal(18,2) DEFAULT 15.00,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `tenants` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `owner_tg_id` bigint(20) NOT NULL,
  `bot_token` varchar(128) NOT NULL,
  `deposit_balance` decimal(18,6) DEFAULT 0.000000,
  `profit_balance` decimal(18,6) DEFAULT 0.000000,
  `withdraw_address` varchar(34) DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT 1,
  `expire_time` datetime NOT NULL,
  `last_active_time` datetime DEFAULT NULL,
  `markup_65k` decimal(18,6) DEFAULT 0.000000,
  `markup_131k` decimal(18,6) DEFAULT 0.000000,
  `has_special_energy_right` tinyint(1) DEFAULT 0,
  `show_special_energy` tinyint(1) DEFAULT 1,
  `special_energy_address` varchar(34) DEFAULT NULL,
  `markup_special` decimal(18,6) DEFAULT 0.000000,
  `created_at` datetime DEFAULT NULL,
  `is_banned` tinyint(1) DEFAULT 0,
  `special_price_65k` decimal(18,6) DEFAULT 0.000000,
  `special_price_131k` decimal(18,6) DEFAULT 0.000000,
  `special_energy_duration` varchar(10) NOT NULL DEFAULT '1h' COMMENT '特价能量下发时效',
  PRIMARY KEY (`id`),
  UNIQUE KEY `owner_tg_id` (`owner_tg_id`),
  UNIQUE KEY `bot_token` (`bot_token`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `users` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tenant_id` int(11) NOT NULL,
  `tg_user_id` bigint(20) NOT NULL,
  `tg_first_name` varchar(128) DEFAULT NULL,
  `balance` decimal(18,6) DEFAULT 0.000000,
  `withdraw_address` varchar(34) DEFAULT NULL,
  `total_orders` int(11) DEFAULT 0,
  `total_spent_trx` decimal(18,6) DEFAULT 0.000000,
  `default_receive_address_id` bigint(20) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_tenant_tg_user` (`tenant_id`,`tg_user_id`),
  KEY `tenant_id` (`tenant_id`),
  CONSTRAINT `users_ibfk_1` FOREIGN KEY (`tenant_id`) REFERENCES `tenants` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `user_receive_addresses` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `user_id` bigint(20) NOT NULL,
  `address` varchar(34) NOT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  CONSTRAINT `user_receive_addresses_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `micro_deposit_orders` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tenant_id` int(11) NOT NULL,
  `user_id` bigint(20) NOT NULL,
  `base_amount` int(11) NOT NULL,
  `fractional_amount` decimal(4,3) NOT NULL,
  `expected_amount` decimal(10,3) NOT NULL,
  `status` varchar(32) DEFAULT 'PENDING',
  `tx_hash` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `expired_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_micro_deposit_status_amount` (`status`,`expected_amount`),
  KEY `idx_micro_deposit_expired_at` (`expired_at`),
  KEY `idx_micro_deposit_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `withdraw_orders` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tenant_id` int(11) NOT NULL,
  `amount` decimal(18,6) NOT NULL,
  `target_address` varchar(34) NOT NULL,
  `status` varchar(32) DEFAULT 'PENDING',
  `tx_hash` varchar(64) DEFAULT NULL,
  `reject_reason` varchar(255) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `handled_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_withdraw_status` (`status`),
  KEY `idx_withdraw_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `energy_orders` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tenant_id` int(11) NOT NULL,
  `user_id` bigint(20) DEFAULT NULL,
  `order_type` varchar(32) NOT NULL COMMENT '订单场景分类',
  `target_address` varchar(34) NOT NULL,
  `admin_base_cost` decimal(18,6) DEFAULT 0.000000,
  `tenant_markup` decimal(18,6) DEFAULT 0.000000,
  `is_unactivated_fee_charged` tinyint(1) DEFAULT 0,
  `total_user_deducted` decimal(18,6) DEFAULT 0.000000,
  `status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT '派发状态',
  `tx_hash` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_energy_tenant_status` (`tenant_id`,`status`),
  KEY `idx_energy_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `processed_txs` (
  `tx_hash` varchar(64) NOT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`tx_hash`),
  KEY `idx_processed_txs_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `financial_daily_summaries` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `summary_date` date NOT NULL,
  `tenant_id` int(11) NOT NULL DEFAULT 0,
  `deposit_success_count` int(11) DEFAULT 0,
  `deposit_trx` decimal(18,6) DEFAULT 0.000000,
  `energy_success_count` int(11) DEFAULT 0,
  `energy_refund_count` int(11) DEFAULT 0,
  `energy_failed_count` int(11) DEFAULT 0,
  `energy_user_paid_trx` decimal(18,6) DEFAULT 0.000000,
  `energy_refund_trx` decimal(18,6) DEFAULT 0.000000,
  `admin_cost_trx` decimal(18,6) DEFAULT 0.000000,
  `tenant_profit_trx` decimal(18,6) DEFAULT 0.000000,
  `withdraw_paid_count` int(11) DEFAULT 0,
  `withdraw_paid_trx` decimal(18,6) DEFAULT 0.000000,
  `withdraw_rejected_count` int(11) DEFAULT 0,
  `withdraw_rejected_trx` decimal(18,6) DEFAULT 0.000000,
  `saas_paid_count` int(11) DEFAULT 0,
  `saas_paid_usdt` decimal(18,6) DEFAULT 0.000000,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_financial_daily_summary_date_tenant` (`summary_date`,`tenant_id`),
  KEY `idx_financial_daily_summary_date` (`summary_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `saas_orders` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `tg_user_id` bigint(20) NOT NULL,
  `order_type` varchar(32) NOT NULL,
  `days` varchar(32) NOT NULL,
  `price` decimal(10,2) NOT NULL,
  `status` varchar(32) DEFAULT 'PENDING',
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_saas_status_price` (`status`,`price`),
  KEY `idx_saas_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE `tron_api_nodes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `api_key` varchar(128) NOT NULL,
  `rpc_url` varchar(255) DEFAULT 'https://api.trongrid.io',
  `weight` int(11) DEFAULT 10,
  `is_active` tinyint(1) DEFAULT 1,
  `fail_count` int(11) DEFAULT 0,
  `last_used_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
