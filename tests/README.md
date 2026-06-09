# 测试说明

这些测试分两类：

- 单元测试：不连接真实 Telegram、MySQL 或 Netts。
- 集成测试：连接一个专用测试数据库，真实创建表、插入数据、提交事务并检查状态流转。

## 安装测试依赖

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

## 运行单元测试

```bash
pytest tests/test_*.py
```

覆盖：

- TRON 地址 Base58Check 校验。
- 上游发货接口返回成功、明确失败、HTTP 503、超时的分类。
- 人工确认成功后的租户利润入账。
- 人工确认失败退款后的用户余额和消费统计回滚。

## 运行集成测试

集成测试必须使用专用测试库，不能使用生产库。`TEST_DATABASE_URL` 必须包含 `test` 字样，否则测试会自动跳过。

示例：

```bash
mysql -u root -p -e "CREATE DATABASE bot_test DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"
export TEST_DATABASE_URL='mysql+aiomysql://bot:你的密码@127.0.0.1:3306/bot_test?charset=utf8mb4'
pytest tests/integration
```

覆盖：

- TRX 充值订单是否能给用户余额入账。
- 同一个 tx_hash 是否不会重复入账。
- 同金额待支付订单是否只会核销一笔，防止重复上分。
- SaaS USDT 付款是否把订单变成 `PAID`。
- 失败退款是否按订单状态做到幂等，避免重复退款。
- 上游结果未知是否能把订单转入 `MANUAL_REVIEW`。
- 人工确认成功是否补记租户利润。
