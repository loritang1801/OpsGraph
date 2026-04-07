# OpsGraph Agent Context

- 日期：2026-03-30
- 产品：事件响应、建议审批、沟通发布、复盘与回放多智能体系统

## 已完成的设计层

- `PRD.md`
- `ARCHITECTURE.md`
- `DATABASE.md`
- `API.md`
- `WORKFLOW.md`
- `PROMPT_TOOL.md`
- `INTEGRATIONS.md`

## 当前实现结论

- 产品层代码位于 `src/opsgraph_app/`
- `bootstrap.py` 只装配 `opsgraph_incident_response` 与 `opsgraph_retrospective` 两个工作流
- `service.py` 已覆盖告警接入、事实与假设处理、建议与审批、沟通发布、关闭流转、postmortem、replay 与运行时诊断
- `routes.py` 已提供真实 FastAPI 路由，包括会话鉴权、成员管理、SSE、runtime-capabilities、远端 smoke 与回放接口
- `repository.py` 已持久化事件、事实、假设、建议、审批任务、沟通草稿、postmortem、replay case、replay run、评估报告与审计日志
- `worker.py` 已提供 replay worker 与 supervisor，含心跳、连续失败阈值与值班表相关支持
- `route_replay_monitor.py` 已提供产品管理员使用的 worker monitor 页面
- 产品模型网关支持本地与可选 OpenAI provider，并暴露是否允许失败回退
- `deployment.lookup`、`service_registry.lookup`、`runbook.search` 与 `comms_publish` 支持远端 provider 契约与 smoke 检测

## 当前实现边界

- 远端 provider 仍以可选集成为主，默认允许本地回退
- worker monitor 更偏运维诊断面，而不是完整运维平台
- replay 评估已经有语义检查，但还不是全量业务真值校验系统

## 建议续做方向

1. 继续提升 replay 评估的语义覆盖与回归基线治理
2. 把远端 provider 的严格模式、告警和执行保障做得更生产化
3. 扩展 recommendation/comms 的自动化编排与审批策略
4. 深化值班策略、监控页面与审计视图的运维整合

## 本地事实来源

共享运行时的本地源仍是 `D:\project\SharedAgentCore`。
