"""Simulation executor (Phase 3).

Implements ``BaseExecutor`` without touching any OS API.  All actions return
``ActionResult(status=SIMULATED)``.  This is the only executor active in Phase 3
and in safety mode — no real mouse clicks, keyboard presses, or window moves.

Independent of UI and Windows.
"""

from __future__ import annotations

import logging
import time

from handcontrol.actions.base import BaseExecutor
from handcontrol.tracking.models import ActionRequest, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class SimulatedExecutor(BaseExecutor):
    """Executor that simulates all actions without OS interaction.

    Records every request to an in-memory log so tests can inspect results.
    """

    def __init__(self) -> None:
        self._log: list[ActionResult] = []

    @property
    def log(self) -> list[ActionResult]:
        """All results produced since last ``clear_log`` call."""
        return list(self._log)

    def clear_log(self) -> None:
        """Clear the execution log."""
        self._log.clear()

    def execute(self, request: ActionRequest) -> ActionResult:
        """Simulate executing an action.

        Always returns SIMULATED status.  Logs the result internally.
        """
        result = ActionResult(
            action_name=request.action_name,
            status=ActionStatus.SIMULATED,
            reason=f"Simulated: {request.action_name}",
            timestamp=time.monotonic(),
        )
        self._log.append(result)
        logger.debug(
            "SIMULATED: %s params=%s source=%s",
            request.action_name,
            request.parameters,
            request.source_gesture,
        )
        return result

    def release_all(self) -> None:
        """No-op: simulation holds no real OS state."""
        logger.debug("SimulatedExecutor.release_all() called")
