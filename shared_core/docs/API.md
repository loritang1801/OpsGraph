# Shared Core API

共享层提供的是通用 API 服务能力，而不是某个产品的完整业务接口。

## 主要能力

- 通用工作流列表与工作流状态查询
- 统一成功 envelope 与错误封装
- FastAPI 生命周期挂载
- 与运行时存储绑定的 API service

产品仓库通常在此基础上继续挂载自己的 `/api/v1/<product>` 路由。
