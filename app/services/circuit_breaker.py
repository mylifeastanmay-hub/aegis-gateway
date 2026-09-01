import enum
import logging
import time
from typing import Dict, Any

logger = logging.getLogger("aegis.services.circuit_breaker")


class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"       # Healthy: normal requests allowed
    OPEN = "OPEN"           # Tripped: provider failing, failover immediately
    HALF_OPEN = "HALF_OPEN" # Recovery probe: allow 1 test request


class CircuitBreaker:
    """
    State-machine Circuit Breaker tracking upstream provider errors and timeouts.
    Trips to OPEN state after `failure_threshold` consecutive failures.
    Transitions to HALF_OPEN after `recovery_timeout` seconds to probe recovery.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_state_change = time.time()

    @property
    def state(self) -> CircuitState:
        """
        Returns current state, automatically evaluating transition from OPEN to HALF_OPEN
        if recovery_timeout has elapsed.
        """
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_state_change
            if elapsed >= self.recovery_timeout:
                logger.info(f"CircuitBreaker '{self.name}' recovery timeout elapsed ({elapsed:.1f}s). Transitioning OPEN -> HALF_OPEN.")
                self._state = CircuitState.HALF_OPEN
                self._last_state_change = time.time()
        return self._state

    def allow_request(self) -> bool:
        """
        Returns True if requests are allowed to pass to this provider.
        """
        current_state = self.state
        if current_state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
            return True
        return False

    def record_success(self) -> None:
        """
        Records a successful provider call. Resets failure count and closes circuit if HALF_OPEN.
        """
        old_state = self._state
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        if old_state != CircuitState.CLOSED:
            logger.info(f"CircuitBreaker '{self.name}' recorded success. Transitioning {old_state} -> CLOSED.")
            self._last_state_change = time.time()

    def record_failure(self) -> None:
        """
        Records a provider failure or timeout. Trips circuit to OPEN if threshold exceeded.
        """
        self._failure_count += 1
        current_state = self.state
        logger.warning(f"CircuitBreaker '{self.name}' recorded failure #{self._failure_count} (State: {current_state}).")

        if current_state == CircuitState.HALF_OPEN or self._failure_count >= self.failure_threshold:
            if self._state != CircuitState.OPEN:
                logger.error(f"CircuitBreaker '{self.name}' threshold reached ({self._failure_count}/{self.failure_threshold}). Tripping to OPEN!")
                self._state = CircuitState.OPEN
                self._last_state_change = time.time()

    def get_status(self) -> Dict[str, Any]:
        """
        Returns diagnostic status dictionary.
        """
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_seconds": self.recovery_timeout,
            "seconds_since_state_change": round(time.time() - self._last_state_change, 2)
        }
