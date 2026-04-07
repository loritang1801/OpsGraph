# OpsGraph Prompt And Tooling

## 提示词装配

提示词装配仍由共享层 `PromptAssemblyService` 负责，OpsGraph 产品层主要提供事件响应场景所需的上下文。

当前主要上下文来源：

- 告警与事实集
- 部署查询结果
- 服务注册信息
- runbook 搜索结果
- 历史沟通与审批状态

## 产品工具适配器

`src/opsgraph_app/tool_adapters.py` 当前主要暴露：

- `deployment.lookup`
- `service_registry.lookup`
- `runbook.search`
- `comms_publish`

## Provider 策略

- 每类工具都可以配置为 `auto|local|http`
- 默认策略允许在远端 provider 出错时回退到本地启发式实现
- 可通过 `..._ALLOW_FALLBACK=false` 强制严格模式

## 当前实现状态

- runtime-capabilities 会暴露 provider 是否启用回退、策略来源、严格模式与最近错误
- smoke 接口与脚本会按契约发起探测，并保存执行历史
- 沟通发布会保留 accepted / published / failed 的真实结果区分
