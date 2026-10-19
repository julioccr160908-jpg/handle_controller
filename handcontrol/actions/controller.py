"""Central action controller (Section 4.7).

The ``ActionController`` is the single gateway between gesture events and
action execution.  It:

1. Passes ``ActionRequest`` through the ``Allowlist`` (REFUSED if not listed).
2. Forwards allowed requests to the active ``BaseExecutor``.
3. Records all results in a bounded event history.
4. Handles emergency stop: cancels all active gestures, releases inputs,
   and blocks all further actions until reset.

Thread-safety: ``submit`` may be called from the processing thread.
The history lock ensures safe concurrent access.

Independent of UI and Windows.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from handcontrol.actions.allowlist import Allowlist
from handcontrol.actions.base import BaseExecutor
from handcontrol.actions.simulation import SimulatedExecutor
from handcontrol.tracking.models import ActionRequest, ActionResult, ActionStatus

logger = logging.getLogger(__name__)

# Maximum number of history entries kept in memory.
_HISTORY_MAXLEN = 200


class ActionController:
    """Validates, executes, and records all action requests.

    Parameters:
        executor: The ``BaseExecutor`` to use.  Defaults to ``SimulatedExecutor``.
        allowlist: The ``Allowlist`` to validate requests against.
        history_maxlen: Maximum action history entries.
    """

    def __init__(
        self,
        executor: BaseExecutor | None = None,
        allowlist: Allowlist | None = None,
        history_maxlen: int = _HISTORY_MAXLEN,
    ) -> None:
        self._executor: BaseExecutor = executor if executor is not None else SimulatedExecutor()
        self._allowlist = allowlist if allowlist is not None else Allowlist()
        self._history: deque[ActionResult] = deque(maxlen=history_maxlen)
        self._history_lock = threading.Lock()
        self._emergency = False
        self._emergency_lock = threading.Lock()

    # ---------------------------------------------------------------- public

    @property
    def is_emergency(self) -> bool:
        """Whether the emergency stop is currently active."""
        with self._emergency_lock:
            return self._emergency

    @property
    def history(self) -> list[ActionResult]:
        """Snapshot of the action history (newest last)."""
        with self._history_lock:
            return list(self._history)

    def submit(self, request: ActionRequest) -> ActionResult:
        """Submit an action request for validation and execution.

        1. REFUSED if emergency is active.
        2. REFUSED if action is not on the allowlist.
        3. REFUSED if parameters are invalid.
        4. Forwarded to the executor otherwise.

        Args:
            request: The action request to process.

        Returns:
            ``ActionResult`` with the outcome.
        """
        now = time.monotonic()

        # Emergency block.
        with self._emergency_lock:
            if self._emergency:
                result = ActionResult(
                    action_name=request.action_name,
                    status=ActionStatus.REFUSED,
                    reason="Emergency stop is active.",
                    timestamp=now,
                )
                self._record(result)
                logger.warning("REFUSED (emergency): %s", request.action_name)
                return result

        # Allowlist check.
        errors = self._allowlist.validate(request.action_name, request.parameters)
        if errors:
            reason = "; ".join(errors)
            result = ActionResult(
                action_name=request.action_name,
                status=ActionStatus.REFUSED,
                reason=reason,
                timestamp=now,
            )
            self._record(result)
            logger.warning("REFUSED: %s — %s", request.action_name, reason)
            return result

        # Execute.
        try:
            result = self._executor.execute(request)
        except Exception as exc:
            result = ActionResult(
                action_name=request.action_name,
                status=ActionStatus.FAILED,
                reason=str(exc),
                timestamp=now,
            )
            logger.exception("FAILED: %s", request.action_name)

        self._record(result)
        return result

    def submit_many(self, requests: list[ActionRequest]) -> list[ActionResult]:
        """Submit multiple requests, returning results in order."""
        return [self.submit(r) for r in requests]

    def emergency_stop(self) -> None:
        """Activate emergency stop.

        - Sets the emergency flag (all subsequent requests are REFUSED).
        - Calls ``release_all`` on the executor (releases keys/buttons/drags).
        - Records the event in history.
        """
        with self._emergency_lock:
            self._emergency = True

        try:
            self._executor.release_all()
        except Exception:
            logger.exception("Error during emergency release_all")

        result = ActionResult(
            action_name="__emergency_stop__",
            status=ActionStatus.REFUSED,
            reason="Emergency stop activated.",
            timestamp=time.monotonic(),
        )
        self._record(result)
        logger.warning("⚠ EMERGENCY STOP activated")

    def reset_emergency(self) -> None:
        """Deactivate emergency stop and allow actions again."""
        with self._emergency_lock:
            self._emergency = False
        logger.info("Emergency stop cleared")

    def reset(self) -> None:
        """Full reset: clear emergency, release inputs, clear history."""
        self.reset_emergency()
        try:
            self._executor.release_all()
        except Exception:
            logger.exception("Error during reset release_all")
        with self._history_lock:
            self._history.clear()
        logger.info("ActionController reset")

    def swap_executor(self, executor: BaseExecutor) -> None:
        """Replace the active executor at runtime (e.g. sim → real)."""
        try:
            self._executor.release_all()
        except Exception:
            logger.exception("Error releasing old executor")
        self._executor = executor
        logger.info("Executor swapped to %s", type(executor).__name__)

    # --------------------------------------------------------------- private

    def _record(self, result: ActionResult) -> None:
        with self._history_lock:
            self._history.append(result)
