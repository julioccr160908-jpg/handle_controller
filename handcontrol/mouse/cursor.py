"""Cursor controller — converts hand positions to cursor movements (Section 6).

Responsibilities:
1. Receive normalized hand landmark positions.
2. Map them through ``ActiveRegion`` to virtual screen coordinates.
3. Apply One Euro smoothing.
4. Apply dead zone (ignore sub-pixel jitter below threshold).
5. Apply max-speed clamp (prevents cursor teleporting on fast detection errors).
6. Track whether the clutch gesture (pointing) is active.
7. Freeze cursor on hand loss; require re-clutch to resume.

Returns ``CursorUpdate`` objects that the mouse executor can consume.

Independent of UI and Windows OS calls — this module contains no ctypes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from handcontrol.mouse.region import ActiveRegion
from handcontrol.mouse.smoother import OneEuro2D


@dataclass
class CursorConfig:
    """Tunable parameters for the cursor controller.

    Attributes:
        dead_zone_px: Movements smaller than this (in screen pixels) are ignored.
        max_speed_px_per_s: Maximum cursor displacement per second. Prevents jumps.
        smoothing_min_cutoff: One Euro min_cutoff (Hz). Lower = more smoothing.
        smoothing_beta: One Euro beta. Higher = faster response to fast motion.
    """

    dead_zone_px: float = 4.0
    max_speed_px_per_s: float = 3000.0
    smoothing_min_cutoff: float = 1.0
    smoothing_beta: float = 0.007


@dataclass
class CursorUpdate:
    """Output of one cursor controller tick.

    Attributes:
        x: Target screen X in virtual pixels.
        y: Target screen Y in virtual pixels.
        moved: True if x/y changed from the previous tick.
        clutch_active: True if the pointing gesture is active.
        frozen: True if cursor is frozen (hand lost).
    """

    x: float
    y: float
    moved: bool = False
    clutch_active: bool = False
    frozen: bool = False


class CursorController:
    """Translates hand positions to cursor updates.

    The cursor only moves while the clutch (pointing gesture) is active.
    On hand loss, the cursor is frozen until the hand reappears AND
    the clutch gesture is confirmed again.

    Parameters:
        region: The active camera-to-screen mapping region.
        config: Tunable cursor parameters.
    """

    def __init__(
        self,
        region: ActiveRegion | None = None,
        config: CursorConfig | None = None,
    ) -> None:
        self._region = region or ActiveRegion()
        self._config = config or CursorConfig()
        self._smoother = OneEuro2D(
            min_cutoff=self._config.smoothing_min_cutoff,
            beta=self._config.smoothing_beta,
        )
        self._last_x: float = float(self._region.screen_left + self._region.screen_width // 2)
        self._last_y: float = float(self._region.screen_top + self._region.screen_height // 2)
        self._last_timestamp: float = 0.0
        self._clutch_active = False
        self._frozen = True  # Starts frozen; requires clutch before moving.
        self._needs_reclutch = False  # Set on hand loss; cleared on new clutch.

    # ---------------------------------------------------------------- public

    @property
    def region(self) -> ActiveRegion:
        return self._region

    @region.setter
    def region(self, value: ActiveRegion) -> None:
        self._region = value

    @property
    def config(self) -> CursorConfig:
        return self._config

    @property
    def last_position(self) -> tuple[float, float]:
        """Last known cursor position in screen coordinates."""
        return (self._last_x, self._last_y)

    @property
    def clutch_active(self) -> bool:
        return self._clutch_active

    def update(
        self,
        norm_x: float,
        norm_y: float,
        clutch_on: bool,
        timestamp: float,
    ) -> CursorUpdate:
        """Process one frame of hand position data.

        Args:
            norm_x: Normalized X of the index finger tip (post-flip, [0, 1]).
            norm_y: Normalized Y of the index finger tip.
            clutch_on: Whether the pointing gesture is active this frame.
            timestamp: Monotonic timestamp of the frame.

        Returns:
            ``CursorUpdate`` with the target position and state flags.
        """
        self._clutch_active = clutch_on

        # Clutch released or lost.
        if not clutch_on:
            return CursorUpdate(
                x=self._last_x,
                y=self._last_y,
                moved=False,
                clutch_active=False,
                frozen=True,
            )

        # Clutch just reactivated after a loss — reset smoother to avoid jump.
        if self._needs_reclutch:
            self._needs_reclutch = False
            self._smoother.reset()
            # Initialize smoother at current position without moving.
            sx, sy = self._region.map_normalized(norm_x, norm_y)
            self._smoother.filter(sx, sy, timestamp)
            self._last_x = sx
            self._last_y = sy
            self._last_timestamp = timestamp
            return CursorUpdate(
                x=sx,
                y=sy,
                moved=False,
                clutch_active=True,
                frozen=False,
            )

        # Map to screen space.
        sx, sy = self._region.map_normalized(norm_x, norm_y)

        # Apply smoothing.
        sx, sy = self._smoother.filter(sx, sy, timestamp)

        # Max-speed clamp.
        dt = max(timestamp - self._last_timestamp, 1e-6)
        dx = sx - self._last_x
        dy = sy - self._last_y
        dist = math.hypot(dx, dy)
        max_dist = self._config.max_speed_px_per_s * dt
        if dist > max_dist and max_dist > 0:
            scale = max_dist / dist
            sx = self._last_x + dx * scale
            sy = self._last_y + dy * scale
            dist = max_dist

        # Dead zone.
        if dist < self._config.dead_zone_px:
            self._last_timestamp = timestamp
            return CursorUpdate(
                x=self._last_x,
                y=self._last_y,
                moved=False,
                clutch_active=True,
                frozen=False,
            )

        self._last_x = sx
        self._last_y = sy
        self._last_timestamp = timestamp

        return CursorUpdate(
            x=sx,
            y=sy,
            moved=True,
            clutch_active=True,
            frozen=False,
        )

    def on_hand_lost(self) -> None:
        """Call when the hand tracking is lost.

        Freezes the cursor and requires re-clutch before moving again.
        """
        self._clutch_active = False
        self._frozen = True
        self._needs_reclutch = True
        self._smoother.reset()

    def reset(self) -> None:
        """Full reset — call on stop or emergency."""
        self.on_hand_lost()
        self._last_x = float(self._region.screen_left + self._region.screen_width // 2)
        self._last_y = float(self._region.screen_top + self._region.screen_height // 2)
