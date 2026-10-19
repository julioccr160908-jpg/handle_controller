"""Base action executor interface (Phase 3).

Defines the abstract interface that all action executors must implement.
Concrete executors (mouse, keyboard, media, windows) will be added in later phases.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from handcontrol.tracking.models import ActionRequest, ActionResult


class BaseExecutor(ABC):
    """Abstract base class for action executors."""

    @abstractmethod
    def execute(self, request: ActionRequest) -> ActionResult:
        """Execute an action and return the result."""
        ...

    @abstractmethod
    def release_all(self) -> None:
        """Release all held inputs (keys, buttons, drags).

        Called on hand loss, pause, stop, and emergency.
        """
        ...
