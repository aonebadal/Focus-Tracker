import os
import threading
from typing import Optional

try:
    import pygame  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    pygame = None


class MusicEngine:
    def __init__(self, music_folder: str = "static/music"):
        self.music_folder = music_folder
        self.lock = threading.Lock()

        self.mode = "stopped"
        self.label = "off"
        self.current_track: Optional[str] = None
        self.volume = 1.0
        self.last_error: Optional[str] = None

        self.player = "virtual"
        self._pygame_ready = False

        if pygame is not None:
            try:
                pygame.mixer.init()
                self._pygame_ready = True
                self.player = "pygame"
            except Exception as exc:  # pragma: no cover - depends on host audio stack
                self.last_error = f"pygame init failed: {exc}"

    def _resolve_track(self, keywords):
        if not os.path.isdir(self.music_folder):
            return None

        valid_ext = {".mp3", ".wav", ".ogg", ".m4a"}
        for name in sorted(os.listdir(self.music_folder)):
            lower = name.lower()
            _, ext = os.path.splitext(lower)
            if ext not in valid_ext:
                continue
            if any(key in lower for key in keywords):
                return os.path.join(self.music_folder, name)
        return None

    def _apply_playback(self, track_path: Optional[str]):
        if self.player != "pygame" or not self._pygame_ready:
            return

        try:
            if track_path and os.path.exists(track_path):
                pygame.mixer.music.load(track_path)
                pygame.mixer.music.set_volume(max(0.0, min(1.0, self.volume)))
                pygame.mixer.music.play(-1)
            else:
                pygame.mixer.music.stop()
        except Exception as exc:  # pragma: no cover - depends on host audio stack
            self.last_error = str(exc)

    def _set_mode(self, mode: str, label: str, keywords, volume: float):
        track = self._resolve_track(keywords)
        with self.lock:
            self.mode = mode
            self.label = label
            self.volume = max(0.0, min(1.0, volume))
            self.current_track = track

        self._apply_playback(track)

    def play_focus_music(self):
        self._set_mode("focus", "deep_work", ["focus", "deep", "work"], 1.0)
        return self.get_state()

    def play_relax_music(self):
        self._set_mode("relax", "ambient", ["ambient", "relax", "chill"], 0.7)
        return self.get_state()

    def play_meditation_music(self):
        self._set_mode("meditation", "meditation", ["meditation", "calm", "breath"], 0.45)
        return self.get_state()

    def stop_music(self):
        with self.lock:
            self.mode = "stopped"
            self.label = "off"
            self.current_track = None
            self.volume = 0.0

        self._apply_playback(None)
        return self.get_state()

    def set_volume(self, value: float):
        if not isinstance(value, (int, float)):
            raise ValueError("volume must be a number")

        clipped = max(0.0, min(1.0, float(value)))
        with self.lock:
            self.volume = clipped

        if self.player == "pygame" and self._pygame_ready:
            try:
                pygame.mixer.music.set_volume(clipped)
            except Exception as exc:  # pragma: no cover - depends on host audio stack
                self.last_error = str(exc)

        return self.get_state()

    def control(self, action: str):
        if not isinstance(action, str):
            raise ValueError("action must be a string")

        selected = action.strip().lower()
        if selected in {"focus", "deep", "deep_work"}:
            return self.play_focus_music()
        if selected in {"relax", "ambient"}:
            return self.play_relax_music()
        if selected in {"meditation", "calm"}:
            return self.play_meditation_music()
        if selected in {"stop", "off"}:
            return self.stop_music()

        raise ValueError("unsupported action")

    def get_state(self):
        with self.lock:
            track_url = None
            if self.current_track:
                # Convert to Flask static path if track lives under static directory.
                normalized = self.current_track.replace("\\", "/")
                lower_path = normalized.lower()
                marker = "/static/"
                idx = lower_path.find(marker)
                if idx >= 0:
                    track_url = normalized[idx + 1 :]
                elif lower_path.startswith("static/"):
                    track_url = normalized

            return {
                "mode": self.mode,
                "label": self.label,
                "volume": round(float(self.volume), 2),
                "track": self.current_track,
                "track_url": track_url,
                "player": self.player,
                "last_error": self.last_error,
            }
