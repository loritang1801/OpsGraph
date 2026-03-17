from __future__ import annotations

import sys
from pathlib import Path


def load_shared_agent_platform():
    try:
        import shared_core.agent_platform as agent_platform  # type: ignore

        return agent_platform
    except ImportError:
        workspace_shared = Path(__file__).resolve().parents[3] / "SharedAgentCore"
        if str(workspace_shared) not in sys.path:
            sys.path.insert(0, str(workspace_shared))
        import agent_platform  # type: ignore

        return agent_platform
