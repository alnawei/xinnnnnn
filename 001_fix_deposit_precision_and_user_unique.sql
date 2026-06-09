-- 修复尾数充值精度：代码支持 3 位小数，数据库也必须保存 3 位。
ALTER TABLE micro_deposit_orders
  MODIFY fractional_amount DECIMAL(4,3) NOT NULL,
  MODIFY expected_amount DECIMAL(10,3) NOT NULL;

-- 防止同一个租户下重复创建同一个 Telegram 用户。
-- 如果执行时报 Duplicate entry，说明库里已经有重复数据，需要先清理重复 users。
ALTER TABLE users
  ADD UNIQUE KEY uq_users_tenant_tg_user (tenant_id, tg_user_id);
