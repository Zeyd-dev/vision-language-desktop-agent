from .base import VLMBackend, AgentAction
from .claude_backend import ClaudeBackend
from .gemini_backend import GeminiBackend

__all__ = ["VLMBackend", "AgentAction", "ClaudeBackend", "GeminiBackend", "get_backend"]

_BACKENDS = {
    "claude": ClaudeBackend,
    "gemini": GeminiBackend,
}


def get_backend(name: str) -> VLMBackend:
    """
    Look up a backend by name ("claude" or "gemini") and construct it.
    This is the one place that needs to know both backend classes exist —
    agent.py just calls get_backend(name) and gets back something that
    satisfies VLMBackend, without caring which one it is.
    """
    try:
        backend_cls = _BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"Unknown backend '{name}'. Choose one of: {sorted(_BACKENDS)}"
        ) from None
    return backend_cls()
