from config import EXECUTOR_MAP
from errors import ExecutorMismatch


def check_executor(agent_id: str, action: str) -> None:
    expected = EXECUTOR_MAP.get(action)
    if expected is None:
        return
    if agent_id != expected:
        raise ExecutorMismatch(
            f"Action {action!r} must be executed by {expected!r}, "
            f"but requestor is {agent_id!r}"
        )
