class RegistryLookupError(KeyError):
    """Raised when a versioned registry entry does not exist."""


class RegistryConsistencyError(ValueError):
    """Raised when registry contents reference missing or incompatible entries."""


class PromptAssemblyError(ValueError):
    """Raised when prompt inputs cannot satisfy a bundle contract."""


class OutputValidationError(ValueError):
    """Raised when structured output does not satisfy the registered schema."""


class ToolAdapterNotRegisteredError(LookupError):
    """Raised when a tool adapter has not been registered for the resolved adapter type."""


class ToolExecutionError(RuntimeError):
    """Raised when tool execution or result validation fails."""


class NodeExecutionError(RuntimeError):
    """Raised when specialist node execution fails before a valid result is produced."""


class LangGraphUnavailableError(ImportError):
    """Raised when optional langgraph integration is requested but the package is unavailable."""


class FastAPIUnavailableError(ImportError):
    """Raised when optional FastAPI integration is requested but the package is unavailable."""
