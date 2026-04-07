# Shared Core Database

共享层数据库关注的是运行时状态，而不是产品业务表。

## 主要存储对象

- workflow state
- checkpoint
- replay record
- outbox event
- auth/session primitives

产品仓库可在同一 engine/session 上扩展自己的业务表与仓储。
