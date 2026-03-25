# focus_tracker.py
import base64
import json
import os
import statistics
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


class FocusTracker:
    def __init__(
        self,
        camera_index: int = 0,
        history_file: str = "previous_data.json",
        sessions_file: str = "sessions_data.json",
    ):
        self.camera_index = camera_index
        self.history_file = history_file
        self.sessions_file = sessions_file

        self.cap = None
        self.thread = None
        self.running = False
        self.lock = threading.Lock()

        self.last_score = 0
        self.current_scores: List[int] = []
        self.session_records: List[Dict[str, object]] = []
        self.session_averages: List[int] = []

        self.score_timeline: List[Tuple[float, int]] = []
        self.last_focus_zone = "unknown"
        self.distraction_events = 0
        self.total_tracking_seconds = 0.0
        self.camera_fps = 0.0
        self._last_reader_ts = None

        self.session_start_ts: Optional[float] = None
        self.capture_mode = "server_camera"

        # Smoothed score avoids jitter and produces realistic continuous values.
        self._smoothed_score = 50.0

        self.last_session_summary = {
            "saved": False,
            "average": 0,
            "duration_sec": 0.0,
            "distractions": 0,
            "camera_fps": 0.0,
            "start_time": None,
            "end_time": None,
        }

        # Haarcascade paths
        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self.eye_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_eye.xml"
        )

        self._load_session_history()

    def _load_json_file(self, path: str):
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_json_file(self, path: str, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _parse_iso(self, value: object):
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _safe_int(self, value: object, default: int = 0):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _safe_float(self, value: object, default: float = 0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _session_key(self, record: Dict[str, object]):
        return (
            record.get("start_time"),
            record.get("end_time"),
            int(record.get("average_focus", 0)),
            len(record.get("focus_scores", [])),
        )

    def _session_sort_key(self, record: Dict[str, object]):
        dt = self._parse_iso(record.get("end_time"))
        if dt is None:
            return datetime.min
        return dt

    def _compress_scores(self, scores: List[int], max_len: int = 1800):
        if len(scores) <= max_len:
            return [int(max(0, min(100, s))) for s in scores]

        step = len(scores) / max_len
        compact = []
        for idx in range(max_len):
            src_index = int(idx * step)
            src_index = min(src_index, len(scores) - 1)
            compact.append(int(max(0, min(100, scores[src_index]))))
        return compact

    def _normalize_session_record(self, raw: object):
        if not isinstance(raw, dict):
            return None

        start_raw = raw.get("start_time")
        end_raw = raw.get("end_time")
        duration_raw = raw.get("duration_sec", raw.get("duration"))

        start_dt = self._parse_iso(start_raw)
        end_dt = self._parse_iso(end_raw)

        if start_dt is None or end_dt is None:
            return None

        duration_sec = self._safe_float(duration_raw, 0.0)
        if duration_sec <= 0:
            return None

        if end_dt <= start_dt:
            return None

        focus_scores_raw = raw.get("focus_scores", raw.get("focus_score_data"))
        if not isinstance(focus_scores_raw, list):
            return None

        focus_scores = [
            int(max(0, min(100, self._safe_int(value, -1))))
            for value in focus_scores_raw
            if isinstance(value, (int, float))
        ]
        if not focus_scores:
            return None

        avg_raw = raw.get("average_focus", raw.get("score"))
        if isinstance(avg_raw, (int, float)):
            average_focus = int(max(0, min(100, round(float(avg_raw)))))
        else:
            average_focus = int(round(statistics.mean(focus_scores)))

        return {
            "start_time": start_dt.isoformat(timespec="seconds"),
            "end_time": end_dt.isoformat(timespec="seconds"),
            "duration_sec": round(float(duration_sec), 2),
            "average_focus": average_focus,
            "focus_scores": self._compress_scores(focus_scores),
        }

    def _sanitize_sessions(self, raw_records: object):
        if not isinstance(raw_records, list):
            return []

        normalized = []
        seen = set()

        for raw in raw_records:
            record = self._normalize_session_record(raw)
            if record is None:
                continue

            key = self._session_key(record)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(record)

        normalized.sort(key=self._session_sort_key, reverse=True)
        return normalized

    def _load_session_history(self):
        raw_sessions = self._load_json_file(self.sessions_file)

        # Legacy fallback: previous_data.json may be old score-only history.
        # Convert numeric entries into synthetic completed sessions so graph can still plot.
        if raw_sessions is None or (isinstance(raw_sessions, list) and len(raw_sessions) == 0):
            legacy = self._load_json_file(self.history_file)
            if isinstance(legacy, list) and legacy:
                if isinstance(legacy[0], dict):
                    raw_sessions = legacy
                elif all(isinstance(x, (int, float)) for x in legacy):
                    now = datetime.now()
                    synthesized = []
                    total = len(legacy)
                    for idx, score in enumerate(legacy):
                        # Oldest first at day granularity, then later sorted latest first.
                        shift_days = total - idx
                        start_dt = now.replace(microsecond=0) - timedelta(days=shift_days)
                        end_dt = start_dt + timedelta(minutes=25)
                        value = int(max(0, min(100, round(float(score)))))
                        synthesized.append(
                            {
                                "start_time": start_dt.isoformat(timespec="seconds"),
                                "end_time": end_dt.isoformat(timespec="seconds"),
                                "duration_sec": 1500,
                                "average_focus": value,
                                "focus_scores": [value],
                            }
                        )
                    raw_sessions = synthesized
                else:
                    raw_sessions = []
            else:
                raw_sessions = []

        self.session_records = self._sanitize_sessions(raw_sessions)
        self.session_averages = [int(rec["average_focus"]) for rec in self.session_records]

        # Persist sanitized copy so invalid/duplicate/empty records are removed.
        self._save_session_history()

    def _save_session_history(self):
        try:
            self._write_json_file(self.sessions_file, self.session_records)
            self._write_json_file(self.history_file, [int(rec["average_focus"]) for rec in self.session_records])
        except Exception:
            pass

    def _open_camera(self):
        if self.cap is None:
            self.cap = cv2.VideoCapture(self.camera_index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_FPS, 30)

    def _close_camera(self):
        if self.cap:
            self.cap.release()
            self.cap = None

    def _decode_image_data_url(self, image_data: str):
        if not isinstance(image_data, str) or not image_data.strip():
            return None

        raw = image_data.strip()
        if "," in raw:
            raw = raw.split(",", 1)[1]

        try:
            frame_bytes = base64.b64decode(raw, validate=False)
        except Exception:
            return None

        if not frame_bytes:
            return None

        buffer = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if frame is None:
            return None

        height, width = frame.shape[:2]
        if width > 640 or height > 480:
            frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)

        return frame

    def ingest_browser_frame(self, image_data: str):
        with self.lock:
            if not self.running:
                return {"ok": False, "error": "tracking_not_running"}
            if self.capture_mode != "browser_stream":
                return {"ok": False, "error": "tracker_not_in_browser_mode"}

        frame = self._decode_image_data_url(image_data)
        if frame is None:
            return {"ok": False, "error": "invalid_frame"}

        score = self.compute_focus(frame)
        now_ts = time.time()

        with self.lock:
            if not self.running:
                return {"ok": False, "error": "tracking_not_running"}
            self._register_score(now_ts, score)
            current = int(self.last_score)

        return {"ok": True, "focus": current}
    def _score_to_zone(self, score: int) -> str:
        if score > 70:
            return "high"
        if score > 40:
            return "medium"
        return "low"

    def _clamp(self, value: float, low: float = 0.0, high: float = 100.0):
        return max(low, min(high, value))

    def _normalize_to_100(self, value: float, min_v: float, max_v: float):
        if max_v <= min_v:
            return 0.0
        normalized = (value - min_v) / (max_v - min_v)
        return self._clamp(normalized * 100.0, 0.0, 100.0)

    def _register_score(self, timestamp: float, score: int):
        if self._last_reader_ts is not None:
            dt = timestamp - self._last_reader_ts
            if 0 < dt < 1:
                instant_fps = 1.0 / dt
                if self.camera_fps <= 0:
                    self.camera_fps = instant_fps
                else:
                    self.camera_fps = (self.camera_fps * 0.9) + (instant_fps * 0.1)
                self.total_tracking_seconds += dt

        self._last_reader_ts = timestamp
        self.last_score = int(max(0, min(100, score)))
        self.current_scores.append(self.last_score)
        self.score_timeline.append((timestamp, self.last_score))

        if len(self.current_scores) > 12000:
            self.current_scores.pop(0)
        if len(self.score_timeline) > 54000:
            self.score_timeline.pop(0)

        current_zone = self._score_to_zone(self.last_score)
        if current_zone == "low" and self.last_focus_zone != "low":
            self.distraction_events += 1
        self.last_focus_zone = current_zone

    def compute_focus(self, frame):
        """
        Continuous focus score estimator (0-100).
        Uses blur quality, face position/size, eye detection count, and eye area.
        """
        if frame is None:
            self._smoothed_score = (self._smoothed_score * 0.7) + (5.0 * 0.3)
            return int(round(self._smoothed_score))

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frame_h, frame_w = gray.shape[:2]

        blur_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        blur_score = self._normalize_to_100(blur_var, min_v=8.0, max_v=150.0)

        faces = self.face_cascade.detectMultiScale(gray, 1.1, 5)

        if len(faces) == 0:
            raw_score = 8.0 + (0.15 * blur_score)
            self._smoothed_score = (self._smoothed_score * 0.65) + (raw_score * 0.35)
            return int(round(self._clamp(self._smoothed_score)))

        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        face_area = float(max(1, w * h))
        frame_area = float(max(1, frame_w * frame_h))

        roi = gray[y:y + h, x:x + w]
        eyes = self.eye_cascade.detectMultiScale(roi, 1.07, 4)

        face_area_ratio = face_area / frame_area
        face_size_score = self._normalize_to_100(face_area_ratio, min_v=0.03, max_v=0.24)

        face_cx = x + (w / 2.0)
        face_cy = y + (h / 2.0)
        center_dx = face_cx - (frame_w / 2.0)
        center_dy = face_cy - (frame_h / 2.0)
        max_dist = (((frame_w / 2.0) ** 2) + ((frame_h / 2.0) ** 2)) ** 0.5
        center_dist_norm = (((center_dx ** 2) + (center_dy ** 2)) ** 0.5) / max(1.0, max_dist)
        alignment_score = self._clamp((1.0 - center_dist_norm) * 100.0)

        eye_count = len(eyes)
        eye_count_score = self._clamp((min(eye_count, 2) / 2.0) * 100.0)

        eye_area_total = 0.0
        for ex, ey, ew, eh in eyes:
            eye_area_total += float(max(1, ew * eh))

        eye_area_ratio = eye_area_total / face_area
        eye_area_score = self._normalize_to_100(eye_area_ratio, min_v=0.015, max_v=0.11)

        raw_score = (
            0.28 * blur_score
            + 0.24 * face_size_score
            + 0.18 * alignment_score
            + 0.20 * eye_count_score
            + 0.10 * eye_area_score
        )

        if eye_count == 0:
            raw_score -= 20.0
        elif eye_count == 1:
            raw_score -= 8.0

        if blur_var < 10.0:
            raw_score -= 14.0

        raw_score = self._clamp(raw_score)
        self._smoothed_score = (self._smoothed_score * 0.62) + (raw_score * 0.38)
        return int(round(self._clamp(self._smoothed_score)))

    def reader(self):
        self._open_camera()
        target_frame_time = 1.0 / 15.0

        while True:
            with self.lock:
                if not self.running:
                    break

            frame_start = time.time()

            if self.cap is None:
                break

            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            score = self.compute_focus(frame)
            now_ts = time.time()

            with self.lock:
                if not self.running:
                    break
                self._register_score(now_ts, score)

            elapsed = time.time() - frame_start
            sleep_time = target_frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._close_camera()

    def start(self, mode: str = "server_camera"):
        selected_mode = "browser_stream" if str(mode).strip().lower() == "browser_stream" else "server_camera"

        with self.lock:
            if self.running:
                return False

            self.running = True
            self.capture_mode = selected_mode
            self.last_score = 0
            self.current_scores = []
            self.score_timeline = []
            self.last_focus_zone = "unknown"
            self.distraction_events = 0
            self.total_tracking_seconds = 0.0
            self.camera_fps = 0.0
            self._last_reader_ts = None
            self._smoothed_score = 50.0
            self.session_start_ts = time.time()

        if selected_mode == "server_camera":
            self.thread = threading.Thread(target=self.reader, daemon=True)
            self.thread.start()
        else:
            self.thread = None

        return True

    def stop(self):
        with self.lock:
            was_running = bool(self.running)
            self.running = False

        if self.thread is not None:
            self.thread.join(timeout=2)

        with self.lock:
            end_ts = time.time()
            start_ts = self.session_start_ts
            scores = list(self.current_scores)

            avg = int(statistics.mean(scores)) if scores else 0

            start_iso = datetime.fromtimestamp(start_ts).isoformat(timespec="seconds") if start_ts else None
            end_iso = datetime.fromtimestamp(end_ts).isoformat(timespec="seconds")

            duration = 0.0
            if start_ts is not None:
                duration = max(0.0, end_ts - start_ts)
            elif self.total_tracking_seconds > 0:
                duration = self.total_tracking_seconds

            # Save only completed sessions with required fields.
            saved = False
            if was_running and start_iso and duration > 0 and len(scores) > 0:
                candidate = {
                    "start_time": start_iso,
                    "end_time": end_iso,
                    "duration_sec": duration,
                    "average_focus": avg,
                    "focus_scores": self._compress_scores(scores),
                }

                normalized = self._normalize_session_record(candidate)
                if normalized is not None:
                    candidate_key = self._session_key(normalized)
                    existing_keys = {self._session_key(rec) for rec in self.session_records}
                    if candidate_key not in existing_keys:
                        self.session_records.append(normalized)
                        self.session_records.sort(key=self._session_sort_key, reverse=True)
                        if len(self.session_records) > 500:
                            self.session_records = self.session_records[:500]
                        self._save_session_history()
                        saved = True

            self.session_averages = [int(rec["average_focus"]) for rec in self.session_records]

            self.last_session_summary = {
                "saved": saved,
                "average": avg if saved else 0,
                "duration_sec": round(duration, 1) if saved else 0.0,
                "distractions": int(self.distraction_events) if saved else 0,
                "camera_fps": round(self.camera_fps, 2) if saved else 0.0,
                "start_time": start_iso if saved else None,
                "end_time": end_iso if saved else None,
            }

            self.current_scores = []
            self.score_timeline = []
            self.last_score = 0
            self.last_focus_zone = "unknown"
            self.distraction_events = 0
            self.total_tracking_seconds = 0.0
            self.camera_fps = 0.0
            self._last_reader_ts = None
            self.session_start_ts = None
            self.capture_mode = "server_camera"

        return avg if self.last_session_summary["saved"] else 0

    def get_last_score(self):
        with self.lock:
            return int(self.last_score)

    def is_running(self):
        with self.lock:
            return bool(self.running)

    def get_capture_mode(self):
        with self.lock:
            return str(self.capture_mode)
    def get_last_session_summary(self):
        with self.lock:
            return dict(self.last_session_summary)

    def _format_duration(self, duration_sec: float):
        total = int(max(0, round(duration_sec)))
        minutes = total // 60
        seconds = total % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _session_date_label(self, iso_text: str):
        dt = self._parse_iso(iso_text)
        if dt is None:
            return iso_text
        return dt.strftime("%Y-%m-%d %H:%M")

    def get_completed_sessions(self, limit: int = 200):
        with self.lock:
            sessions = list(self.session_records)

        sessions = sessions[: max(1, limit)]

        formatted = []
        for record in sessions:
            formatted.append(
                {
                    "start_time": record["start_time"],
                    "end_time": record["end_time"],
                    "duration_sec": float(record["duration_sec"]),
                    "duration_label": self._format_duration(float(record["duration_sec"])),
                    "average_focus": int(record["average_focus"]),
                    "focus_scores": list(record["focus_scores"]),
                    "date_label": self._session_date_label(str(record["start_time"])),
                }
            )

        return formatted

    def get_session_trend(self, limit: int = 200):
        with self.lock:
            sessions = list(self.session_records)

        if not sessions:
            return []

        sessions.sort(key=self._session_sort_key)  # oldest -> latest for trend
        sessions = sessions[-max(1, limit):]

        trend = []
        prev_score = None
        for idx, record in enumerate(sessions, start=1):
            score = int(record["average_focus"])
            delta = 0 if prev_score is None else score - prev_score
            trend.append(
                {
                    "session_index": idx,
                    "date_label": self._session_date_label(str(record["start_time"])),
                    "score": score,
                    "delta": delta,
                }
            )
            prev_score = score

        return trend

    def get_session_averages(self):
        with self.lock:
            return [int(rec["average_focus"]) for rec in self.session_records]

    def get_focus_history(self, limit: int = 60):
        with self.lock:
            timeline = list(self.score_timeline)

        if not timeline:
            return []

        per_second = {}
        for ts, score in timeline:
            second_key = int(ts)
            per_second[second_key] = int(score)

        keys = sorted(per_second.keys())[-max(1, limit):]
        return [
            {
                "time": time.strftime("%H:%M:%S", time.localtime(sec)),
                "score": per_second[sec],
            }
            for sec in keys
        ]

    def get_focus_distribution(self, window_seconds: int = 300):
        now = time.time()

        with self.lock:
            timeline = list(self.score_timeline)

        if window_seconds > 0:
            timeline = [point for point in timeline if (now - point[0]) <= window_seconds]

        high = medium = low = 0
        for _, score in timeline:
            zone = self._score_to_zone(score)
            if zone == "high":
                high += 1
            elif zone == "medium":
                medium += 1
            else:
                low += 1

        return {"high": high, "medium": medium, "low": low}

    def predict_focus_drop(self, horizon_seconds: int = 10):
        with self.lock:
            timeline = list(self.score_timeline[-600:])
            current_score = int(self.last_score)

        if len(timeline) < 30:
            return {
                "predicted_score": current_score,
                "drop_expected": False,
                "confidence": 0.0,
                "trend_slope": 0.0,
            }

        t0 = timeline[0][0]
        xs = [point[0] - t0 for point in timeline]
        ys = [float(point[1]) for point in timeline]

        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)

        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        denominator = sum((x - mean_x) ** 2 for x in xs)

        if denominator <= 1e-9:
            slope = 0.0
            intercept = mean_y
        else:
            slope = numerator / denominator
            intercept = mean_y - slope * mean_x

        future_x = xs[-1] + max(1, horizon_seconds)
        predicted = intercept + (slope * future_x)
        predicted = int(round(max(0.0, min(100.0, predicted))))

        if len(ys) > 1:
            ss_tot = sum((y - mean_y) ** 2 for y in ys)
            if ss_tot <= 1e-9:
                r2 = 0.0
            else:
                ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
                r2 = max(0.0, min(1.0, 1.0 - (ss_res / ss_tot)))
        else:
            r2 = 0.0

        drop_expected = predicted < 40 or (slope < -1.2 and predicted < 55)
        confidence = round(max(0.0, min(1.0, r2 * min(1.0, len(ys) / 180.0))), 2)

        return {
            "predicted_score": predicted,
            "drop_expected": bool(drop_expected),
            "confidence": confidence,
            "trend_slope": round(slope, 3),
        }

    def get_analytics(self):
        with self.lock:
            session_averages = [int(rec["average_focus"]) for rec in self.session_records]
            current_scores = list(self.current_scores)
            running = bool(self.running)
            distraction_events = int(self.distraction_events)
            total_tracking_seconds = float(self.total_tracking_seconds)
            camera_fps = float(self.camera_fps)
            last_session = dict(self.last_session_summary)

        average_focus = int(statistics.mean(session_averages)) if session_averages else 0

        if session_averages:
            best_score = max(session_averages)
            worst_score = min(session_averages)
            best_index = session_averages.index(best_score) + 1
            worst_index = session_averages.index(worst_score) + 1
        else:
            best_score = 0
            worst_score = 0
            best_index = 0
            worst_index = 0

        if total_tracking_seconds > 0:
            distraction_frequency = distraction_events / (total_tracking_seconds / 60.0)
        elif last_session.get("duration_sec", 0) > 0:
            distraction_frequency = float(last_session.get("distractions", 0)) / (
                float(last_session.get("duration_sec", 1)) / 60.0
            )
        else:
            distraction_frequency = 0.0

        current_session_average = int(statistics.mean(current_scores)) if current_scores else 0

        return {
            "running": running,
            "session_count": len(session_averages),
            "average_focus": average_focus,
            "best_session": {"index": best_index, "score": best_score},
            "worst_session": {"index": worst_index, "score": worst_score},
            "distraction_frequency_per_minute": round(distraction_frequency, 2),
            "current_session_average": current_session_average,
            "current_session_duration_sec": round(total_tracking_seconds, 1),
            "camera_fps": round(camera_fps, 2),
            "last_session": last_session,
        }












