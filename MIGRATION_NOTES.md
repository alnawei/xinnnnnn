# 数据库迁移提示

如果是全新安装，可以直接使用修改版 `schema.sql`。

如果是在已有数据库上升级，需要执行：

```sql
ALTER TABLE block_scan_pointers
  ADD COLUMN address varchar(64) DEFAULT NULL,
  ADD COLUMN asset_type varchar(16) DEFAULT NULL;

ALTER TABLE energy_orders
  MODIFY COLUMN status enum('PENDING','SUCCESS','FAILED_REFUNDED','FAILED_SILENT','PROCESSING','MANUAL_REVIEW')
  NOT NULL DEFAULT 'PENDING' COMMENT '派发状态';

ALTER TABLE processed_txs
  ADD INDEX idx_processed_txs_created_at (created_at);
```

说明：

- `block_scan_pointers.last_scanned_block` 当前保存的是 TronGrid 返回的毫秒时间戳游标，不是传统区块高度。
- 旧数据如果只有 `job_name` 和 `last_scanned_block` 也能继续保留；新代码会按地址和资产类型写入新的指针记录。
- `SaaSOrder.status='EXPIRED'` 是字符串状态，不需要改表结构。
- `EnergyOrder.status='MANUAL_REVIEW'` 用于上游请求超时、连接中断等“发货结果未知”的订单，避免自动退款造成资金损失。
