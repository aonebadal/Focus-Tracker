import json
import os
import threading
import urllib.error
import urllib.request
from typing import Dict, Optional


class IoTController:
    VALID_COLORS = {"green", "yellow", "red", "blue", "off"}
    VALID_FAN_SPEEDS = {"off", "low", "medium", "normal", "high"}
    VALID_RELAY_STATES = {"on", "off"}

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 0.08,
        enabled: Optional[bool] = None,
    ):
        configured_url = base_url or os.getenv("ESP32_BASE_URL", "http://192.168.4.1")
        self.base_url = configured_url.rstrip("/")

        if enabled is None:
            env_value = os.getenv("IOT_ENABLED", "1").strip().lower()
            self.enabled = env_value in {"1", "true", "yes", "on"}
        else:
            self.enabled = bool(enabled)

        self.timeout = float(os.getenv("ESP32_TIMEOUT", timeout))

        self.lock = threading.Lock()
        self.last_result: Dict[str, object] = {
            "ok": False,
            "message": "No IoT command sent yet.",
        }
        self.last_payload: Dict[str, object] = {}
        self.last_sensor_data: Dict[str, object] = {
            "ok": False,
            "message": "No sensor data fetched yet.",
        }

    def _post_sync(self, path: str, payload: Dict[str, object]):
        if not self.enabled:
            result = {"ok": False, "message": "IoT controller disabled."}
            with self.lock:
                self.last_result = result
            return result

        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="ignore")
                result = {
                    "ok": True,
                    "status": int(getattr(response, "status", 200)),
                    "response": raw,
                }
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            result = {"ok": False, "error": str(exc)}

        with self.lock:
            self.last_result = result
            self.last_payload = dict(payload)

        return result

    def _post_async(self, path: str, payload: Dict[str, object]):
        if not self.enabled:
            result = {"ok": False, "message": "IoT controller disabled."}
            with self.lock:
                self.last_result = result
                self.last_payload = dict(payload)
            return result

        thread = threading.Thread(target=self._post_sync, args=(path, payload), daemon=True)
        thread.start()

        with self.lock:
            self.last_payload = dict(payload)

        return {"ok": True, "queued": True}

    def _get_sync(self, path: str):
        if not self.enabled:
            return {"ok": False, "message": "IoT controller disabled."}

        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url=url, method="GET")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="ignore")
                payload = json.loads(raw) if raw else {}
                result = {"ok": True, "status": int(getattr(response, "status", 200)), "data": payload}
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            result = {"ok": False, "error": str(exc)}

        with self.lock:
            self.last_sensor_data = dict(result)
        return result

    def send_focus_to_esp32(self, score: int):
        if not isinstance(score, (int, float)):
            raise ValueError("focus must be a number")
        value = int(score)
        if value < 0 or value > 100:
            raise ValueError("focus must be between 0 and 100")

        return self._post_async("/iot/control", {"focus": value})

    def set_light_color(self, color: str):
        if not isinstance(color, str):
            raise ValueError("color must be a string")

        selected = color.strip().lower()
        if selected not in self.VALID_COLORS:
            raise ValueError(f"color must be one of {sorted(self.VALID_COLORS)}")

        return self._post_async("/iot/control", {"light_color": selected})

    def set_fan_speed(self, level: str):
        if not isinstance(level, str):
            raise ValueError("fan speed must be a string")

        selected = level.strip().lower()
        if selected not in self.VALID_FAN_SPEEDS:
            raise ValueError(f"fan speed must be one of {sorted(self.VALID_FAN_SPEEDS)}")

        return self._post_async("/iot/control", {"fan_speed": selected})

    def set_relay_state(self, state: str):
        if not isinstance(state, str):
            raise ValueError("relay state must be a string")

        selected = state.strip().lower()
        if selected not in self.VALID_RELAY_STATES:
            raise ValueError(f"relay state must be one of {sorted(self.VALID_RELAY_STATES)}")

        return self._post_async("/iot/control", {"relay_state": selected})

    def set_environment(
        self,
        focus: Optional[int] = None,
        light_color: Optional[str] = None,
        fan_speed: Optional[str] = None,
        relay_state: Optional[str] = None,
    ):
        payload: Dict[str, object] = {}

        if focus is not None:
            if not isinstance(focus, (int, float)):
                raise ValueError("focus must be numeric")
            focus_int = int(focus)
            if focus_int < 0 or focus_int > 100:
                raise ValueError("focus must be between 0 and 100")
            payload["focus"] = focus_int

        if light_color is not None:
            if not isinstance(light_color, str):
                raise ValueError("light_color must be a string")
            light_color = light_color.strip().lower()
            if light_color not in self.VALID_COLORS:
                raise ValueError(f"light_color must be one of {sorted(self.VALID_COLORS)}")
            payload["light_color"] = light_color

        if fan_speed is not None:
            if not isinstance(fan_speed, str):
                raise ValueError("fan_speed must be a string")
            fan_speed = fan_speed.strip().lower()
            if fan_speed not in self.VALID_FAN_SPEEDS:
                raise ValueError(f"fan_speed must be one of {sorted(self.VALID_FAN_SPEEDS)}")
            payload["fan_speed"] = fan_speed

        if relay_state is not None:
            if not isinstance(relay_state, str):
                raise ValueError("relay_state must be a string")
            relay_state = relay_state.strip().lower()
            if relay_state not in self.VALID_RELAY_STATES:
                raise ValueError(f"relay_state must be one of {sorted(self.VALID_RELAY_STATES)}")
            payload["relay_state"] = relay_state

        if not payload:
            raise ValueError("at least one environment field must be provided")

        return self._post_async("/iot/control", payload)

    def get_sensor_data(self):
        return self._get_sync("/iot/status")

    def get_status(self):
        with self.lock:
            return {
                "enabled": self.enabled,
                "base_url": self.base_url,
                "last_payload": dict(self.last_payload),
                "last_result": dict(self.last_result),
                "last_sensor_data": dict(self.last_sensor_data),
            }
