"""Safety, simulation mode, and emergency stop (Phase 3).

``SafetyManager`` is the single place that tracks:
- Simulation mode (no OS interaction; all actions SIMULATED).
- Commands paused (gestures still detected but no actions fired).
- Emergency state (hard block on all actions; inputs released).

It coordinates the mapper pause flag and the controller emergency flag
so the rest of the code only needs to talk to one object.

Independent of UI and Windows.
"""

from __future__ import annotations

import logging

from handcontrol.actions.controller import ActionController
from handcontrol.mapping.profile import GestureMapper

logger = logging.getLogger(__name__)


class SafetyManager:
    """Coordinates safety state across mapper, controller, and gesture engine.

    Parameters:
        controller: The ``ActionController`` instance.
        mapper: The ``GestureMapper`` instance.
    """

    def __init__(
        self,
        controller: ActionController,
        mapper: GestureMapper,
    ) -> None:
        self._controller = controller
        self._mapper = mapper
        self._simulation_mode = True  # Phase 3: always starts in simulation.

    # ---------------------------------------------------------------- public

    @property
    def simulation_mode(self) -> bool:
        """True while all actions are simulated (no OS interaction)."""
        return self._simulation_mode

    @property
    def commands_paused(self) -> bool:
        """True while gesture commands are suppressed."""
        return self._mapper.commands_paused

    @property
    def is_emergency(self) -> bool:
        """True while emergency stop is active."""
        return self._controller.is_emergency

    def pause_commands(self) -> None:
        """Pause gesture-to-action translation (gestures still detected)."""
        self._mapper.commands_paused = True
        logger.info("Commands paused")

    def resume_commands(self) -> None:
        """Resume gesture-to-action translation."""
        self._mapper.commands_paused = False
        logger.info("Commands resumed")

    def toggle_pause(self) -> bool:
        """Toggle pause state.  Returns the new state (True = paused)."""
        new_state = not self._mapper.commands_paused
        self._mapper.commands_paused = new_state
        logger.info("Commands %s", "paused" if new_state else "resumed")
        return new_state

    def emergency_stop(self) -> None:
        """Activate emergency stop: block actions and release all inputs."""
        self._controller.emergency_stop()
        self._mapper.commands_paused = True

    def reset_emergency(self) -> None:
        """Clear emergency stop (does NOT auto-resume commands)."""
        self._controller.reset_emergency()
        logger.info("Emergency cleared — commands remain paused until manually resumed")

    def full_reset(self) -> None:
        """Full reset on stop: clear emergency, history, and mapper state."""
        self._controller.reset()
        self._mapper.reset_all()
        self._mapper.commands_paused = False
        logger.info("SafetyManager full reset")
