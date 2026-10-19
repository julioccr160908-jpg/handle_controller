"""Active region mapping — camera frame ↔ virtual screen space (Section 6).

Defines a rectangular region within the camera frame that maps to the total
virtual screen area. Coordinates outside the region still clamp to the edges
so the user can always reach screen corners without extreme hand movement.

The default region is a centered rectangle with configurable margins, meaning
the hand only needs to travel to ~80% of the frame edges to reach the screen edges.

All computations are independent of UI and Windows OS calls.
The ``ActiveRegion`` class is a pure data-transform object.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ActiveRegion:
    """Defines the camera sub-region that maps to the full virtual screen.

    Attributes:
        frame_width: Camera frame width in pixels.
        frame_height: Camera frame height in pixels.
        left_frac: Left edge of the region as a fraction of frame_width [0, 1].
        top_frac: Top edge of the region as a fraction of frame_height [0, 1].
        right_frac: Right edge of the region as a fraction of frame_width [0, 1].
        bottom_frac: Bottom edge of the region as a fraction of frame_height [0, 1].
        screen_left: Virtual screen left edge (may be negative in multi-monitor).
        screen_top: Virtual screen top edge (may be negative in multi-monitor).
        screen_width: Total virtual screen width in pixels.
        screen_height: Total virtual screen height in pixels.
    """

    frame_width: int = 640
    frame_height: int = 480
    # Default: 10 % margin on each side → 80 % of the frame is the active region.
    left_frac: float = 0.10
    top_frac: float = 0.10
    right_frac: float = 0.90
    bottom_frac: float = 0.90
    screen_left: int = 0
    screen_top: int = 0
    screen_width: int = 1920
    screen_height: int = 1080

    # ---------------------------------------------------------------- public

    @property
    def pixel_rect(self) -> tuple[int, int, int, int]:
        """Return (left, top, right, bottom) in camera frame pixels."""
        return (
            int(self.left_frac * self.frame_width),
            int(self.top_frac * self.frame_height),
            int(self.right_frac * self.frame_width),
            int(self.bottom_frac * self.frame_height),
        )

    @property
    def normalized_rect(self) -> tuple[float, float, float, float]:
        """Return (left_frac, top_frac, right_frac, bottom_frac)."""
        return (self.left_frac, self.top_frac, self.right_frac, self.bottom_frac)

    def set_margins(self, margin_frac: float) -> None:
        """Set symmetric margin fraction (clamped to [0.0, 0.45])."""
        m = max(0.0, min(0.45, margin_frac))
        self.left_frac = m
        self.top_frac = m
        self.right_frac = 1.0 - m
        self.bottom_frac = 1.0 - m

    def set_bounds(
        self,
        left_frac: float,
        top_frac: float,
        right_frac: float,
        bottom_frac: float,
    ) -> None:
        """Set explicit boundary fractions, ensuring min dimension of 10%."""
        lf = max(0.0, min(0.8, left_frac))
        tf = max(0.0, min(0.8, top_frac))
        rf = max(lf + 0.1, min(1.0, right_frac))
        bf = max(tf + 0.1, min(1.0, bottom_frac))
        self.left_frac = lf
        self.top_frac = tf
        self.right_frac = rf
        self.bottom_frac = bf

    def map_landmark(
        self,
        norm_x: float,
        norm_y: float,
    ) -> tuple[float, float]:
        """Map a normalized landmark position to virtual screen coordinates.

        Args:
            norm_x: Landmark X in [0, 1] (MediaPipe normalized, post-flip).
            norm_y: Landmark Y in [0, 1].

        Returns:
            (screen_x, screen_y) in virtual screen pixels.
            Clamped to the screen bounds.
        """
        # Convert to pixel position within the frame.
        px = norm_x * self.frame_width
        py = norm_y * self.frame_height

        # Active region bounds in pixels.
        region_left = self.left_frac * self.frame_width
        region_top = self.top_frac * self.frame_height
        region_right = self.right_frac * self.frame_width
        region_bottom = self.bottom_frac * self.frame_height
        region_w = region_right - region_left
        region_h = region_bottom - region_top

        if region_w <= 0 or region_h <= 0:
            return (float(self.screen_left), float(self.screen_top))

        # Normalize within the active region [0, 1].
        rel_x = (px - region_left) / region_w
        rel_y = (py - region_top) / region_h

        # Clamp to [0, 1].
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))

        # Map to virtual screen.
        sx = self.screen_left + rel_x * self.screen_width
        sy = self.screen_top + rel_y * self.screen_height

        return (sx, sy)

    def map_normalized(
        self,
        norm_x: float,
        norm_y: float,
    ) -> tuple[float, float]:
        """Alias for ``map_landmark`` — clearer when input is already 0-1."""
        return self.map_landmark(norm_x, norm_y)

    @classmethod
    def from_screen_info(
        cls,
        frame_width: int,
        frame_height: int,
        screen_left: int,
        screen_top: int,
        screen_width: int,
        screen_height: int,
        margin_frac: float = 0.10,
    ) -> ActiveRegion:
        """Convenience factory that calculates margins symmetrically."""
        return cls(
            frame_width=frame_width,
            frame_height=frame_height,
            left_frac=margin_frac,
            top_frac=margin_frac,
            right_frac=1.0 - margin_frac,
            bottom_frac=1.0 - margin_frac,
            screen_left=screen_left,
            screen_top=screen_top,
            screen_width=screen_width,
            screen_height=screen_height,
        )
