# app.py
import atexit
import os
import re

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

def _load_env_file_fallback(env_path: str = ".env") -> None:
    if not os.path.exists(env_path):
        return

    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ[key] = value
    except Exception:
        return


def _try_load_dotenv() -> bool:
    # Dynamic import avoids hard failure/warning when python-dotenv is not installed.
    try:
        dotenv = __import__("dotenv", fromlist=["load_dotenv"])
        loader = getattr(dotenv, "load_dotenv", None)
        if callable(loader):
            try:
                loader(override=True)
            except TypeError:
                loader()
            return True
    except Exception:
        return False
    return False


if not _try_load_dotenv():
    _load_env_file_fallback()

from brainwave_engine import BrainwaveEngine
from focus_engine import FocusDecisionEngine
from focus_tracker import FocusTracker
from iot_controller import IoTController
from music_engine import MusicEngine
from vision_ai import VisionAIError, VisionAIService, extract_text_from_uploaded_file

app = Flask(__name__, template_folder="templates", static_folder="static")

# Core services
tracker = FocusTracker()
iot_controller = IoTController()
music_engine = MusicEngine()
brainwave_engine = BrainwaveEngine()
decision_engine = FocusDecisionEngine(iot_controller=iot_controller, music_engine=music_engine)
vision_ai_service = VisionAIService()

POMODORO_SUBJECTS = [
    "Programming",
    "Mathematics",
    "Physics",
    "Chemistry",
    "Computer Science",
    "General Study Help",
]

# Ensure camera/audio closes on app exit
def cleanup():
    tracker._close_camera()
    music_engine.stop_music()


atexit.register(cleanup)


def _parse_focus_value(value):
    if not isinstance(value, (int, float, str)):
        raise ValueError("focus must be numeric")

    try:
        focus = int(float(value))
    except (TypeError, ValueError):
        raise ValueError("focus must be numeric")

    if focus < 0 or focus > 100:
        raise ValueError("focus must be between 0 and 100")

    return focus


def _parse_bool(value, field_name: str):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False

    raise ValueError(f"{field_name} must be boolean")


def _parse_relay_state(value):
    if isinstance(value, bool):
        return "on" if value else "off"

    if isinstance(value, (int, float)):
        return "on" if int(value) != 0 else "off"

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"on", "1", "true", "yes", "enabled"}:
            return "on"
        if lowered in {"off", "0", "false", "no", "disabled"}:
            return "off"

    raise ValueError("relay_state must be on/off or boolean")


def _normalize_subject(subject: object) -> str:
    value = str(subject or "").strip()
    for item in POMODORO_SUBJECTS:
        if value.lower() == item.lower():
            return item
    return POMODORO_SUBJECTS[0]


def _normalize_chat_id(raw_chat_id: object) -> str:
    value = str(raw_chat_id or "").strip()
    if not value:
        return ""
    if len(value) > 128:
        raise ValueError("chat_id is too long")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("chat_id must contain only letters, numbers, dash, or underscore")
    return value


# ------------------ ROUTES ------------------

@app.route("/")
def index():
    esp32_browser_url = str(
        os.getenv("ESP32_BROWSER_URL", os.getenv("ESP32_BASE_URL", ""))
    ).strip().rstrip("/")
    return render_template("index.html", esp32_browser_url=esp32_browser_url)


@app.route("/start", methods=["POST"])
def start_route():
    """Start tracking in server camera mode or browser stream mode."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"mode"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    mode = payload.get("mode", "server_camera")
    if not isinstance(mode, str):
        return jsonify({"error": "mode must be string"}), 400

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"server_camera", "browser_stream"}:
        return jsonify({"error": "mode must be server_camera or browser_stream"}), 400

    started = tracker.start(mode=normalized_mode)
    if started:
        decision_engine.reset()

    return jsonify(
        {
            "started": started,
            "mode": tracker.get_capture_mode(),
            "brainwave": brainwave_engine.get_state(),
        }
    )


@app.route("/stop", methods=["POST"])
def stop_route():
    """Stop camera and return session average"""
    avg = tracker.stop()
    music_engine.stop_music()
    decision_engine.reset()

    return jsonify(
        {
            "average": avg,
            "saved": tracker.get_last_session_summary().get("saved", False),
            "analytics": tracker.get_analytics(),
            "brainwave": brainwave_engine.get_state(),
        }
    )


@app.route("/focus")
def focus_route():
    """Get last focus score plus environment and prediction state"""
    focus_score = tracker.get_last_score()
    prediction = tracker.predict_focus_drop(horizon_seconds=10)

    if tracker.is_running():
        environment = decision_engine.process_focus_score(focus_score, prediction)
    else:
        environment = decision_engine.get_status()

    brainwave_state = brainwave_engine.update_from_focus(focus_score)

    history = tracker.get_focus_history(limit=60)
    distribution = tracker.get_focus_distribution(window_seconds=300)
    analytics = tracker.get_analytics()

    return jsonify(
        {
            "focus": focus_score,
            "capture_mode": tracker.get_capture_mode(),
            "prediction": prediction,
            "environment": environment,
            "brainwave": brainwave_state,
            "history": history,
            "distribution": distribution,
            "analytics": analytics,
        }
    )



@app.route("/focus/frame", methods=["POST"])
def focus_frame_route():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "JSON body is required"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"image"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    image_data = payload.get("image")
    if not isinstance(image_data, str) or not image_data.strip():
        return jsonify({"error": "image is required"}), 400

    # Cap payload size to keep frame ingestion low-latency.
    if len(image_data) > 2_000_000:
        return jsonify({"error": "image payload too large"}), 413

    result = tracker.ingest_browser_frame(image_data)
    if not result.get("ok"):
        code_map = {
            "tracking_not_running": 409,
            "tracker_not_in_browser_mode": 409,
            "invalid_frame": 400,
        }
        error = str(result.get("error", "frame_ingest_failed"))
        return jsonify({"error": error}), code_map.get(error, 400)

    return jsonify(result)
@app.route("/previous")
def previous_page():
    sessions = tracker.get_completed_sessions(limit=300)
    trend = tracker.get_session_trend(limit=300)
    return render_template("previous.html", sessions=sessions, trend=trend)


@app.route("/api/previous")
def previous_api():
    sessions = tracker.get_completed_sessions(limit=300)
    trend = tracker.get_session_trend(limit=300)
    return jsonify({"sessions": sessions, "trend": trend})


@app.route("/improve")
def improve_page():
    return render_template("improve_focus.html")


@app.route("/pomodoro/start", methods=["POST"])
def pomodoro_start_route():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"subject", "duration_minutes"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    subject = payload.get("subject", POMODORO_SUBJECTS[0])
    duration_minutes = payload.get("duration_minutes", 25)

    if not isinstance(duration_minutes, (int, float)):
        return jsonify({"error": "duration_minutes must be numeric"}), 400

    safe_minutes = int(max(5, min(180, int(duration_minutes))))
    canonical_subject = _normalize_subject(subject)
    message = f"Start {safe_minutes} minute deep study session on {canonical_subject}."

    return jsonify(
        {
            "ok": True,
            "subject": canonical_subject,
            "duration_minutes": safe_minutes,
            "message": message,
        }
    )


@app.route("/iot/update", methods=["POST"])
def iot_update_route():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "JSON body is required"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"focus", "light_color", "fan_speed", "relay_state"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    focus = payload.get("focus")
    light_color = payload.get("light_color")
    fan_speed = payload.get("fan_speed")
    relay_state = payload.get("relay_state")

    if focus is None and light_color is None and fan_speed is None and relay_state is None:
        return jsonify({"error": "at least one field is required"}), 400

    try:
        parsed_focus = _parse_focus_value(focus) if focus is not None else None
        parsed_relay = _parse_relay_state(relay_state) if relay_state is not None else None
        result = iot_controller.set_environment(
            focus=parsed_focus,
            light_color=light_color,
            fan_speed=fan_speed,
            relay_state=parsed_relay,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if parsed_focus is not None:
        prediction = tracker.predict_focus_drop(horizon_seconds=10)
        environment = decision_engine.process_focus_score(parsed_focus, prediction, force=True)
        brainwave = brainwave_engine.update_from_focus(parsed_focus)
    else:
        environment = decision_engine.get_status()
        brainwave = brainwave_engine.get_state()

    return jsonify({"ok": True, "iot_result": result, "environment": environment, "brainwave": brainwave})


@app.route("/iot/relay", methods=["POST"])
def iot_relay_route():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "JSON body is required"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"relay_state"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    if "relay_state" not in payload:
        return jsonify({"error": "relay_state is required"}), 400

    try:
        state = _parse_relay_state(payload.get("relay_state"))
        result = iot_controller.set_relay_state(state)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "relay_state": state, "iot_result": result})


@app.route("/iot/sensors")
def iot_sensors_route():
    data = iot_controller.get_sensor_data()
    return jsonify(data)


@app.route("/music/control", methods=["POST"])
def music_control_route():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "JSON body is required"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"action", "volume"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    action = payload.get("action")
    volume = payload.get("volume")

    if action is None:
        return jsonify({"error": "action is required"}), 400

    try:
        state = music_engine.control(action)

        if volume is not None:
            if not isinstance(volume, (int, float, str)):
                raise ValueError("volume must be numeric")
            state = music_engine.set_volume(float(volume))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "music": state})


@app.route("/brainwave/control", methods=["POST"])
def brainwave_control_route():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "JSON body is required"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"enabled", "volume", "carrier_hz"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    enabled = payload.get("enabled")
    volume = payload.get("volume")
    carrier_hz = payload.get("carrier_hz")

    if enabled is None and volume is None and carrier_hz is None:
        return jsonify({"error": "at least one field is required"}), 400

    try:
        parsed_enabled = _parse_bool(enabled, "enabled") if enabled is not None else None

        if volume is not None and not isinstance(volume, (int, float)):
            raise ValueError("volume must be numeric")

        if carrier_hz is not None and not isinstance(carrier_hz, (int, float)):
            raise ValueError("carrier_hz must be numeric")

        state = brainwave_engine.control(
            enabled=parsed_enabled,
            volume=volume,
            carrier_hz=carrier_hz,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "brainwave": state})


@app.route("/analytics")
def analytics_route():
    analytics = tracker.get_analytics()
    analytics["prediction"] = tracker.predict_focus_drop(horizon_seconds=10)
    analytics["environment"] = decision_engine.get_status()
    analytics["brainwave"] = brainwave_engine.get_state()
    analytics["iot"] = iot_controller.get_status()
    analytics["iot_sensors"] = iot_controller.get_sensor_data()

    return jsonify(analytics)


@app.route("/vision-ai", methods=["POST"])
def vision_ai_route():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "JSON body is required"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"message", "action", "subject", "study_time", "focus_score", "text", "chat_id"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    action = str(payload.get("action", "chat")).strip().lower()
    if action not in {"chat", "study_plan", "summarize", "focus_help"}:
        return jsonify({"error": "action must be chat, study_plan, summarize, or focus_help"}), 400

    message = str(payload.get("message", "") or "").strip()
    text = str(payload.get("text", "") or "").strip()
    if not message and not text:
        return jsonify({"error": "message or text is required"}), 400

    subject = str(payload.get("subject", "") or "").strip()
    study_time = str(payload.get("study_time", "") or "").strip()
    try:
        chat_id = _normalize_chat_id(payload.get("chat_id", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    focus_score = payload.get("focus_score")
    parsed_focus = None
    if focus_score is not None:
        if not isinstance(focus_score, (int, float, str)):
            return jsonify({"error": "focus_score must be numeric"}), 400
        try:
            parsed_focus = int(float(focus_score))
        except (TypeError, ValueError):
            return jsonify({"error": "focus_score must be numeric"}), 400
        parsed_focus = max(0, min(100, parsed_focus))

    try:
        reply, active_chat_id = vision_ai_service.ask_with_session(
            chat_id=chat_id,
            action=action,
            message=message,
            subject=subject,
            study_time=study_time,
            focus_score=parsed_focus,
            text=text,
        )
    except VisionAIError as exc:
        return jsonify({"error": str(exc)}), 503

    return jsonify({"ok": True, "action": action, "chat_id": active_chat_id, "reply": reply})


@app.route("/vision-ai/session/new", methods=["POST"])
def vision_ai_new_session_route():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400
    allowed = {"chat_id"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    try:
        requested_chat_id = _normalize_chat_id(payload.get("chat_id", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    active_chat_id = vision_ai_service.create_session(chat_id=requested_chat_id)
    return jsonify({"ok": True, "chat_id": active_chat_id})


@app.route("/vision-ai/session/reset", methods=["POST"])
def vision_ai_reset_session_route():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400
    allowed = {"chat_id"}
    extras = [key for key in payload if key not in allowed]
    if extras:
        return jsonify({"error": f"unsupported fields: {', '.join(extras)}"}), 400

    try:
        chat_id = _normalize_chat_id(payload.get("chat_id", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not chat_id:
        return jsonify({"error": "chat_id is required"}), 400

    active_chat_id = vision_ai_service.reset_session(chat_id)
    return jsonify({"ok": True, "chat_id": active_chat_id})


@app.route("/vision-ai/upload", methods=["POST"])
def vision_ai_upload_route():
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"error": "file is required"}), 400

    safe_name = secure_filename(uploaded.filename or "")
    if not safe_name:
        return jsonify({"error": "invalid file name"}), 400

    subject = str(request.form.get("subject", "") or "").strip()
    focus_score = request.form.get("focus_score")
    parsed_focus = None
    if focus_score not in (None, ""):
        try:
            parsed_focus = max(0, min(100, int(float(focus_score))))
        except (TypeError, ValueError):
            return jsonify({"error": "focus_score must be numeric"}), 400

    try:
        file_bytes = uploaded.read()
        extracted_text = extract_text_from_uploaded_file(safe_name, file_bytes)
        reply = vision_ai_service.summarize_text_fast(
            text=extracted_text,
            subject=subject,
            focus_score=parsed_focus,
        )
    except VisionAIError as exc:
        message = str(exc)
        lower = message.lower()
        if (
            "unsupported file type" in lower
            or "empty" in lower
            or "too large" in lower
            or "no readable text" in lower
            or "unable to read pdf" in lower
            or "parser missing" in lower
        ):
            return jsonify({"error": message}), 400
        return jsonify({"error": message}), 503

    return jsonify(
        {
            "ok": True,
            "action": "upload_summarize",
            "file_name": safe_name,
            "content_length": len(extracted_text),
            "reply": reply,
        }
    )


# ------------------ RUN APP ------------------

if __name__ == "__main__":
    app.run(debug=True, threaded=True)












