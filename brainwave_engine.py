import threading
import time
from typing import Dict, Optional


class BrainwaveEngine:
    """
    Score-aware brainwave guidance state for front-end binaural synthesis.

    Focus mapping (based on provided spec + fallback low-focus states):
    - 80-100: Gamma 40 Hz
    - 60-79: Beta 20 Hz
    - 40-59: Alpha 10 Hz
    - 20-39: Theta 6 Hz
    - 0-19: Delta 2 Hz
    """

    WAVE_RULES = [
        {
            "name": "gamma",
            "label": "Gamma Waves",
            "min": 80,
            "max": 100,
            "beat_hz": 40.0,
            "carrier_hz": 440.0,
            "purpose": "Deep concentration",
        },
        {
            "name": "beta",
            "label": "Beta Waves",
            "min": 60,
            "max": 79,
            "beat_hz": 20.0,
            "carrier_hz": 360.0,
            "purpose": "Active study and task execution",
        },
        {
            "name": "alpha",
            "label": "Alpha Waves",
            "min": 40,
            "max": 59,
            "beat_hz": 10.0,
            "carrier_hz": 280.0,
            "purpose": "Calm focus and reduced stress",
        },
        {
            "name": "theta",
            "label": "Theta Waves",
            "min": 20,
            "max": 39,
            "beat_hz": 6.0,
            "carrier_hz": 220.0,
            "purpose": "Mental reset and creativity",
        },
        {
            "name": "delta",
            "label": "Delta Waves",
            "min": 0,
            "max": 19,
            "beat_hz": 2.0,
            "carrier_hz": 180.0,
            "purpose": "Recovery and deep relaxation",
        },
    ]

    def __init__(self, enabled: bool = True, volume: float = 0.18):
        self.lock = threading.Lock()
        self.enabled = bool(enabled)
        self.volume = float(max(0.0, min(1.0, volume)))
        self.custom_carrier_hz: Optional[float] = None

        self.last_focus_score = 0
        self.last_state = self._build_state(score=0)
        self.updated_at = time.time()

    def _normalize_score(self, score):
        if not isinstance(score, (int, float)):
            raise ValueError("focus score must be numeric")
        return int(max(0, min(100, score)))

    def _rule_for_score(self, score: int) -> Dict[str, object]:
        for rule in self.WAVE_RULES:
            if rule["min"] <= score <= rule["max"]:
                return rule
        return self.WAVE_RULES[-1]

    def _build_state(self, score: int):
        rule = self._rule_for_score(score)
        carrier_hz = float(self.custom_carrier_hz) if self.custom_carrier_hz is not None else float(rule["carrier_hz"])
        beat_hz = float(rule["beat_hz"])

        left_hz = max(20.0, carrier_hz - (beat_hz / 2.0))
        right_hz = max(20.0, carrier_hz + (beat_hz / 2.0))

        return {
            "enabled": bool(self.enabled),
            "focus_score": int(score),
            "wave": rule["name"],
            "label": rule["label"],
            "purpose": rule["purpose"],
            "beat_hz": round(beat_hz, 2),
            "carrier_hz": round(carrier_hz, 2),
            "left_hz": round(left_hz, 2),
            "right_hz": round(right_hz, 2),
            "volume": round(float(self.volume), 2),
        }

    def update_from_focus(self, score):
        normalized = self._normalize_score(score)
        with self.lock:
            self.last_focus_score = normalized
            self.last_state = self._build_state(normalized)
            self.updated_at = time.time()
            state = dict(self.last_state)
            state["updated_at"] = self.updated_at
            return state

    def control(
        self,
        enabled: Optional[bool] = None,
        volume: Optional[float] = None,
        carrier_hz: Optional[float] = None,
    ):
        with self.lock:
            if enabled is not None:
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be boolean")
                self.enabled = enabled

            if volume is not None:
                if not isinstance(volume, (int, float)):
                    raise ValueError("volume must be numeric")
                self.volume = float(max(0.0, min(1.0, volume)))

            if carrier_hz is not None:
                if not isinstance(carrier_hz, (int, float)):
                    raise ValueError("carrier_hz must be numeric")
                if carrier_hz < 100 or carrier_hz > 1000:
                    raise ValueError("carrier_hz must be between 100 and 1000")
                self.custom_carrier_hz = float(carrier_hz)

            self.last_state = self._build_state(self.last_focus_score)
            self.updated_at = time.time()

            state = dict(self.last_state)
            state["updated_at"] = self.updated_at
            return state

    def get_state(self):
        with self.lock:
            state = dict(self.last_state)
            state["updated_at"] = self.updated_at
            return state
