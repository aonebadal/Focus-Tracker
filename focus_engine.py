import threading
import time
from typing import Dict, Optional


class FocusDecisionEngine:
    TECHNIQUES = [
        "Use the 4-7-8 breathing cycle for 60 seconds.",
        "Try a 25-minute Pomodoro sprint with a 5-minute break.",
        "Do Trataka-style single-point attention for 1 minute.",
        "Stand up, stretch, and restart with one clear micro-task.",
    ]

    def __init__(self, iot_controller, music_engine):
        self.iot = iot_controller
        self.music = music_engine
        self.lock = threading.Lock()

        self.last_mode = "idle"
        self.last_light_color = "off"
        self.last_fan_speed = "off"
        self.last_alert = False
        self.last_suggestion: Optional[str] = None
        self.last_prediction_warning = False
        self.last_music_state = self.music.get_state()

        self._suggestion_index = 0
        self._last_focus_push = 0.0
        self.focus_push_interval_sec = 1.0

    def _next_suggestion(self):
        suggestion = self.TECHNIQUES[self._suggestion_index % len(self.TECHNIQUES)]
        self._suggestion_index += 1
        return suggestion

    def _decide_target(self, score: int, prediction: Dict[str, object]):
        prediction_warning = bool(prediction.get("drop_expected", False))

        if score > 70:
            return {
                "mode": "high",
                "light_color": "green",
                "fan_speed": "normal",
                "music_action": "focus",
                "alert": False,
                "suggestion": None,
                "prediction_warning": prediction_warning,
            }

        if score > 40:
            suggestion = self._next_suggestion() if prediction_warning else None
            return {
                "mode": "medium",
                "light_color": "yellow",
                "fan_speed": "medium",
                "music_action": "relax",
                "alert": False,
                "suggestion": suggestion,
                "prediction_warning": prediction_warning,
            }

        return {
            "mode": "low",
            "light_color": "red",
            "fan_speed": "low",
            "music_action": "meditation",
            "alert": True,
            "suggestion": self._next_suggestion(),
            "prediction_warning": True,
        }

    def process_focus_score(self, score: int, prediction: Optional[Dict[str, object]] = None, force: bool = False):
        if not isinstance(score, (int, float)):
            raise ValueError("focus score must be numeric")

        normalized = int(max(0, min(100, score)))
        prediction = prediction or {}

        with self.lock:
            target = self._decide_target(normalized, prediction)
            mode_changed = force or (target["mode"] != self.last_mode)
            env_changed = force or (
                target["light_color"] != self.last_light_color
                or target["fan_speed"] != self.last_fan_speed
            )
            alert_changed = force or (target["alert"] != self.last_alert)

        now = time.time()

        if env_changed:
            self.iot.set_environment(
                focus=normalized,
                light_color=target["light_color"],
                fan_speed=target["fan_speed"],
            )
        elif now - self._last_focus_push >= self.focus_push_interval_sec:
            self.iot.send_focus_to_esp32(normalized)

        self._last_focus_push = now

        if mode_changed:
            action = target["music_action"]
            if action == "focus":
                music_state = self.music.play_focus_music()
            elif action == "relax":
                music_state = self.music.play_relax_music()
            else:
                music_state = self.music.play_meditation_music()
        else:
            music_state = self.music.get_state()

        with self.lock:
            self.last_mode = target["mode"]
            self.last_light_color = target["light_color"]
            self.last_fan_speed = target["fan_speed"]
            self.last_alert = bool(target["alert"])
            self.last_prediction_warning = bool(target["prediction_warning"])
            if target["suggestion"]:
                self.last_suggestion = target["suggestion"]
            elif alert_changed and not target["alert"]:
                self.last_suggestion = None
            self.last_music_state = music_state

            return {
                "mode": self.last_mode,
                "light_color": self.last_light_color,
                "fan_speed": self.last_fan_speed,
                "music_state": self.last_music_state,
                "alert": self.last_alert,
                "suggestion": self.last_suggestion,
                "prediction_warning": self.last_prediction_warning,
            }

    def reset(self):
        self.music.stop_music()
        with self.lock:
            self.last_mode = "idle"
            self.last_light_color = "off"
            self.last_fan_speed = "off"
            self.last_alert = False
            self.last_suggestion = None
            self.last_prediction_warning = False
            self.last_music_state = self.music.get_state()

    def get_status(self):
        with self.lock:
            return {
                "mode": self.last_mode,
                "light_color": self.last_light_color,
                "fan_speed": self.last_fan_speed,
                "music_state": self.last_music_state,
                "alert": self.last_alert,
                "suggestion": self.last_suggestion,
                "prediction_warning": self.last_prediction_warning,
            }
