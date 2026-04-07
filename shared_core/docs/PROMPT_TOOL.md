# Shared Core Prompt And Tool

共享层负责：

- Prompt catalog 与装配服务
- 工具注册与执行抽象
- 节点运行时与模型网关接口

产品层负责：

- 提供领域上下文
- 注册产品工具适配器
- 提供产品专属模型网关或 provider 策略
