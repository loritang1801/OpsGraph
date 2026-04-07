# Shared Core Architecture

共享层采用模块化运行时结构：

- `workflow_definitions.py`：工作流定义
- `workflow_registry.py`：工作流注册表
- `workflow_runner.py`：工作流执行器
- `service.py` 与 `api_service.py`：通用服务层
- `tool_executor.py`：工具执行
- `persistence.py`、`sqlalchemy_stores.py`：持久化
- `replay.py`、`file_replay_store.py`：回放
- `fastapi_adapter.py`：FastAPI 集成

设计目标是让产品层只补领域能力，而不重写通用工作流基础设施。
