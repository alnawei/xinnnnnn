# 测试说明

这些测试用于覆盖资金安全相关的核心规则，不需要连接真实 Telegram、MySQL 或 Netts。

## 安装测试依赖

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

## 运行测试

```bash
pytest tests
```

## 当前覆盖

- TRON 地址 Base58Check 校验。
- 上游发货接口返回成功、明确失败、HTTP 503、超时的分类。
- 人工确认成功后的租户利润入账。
- 人工确认失败退款后的用户余额和消费统计回滚。
