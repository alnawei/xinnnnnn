# xinnnnnn-main-fixed 修改说明

此文件夹是原项目的“修改版副本”，没有改动原目录 `/Users/x/Documents/Codex/2026-06-09/xinnnnnn-main/`。

## 修改文件

- `tron_scanner.py`
- `netts_api.py`
- `tron_utils.py`
- `routers/user.py`
- `routers/tenant.py`
- `routers/admin.py`
- `services/cleanup_task.py`
- `models.py`
- `schema.sql`
- `main.py`
- `MIGRATION_NOTES.md`

## 第一批核心修复

1. 修复 SaaS USDT 支付核销：克隆订单付款后等待用户提交 Bot Token，再创建/续费租户；避免缺 `bot_token` 的租户创建失败。
2. 修复特价直转金额比较：资金路径从 `float` 比较改为 `Decimal` 精确比较。
3. 修复散客充值跨租户串单：充值尾数全局排重，与扫块核销规则一致。
4. 修复余额购买退款幂等：退款前锁定 `PROCESSING` 订单，避免重复退款。
5. 修复租户本金充值死单：缺少代理老板 `User` 记录时自动创建。
6. 修复关停清理小错误：子机器人运行状态按三元组解包。

## 第二批增强修复

1. 扫块游标落库：TRX/USDT 地址游标写入 `block_scan_pointers`，降低重启漏单风险。
2. 并发尾数分配锁：充值尾数生成使用 MySQL `GET_LOCK/RELEASE_LOCK` 短锁。
3. 后台金额输入精度：套餐、预警金额从 `float` 改为 `Decimal`。
4. 过期订单保留审计：过期订单改标记 `EXPIRED`，不再物理删除。

## 第三批加固修复

1. 全局直营特价订单闭环
   - 超管全局兜底特价直转会创建 `EnergyOrder`。
   - 发货回执会更新为 `SUCCESS` 或 `FAILED_SILENT`，避免静默无账。

2. TRON 地址 Base58Check 校验
   - 新增 `tron_utils.py`。
   - 用户接收地址、租户提现地址、租户特价地址、超管主收款地址、超管全局特价地址都改为真实 Base58Check 校验。

3. 上游结果未知不自动退款
   - `delegate_energy` 区分明确失败与结果未知。
   - 超时、连接异常、部分 5xx/429 等会返回 `uncertain=True`。
   - 用户余额购买遇到结果未知时，订单进入 `MANUAL_REVIEW`，不立即退款，避免上游已发货但本地误退。

## 验证

已对修改版 Python 文件运行语法解析检查：通过。

## 第四批运营闭环修复

1. `MANUAL_REVIEW` 超管处理入口
   - 财务管理菜单新增“发货人工确认”。
   - 可查看最近 5 条 `MANUAL_REVIEW` 能量订单。
   - 每张卡片展示订单 ID、租户 ID、用户 ID、订单类型、扣款金额、目标地址和创建时间。

2. 人工确认成功
   - 超管核对上游确已发货后，可点击“确认已成功”。
   - 订单状态更新为 `SUCCESS`。
   - 若订单包含租户利润，会补记到租户 `profit_balance`。

3. 人工确认失败并退款
   - 超管核对上游确未发货后，可点击“确认失败并退款”。
   - 订单状态更新为 `FAILED_REFUNDED`。
   - 自动退回用户余额，并回滚用户消费笔数和消费金额。
   - 尝试通知用户退款结果。

## 第五批自动化测试补充

1. 新增测试依赖说明
   - 新增 `requirements-dev.txt`，包含 `pytest` 和 `pytest-asyncio`。
   - 新增 `tests/README.md`，说明如何安装依赖和运行测试。

2. 新增可测试账务工具
   - 新增 `accounting_utils.py`。
   - 将人工确认成功、确认失败退款的核心账务计算抽成纯函数，方便测试和复用。

3. 新增测试用例
   - `tests/test_tron_utils.py`：测试 TRON 地址 Base58Check 校验。
   - `tests/test_netts_api.py`：测试上游成功、明确失败、HTTP 503、超时的返回分类。
   - `tests/test_accounting_utils.py`：测试人工确认成功记利润、失败退款回滚余额与统计。

4. 验证情况
   - 当前环境未安装 `pytest`，无法直接运行完整测试套件。
   - 已对新增测试文件和相关模块运行 Python 语法解析检查：通过。

## 第六批测试前收尾加固

1. TronGrid 分页扫块
   - `tron_scanner.py` 新增通用分页请求函数。
   - TRX 和 USDT 交易查询都支持 `fingerprint` 翻页。
   - 每轮每个地址最多翻 5 页，降低单地址短时超过 200 笔导致漏单的风险。

2. 防重放哈希保留策略
   - `processed_txs.created_at` 增加索引，便于按时间清理。
   - 新增修改版 `db_cleanup.py`。
   - 防重放哈希保留期从 15 天延长到 180 天，并改为每批 1000 条分批删除。
