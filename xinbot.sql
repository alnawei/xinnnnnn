-- phpMyAdmin SQL Dump
-- version 5.2.3
-- https://www.phpmyadmin.net/
--
-- 主机： localhost
-- 生成日期： 2026-06-09 10:12:07
-- 服务器版本： 10.11.10-MariaDB-log
-- PHP 版本： 8.5.2

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- 数据库： `bot`
--

-- --------------------------------------------------------

--
-- 表的结构 `activation_codes`
--

CREATE TABLE `activation_codes` (
  `id` int(11) NOT NULL,
  `code` varchar(32) NOT NULL,
  `duration_days` int(11) NOT NULL,
  `includes_special_energy` tinyint(1) DEFAULT NULL,
  `is_used` tinyint(1) DEFAULT NULL,
  `used_by_tg_id` varchar(50) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `used_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `block_scan_pointers`
--

CREATE TABLE `block_scan_pointers` (
  `job_name` varchar(64) NOT NULL,
  `last_scanned_block` bigint(20) NOT NULL,
  `updated_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `energy_orders`
--

CREATE TABLE `energy_orders` (
  `id` bigint(20) NOT NULL,
  `tenant_id` int(11) NOT NULL,
  `user_id` bigint(20) DEFAULT NULL,
  `order_type` enum('BALANCE_65K','BALANCE_131K','DIRECT_SPECIAL','DIRECT_SPECIAL_65K','DIRECT_SPECIAL_131K') NOT NULL COMMENT '订单场景分类',
  `target_address` varchar(34) NOT NULL,
  `admin_base_cost` decimal(18,6) DEFAULT NULL,
  `tenant_markup` decimal(18,6) DEFAULT NULL,
  `is_unactivated_fee_charged` tinyint(1) DEFAULT NULL,
  `total_user_deducted` decimal(18,6) DEFAULT NULL,
  `status` enum('PENDING','SUCCESS','FAILED_REFUNDED','FAILED_SILENT','PROCESSING') NOT NULL DEFAULT 'PENDING' COMMENT '派发状态',
  `tx_hash` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `micro_deposit_orders`
--

CREATE TABLE `micro_deposit_orders` (
  `id` bigint(20) NOT NULL,
  `tenant_id` int(11) NOT NULL,
  `user_id` bigint(20) NOT NULL,
  `base_amount` int(11) NOT NULL,
  `fractional_amount` decimal(4,2) NOT NULL,
  `expected_amount` decimal(10,2) NOT NULL,
  `status` enum('PENDING','SUCCESS','EXPIRED') DEFAULT NULL,
  `tx_hash` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `expired_at` datetime NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `processed_txs`
--

CREATE TABLE `processed_txs` (
  `tx_hash` varchar(64) NOT NULL,
  `created_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `saas_orders`
--

CREATE TABLE `saas_orders` (
  `id` bigint(20) NOT NULL,
  `tg_user_id` bigint(20) NOT NULL,
  `order_type` varchar(32) NOT NULL,
  `days` varchar(32) NOT NULL,
  `price` decimal(10,2) NOT NULL,
  `status` varchar(32) DEFAULT 'PENDING',
  `created_at` datetime DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `system_configs`
--

CREATE TABLE `system_configs` (
  `id` int(11) NOT NULL,
  `master_receive_address` varchar(34) NOT NULL,
  `global_special_address` varchar(34) DEFAULT NULL,
  `clone_bot_price_usdt` decimal(10,2) DEFAULT NULL,
  `base_cost_65k` decimal(18,6) DEFAULT NULL,
  `base_cost_131k` decimal(18,6) DEFAULT NULL,
  `special_energy_base_cost` decimal(18,6) DEFAULT NULL,
  `unactivated_fee_trx` decimal(18,6) DEFAULT NULL,
  `is_special_energy_global_enabled` tinyint(1) DEFAULT NULL,
  `show_customer_service` tinyint(1) DEFAULT NULL,
  `customer_service_link` varchar(255) DEFAULT NULL,
  `global_welcome_template` text DEFAULT NULL,
  `min_withdraw_amount` decimal(18,6) DEFAULT NULL,
  `zombie_tenant_days` int(11) DEFAULT NULL,
  `clone_fee_config` varchar(255) DEFAULT '30-29.9,365-299',
  `tron_api_keys` varchar(1024) DEFAULT '',
  `special_auth_config` varchar(1024) DEFAULT '{}',
  `netts_cost_65k` decimal(18,6) DEFAULT 0.000000,
  `netts_cost_131k` decimal(18,6) DEFAULT 0.000000,
  `special_base_cost_65k` decimal(18,6) DEFAULT 0.000000,
  `special_base_cost_131k` decimal(18,6) DEFAULT 0.000000,
  `super_admin_tg_id` varchar(50) DEFAULT NULL,
  `netts_alert_threshold` decimal(18,2) DEFAULT 50.00,
  `tenant_alert_threshold` decimal(18,2) DEFAULT 15.00
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `tenants`
--

CREATE TABLE `tenants` (
  `id` int(11) NOT NULL,
  `owner_tg_id` bigint(20) NOT NULL,
  `bot_token` varchar(128) NOT NULL,
  `deposit_balance` decimal(18,6) DEFAULT NULL,
  `profit_balance` decimal(18,6) DEFAULT NULL,
  `withdraw_address` varchar(34) DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT NULL,
  `expire_time` datetime NOT NULL,
  `last_active_time` datetime DEFAULT NULL,
  `markup_65k` decimal(18,6) DEFAULT NULL,
  `markup_131k` decimal(18,6) DEFAULT NULL,
  `has_special_energy_right` tinyint(1) DEFAULT NULL,
  `show_special_energy` tinyint(1) DEFAULT NULL,
  `special_energy_address` varchar(34) DEFAULT NULL,
  `markup_special` decimal(18,6) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `is_banned` tinyint(1) DEFAULT 0,
  `special_price_65k` decimal(18,6) DEFAULT 0.000000,
  `special_price_131k` decimal(18,6) DEFAULT 0.000000,
  `special_energy_duration` varchar(10) NOT NULL DEFAULT '1h' COMMENT '特价能量下发时效'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `tron_api_nodes`
--

CREATE TABLE `tron_api_nodes` (
  `id` int(11) NOT NULL,
  `api_key` varchar(128) NOT NULL,
  `rpc_url` varchar(255) DEFAULT NULL,
  `weight` int(11) DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT NULL,
  `fail_count` int(11) DEFAULT NULL,
  `last_used_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `users`
--

CREATE TABLE `users` (
  `id` bigint(20) NOT NULL,
  `tenant_id` int(11) NOT NULL,
  `tg_user_id` bigint(20) NOT NULL,
  `tg_first_name` varchar(128) DEFAULT NULL,
  `balance` decimal(18,6) DEFAULT NULL,
  `withdraw_address` varchar(34) DEFAULT NULL,
  `total_orders` int(11) DEFAULT NULL,
  `total_spent_trx` decimal(18,6) DEFAULT NULL,
  `default_receive_address_id` bigint(20) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `user_receive_addresses`
--

CREATE TABLE `user_receive_addresses` (
  `id` bigint(20) NOT NULL,
  `user_id` bigint(20) NOT NULL,
  `address` varchar(34) NOT NULL,
  `created_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- 表的结构 `withdraw_orders`
--

CREATE TABLE `withdraw_orders` (
  `id` bigint(20) NOT NULL,
  `tenant_id` int(11) NOT NULL,
  `amount` decimal(18,6) NOT NULL,
  `target_address` varchar(34) NOT NULL,
  `status` enum('PENDING','PAID','REJECTED') DEFAULT NULL,
  `tx_hash` varchar(64) DEFAULT NULL,
  `reject_reason` varchar(255) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `handled_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 转储表的索引
--

--
-- 表的索引 `activation_codes`
--
ALTER TABLE `activation_codes`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `code` (`code`);

--
-- 表的索引 `block_scan_pointers`
--
ALTER TABLE `block_scan_pointers`
  ADD PRIMARY KEY (`job_name`);

--
-- 表的索引 `energy_orders`
--
ALTER TABLE `energy_orders`
  ADD PRIMARY KEY (`id`);

--
-- 表的索引 `micro_deposit_orders`
--
ALTER TABLE `micro_deposit_orders`
  ADD PRIMARY KEY (`id`);

--
-- 表的索引 `processed_txs`
--
ALTER TABLE `processed_txs`
  ADD PRIMARY KEY (`tx_hash`);

--
-- 表的索引 `saas_orders`
--
ALTER TABLE `saas_orders`
  ADD PRIMARY KEY (`id`);

--
-- 表的索引 `system_configs`
--
ALTER TABLE `system_configs`
  ADD PRIMARY KEY (`id`);

--
-- 表的索引 `tenants`
--
ALTER TABLE `tenants`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `owner_tg_id` (`owner_tg_id`),
  ADD UNIQUE KEY `bot_token` (`bot_token`);

--
-- 表的索引 `tron_api_nodes`
--
ALTER TABLE `tron_api_nodes`
  ADD PRIMARY KEY (`id`);

--
-- 表的索引 `users`
--
ALTER TABLE `users`
  ADD PRIMARY KEY (`id`),
  ADD KEY `tenant_id` (`tenant_id`);

--
-- 表的索引 `user_receive_addresses`
--
ALTER TABLE `user_receive_addresses`
  ADD PRIMARY KEY (`id`),
  ADD KEY `user_id` (`user_id`);

--
-- 表的索引 `withdraw_orders`
--
ALTER TABLE `withdraw_orders`
  ADD PRIMARY KEY (`id`);

--
-- 在导出的表使用AUTO_INCREMENT
--

--
-- 使用表AUTO_INCREMENT `activation_codes`
--
ALTER TABLE `activation_codes`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `energy_orders`
--
ALTER TABLE `energy_orders`
  MODIFY `id` bigint(20) NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `micro_deposit_orders`
--
ALTER TABLE `micro_deposit_orders`
  MODIFY `id` bigint(20) NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `saas_orders`
--
ALTER TABLE `saas_orders`
  MODIFY `id` bigint(20) NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `tenants`
--
ALTER TABLE `tenants`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `tron_api_nodes`
--
ALTER TABLE `tron_api_nodes`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `users`
--
ALTER TABLE `users`
  MODIFY `id` bigint(20) NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `user_receive_addresses`
--
ALTER TABLE `user_receive_addresses`
  MODIFY `id` bigint(20) NOT NULL AUTO_INCREMENT;

--
-- 使用表AUTO_INCREMENT `withdraw_orders`
--
ALTER TABLE `withdraw_orders`
  MODIFY `id` bigint(20) NOT NULL AUTO_INCREMENT;

--
-- 限制导出的表
--

--
-- 限制表 `users`
--
ALTER TABLE `users`
  ADD CONSTRAINT `users_ibfk_1` FOREIGN KEY (`tenant_id`) REFERENCES `tenants` (`id`) ON DELETE CASCADE;

--
-- 限制表 `user_receive_addresses`
--
ALTER TABLE `user_receive_addresses`
  ADD CONSTRAINT `user_receive_addresses_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
