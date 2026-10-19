"""One Euro filter for cursor smoothing (Section 6).

Implements the One Euro Filter as described in:
  Géry Casiez, Nicolas Roussel, Daniel Vogel (2012).
  "1 € Filter: A Simple Speed-based Low-pass Filter for Noisy Input in
  Interactive Systems." CHI '12.

The filter adapts its cutoff frequency to the velocity of the input:
- Slow movement → lower cutoff → more smoothing (removes jitter/tremor).
- Fast movement → higher cutoff → less smoothing (preserves responsiveness).

This module is independent of UI, OS, and any display system.
"""

from __future__ import annotations

import math


class _LowPassFilter:
    """Exponential low-pass filter (per-axis)."""

    def __init__(self, initial: float = 0.0) -> None:
        self._value = initial
        self._initialized = False

    def filter(self, value: float, alpha: float) -> float:
        if not self._initialized:
            self._value = value
            self._initialized = True
            return value
        self._value = alpha * value + (1.0 - alpha) * self._value
        return self._value

    @property
    def value(self) -> float:
        return self._value

    def reset(self, value: float = 0.0) -> None:
        self._value = value
        self._initialized = False


class OneEuroFilter:
    """One Euro Filter for a single scalar value.

    Parameters:
        min_cutoff: Minimum cutoff frequency in Hz (controls smoothing at rest).
        beta: Speed coefficient (how fast the cutoff increases with velocity).
        d_cutoff: Cutoff frequency for the derivative (speed estimate).
    """

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_filter = _LowPassFilter()
        self._dx_filter = _LowPassFilter()
        self._last_time: float | None = None

    def __call__(self, x: float, timestamp: float) -> float:
        """Filter a new sample.

        Args:
            x: New raw value.
            timestamp: Current time in seconds (monotonic).

        Returns:
            Filtered value.
        """
        if self._last_time is None:
            self._last_time = timestamp
            return self._x_filter.filter(x, 1.0)

        dt = timestamp - self._last_time
        if dt <= 0:
            return self._x_filter.value

        self._last_time = timestamp

        # Derivative estimate.
        dx = (x - self._x_filter.value) / dt
        alpha_d = self._alpha(dt, self._d_cutoff)
        dx_hat = self._dx_filter.filter(dx, alpha_d)

        # Adaptive cutoff.
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        alpha = self._alpha(dt, cutoff)

        return self._x_filter.filter(x, alpha)

    @property
    def _d_cutoff(self) -> float:
        return self.d_cutoff

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        """Compute the smoothing factor alpha from dt and cutoff frequency."""
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self) -> None:
        """Reset filter state (e.g. when hand reappears after loss)."""
        self._x_filter.reset()
        self._dx_filter.reset()
        self._last_time = None


class OneEuro2D:
    """Two-axis One Euro Filter for 2D cursor smoothing.

    Parameters:
        min_cutoff: Minimum cutoff frequency in Hz.
        beta: Speed coefficient.
        d_cutoff: Derivative cutoff frequency.
    """

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ) -> None:
        self._fx = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self._fy = OneEuroFilter(min_cutoff, beta, d_cutoff)

    @property
    def filter_x(self) -> OneEuroFilter:
        return self._fx

    @property
    def filter_y(self) -> OneEuroFilter:
        return self._fy

    def filter(self, x: float, y: float, timestamp: float) -> tuple[float, float]:
        """Filter a 2D position.

        Args:
            x: Raw X coordinate.
            y: Raw Y coordinate.
            timestamp: Current monotonic timestamp in seconds.

        Returns:
            (filtered_x, filtered_y).
        """
        return (self._fx(x, timestamp), self._fy(y, timestamp))

    def reset(self) -> None:
        """Reset both axes (call when hand reappears after loss)."""
        self._fx.reset()
        self._fy.reset()

