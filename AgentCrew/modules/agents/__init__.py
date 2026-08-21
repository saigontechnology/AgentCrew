from .agent_runner import run_agent_loop
from .local_agent import LocalAgent
from .manager import AgentManager, AgentMode

__all__ = ["AgentManager", "AgentMode", "LocalAgent", "run_agent_loop"]


# Reduce the initial import cost by lazy load class
def __getattr__(name):
    if name == "RemoteAgent":
        from .remote_agent import RemoteAgent

        return RemoteAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
