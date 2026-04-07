# Shared Core

本目录是 `SharedAgentCore` 的 vendored 副本，用于给 OpsGraph 提供共享工作流运行时能力。

## 主要职责

- 工作流注册与执行
- Prompt 装配
- 工具执行器
- 状态、checkpoint、回放与 outbox 存储
- FastAPI 适配与通用 API 服务
- 鉴权、持久化、事件、测试支撑

## 目录说明

- `agent_platform/`：共享运行时源码
- `docs/`：共享层文档
- `tests/`：共享层测试
- `scripts/`：同步与 vendoring 脚本

## 使用方式

产品层通过各自的 `bootstrap.py` 调用共享层能力，不应在产品层重复实现工作流执行基础设施。
