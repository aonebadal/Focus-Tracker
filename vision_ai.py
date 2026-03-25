import io
import json
import hashlib
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

try:
    import PyPDF2
except Exception:  # pragma: no cover
    PyPDF2 = None


VISION_SYSTEM_PROMPT = """You are Vision, a smart AI tutor and productivity coach.
You help students with:
- answering questions clearly
- summarizing content
- generating study schedules
- improving focus

Always:
- give structured answers
- use bullet points when needed
- keep answers simple and practical
"""


class VisionAIError(Exception):
    pass


class VisionAIService:
    def __init__(self) -> None:
        self.api_key = str(os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.base_url = self._normalize_deepseek_base_url(
            str(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")).strip()
        )
        self.model = str(os.getenv("DEEPSEEK_MODEL", "deepseek-chat")).strip()
        requested_deepseek_timeout = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "25"))
        self.deepseek_timeout_seconds = max(8.0, min(requested_deepseek_timeout, 45.0))

        self.ollama_base_url = str(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).strip().rstrip("/")
        self.local_model = str(os.getenv("LOCAL_MODEL", "llama3:latest")).strip()
        requested_timeout = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "10"))
        self.ollama_timeout_seconds = max(6.0, min(requested_timeout, 12.0))
        self.ollama_num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "180"))

        raw_order = str(os.getenv("AI_PROVIDER_ORDER", "deepseek,ollama")).strip().lower()
        self.provider_order = [item.strip() for item in raw_order.split(",") if item.strip()]

        self.cache_ttl_seconds = int(os.getenv("VISION_CACHE_TTL_SECONDS", "600"))
        self._response_cache: Dict[str, Tuple[float, str]] = {}
        self._ollama_model_cache_ts = 0.0
        self._ollama_model_cache: List[str] = []
        self.session_ttl_seconds = max(300, int(os.getenv("VISION_SESSION_TTL_SECONDS", "7200")))
        self.max_session_messages = max(8, int(os.getenv("VISION_MAX_SESSION_MESSAGES", "30")))
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _normalize_deepseek_base_url(self, value: str) -> str:
        base = (value or "").strip().rstrip("/")
        if not base:
            return "https://api.deepseek.com/v1"

        # Most DeepSeek setups use /v1/chat/completions.
        if "api.deepseek.com" in base.lower() and not base.lower().endswith("/v1"):
            if base.lower().endswith("/chat/completions"):
                base = base[: -len("/chat/completions")]
            if not base.lower().endswith("/v1"):
                base = f"{base}/v1"
        return base

    def _normalize_whitespace(self, value: str) -> str:
        cleaned = re.sub(r"[ \t]+", " ", value or "")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _new_session_state(self) -> Dict[str, Any]:
        return {
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
            ],
            "updated_at": time.time(),
        }

    def _prune_expired_sessions(self) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if (now - float(state.get("updated_at", 0.0))) > self.session_ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

        # Keep memory bounded if TTL is not enough.
        if len(self._sessions) > 500:
            ordered = sorted(
                self._sessions.items(),
                key=lambda item: float(item[1].get("updated_at", 0.0)),
            )
            for session_id, _ in ordered[: len(self._sessions) - 500]:
                self._sessions.pop(session_id, None)

    def _trim_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not messages:
            return [{"role": "system", "content": VISION_SYSTEM_PROMPT}]

        limit = max(8, self.max_session_messages)
        # Keep system prompt and the most recent turns.
        if len(messages) <= limit:
            return messages

        system = messages[0]
        tail = messages[-(limit - 1):]
        return [system] + tail

    def create_session(self, chat_id: Optional[str] = None) -> str:
        normalized = str(chat_id or "").strip()
        session_id = normalized or uuid.uuid4().hex
        with self._lock:
            self._prune_expired_sessions()
            self._sessions[session_id] = self._new_session_state()
        return session_id

    def reset_session(self, chat_id: str) -> str:
        normalized = str(chat_id or "").strip()
        if not normalized:
            raise VisionAIError("chat_id is required to reset a session.")
        with self._lock:
            self._prune_expired_sessions()
            self._sessions[normalized] = self._new_session_state()
        return normalized

    def _get_or_create_session_state(self, chat_id: str) -> Tuple[str, Dict[str, Any]]:
        normalized = str(chat_id or "").strip() or uuid.uuid4().hex
        with self._lock:
            self._prune_expired_sessions()
            state = self._sessions.get(normalized)
            if state is None:
                state = self._new_session_state()
                self._sessions[normalized] = state
            state["updated_at"] = time.time()
        return normalized, state

    def _build_user_prompt(
        self,
        action: str,
        message: str,
        subject: str,
        study_time: str,
        focus_score: Optional[int],
        text: str,
    ) -> str:
        normalized_action = action.strip().lower()
        focus_text = "unknown" if focus_score is None else str(max(0, min(100, int(focus_score))))
        subject_text = subject.strip() or "General Study"
        time_text = study_time.strip() or "not specified"

        if normalized_action == "study_plan":
            return (
                "Task: Build a practical and detailed study schedule.\n"
                f"Subject: {subject_text}\n"
                f"Available study time: {time_text}\n"
                f"Focus score: {focus_text}\n"
                "Output format:\n"
                "1) Goal for this session\n"
                "2) Time blocks with minutes\n"
                "3) What to revise\n"
                "4) Practice task\n"
                "5) Quick self-test\n"
                "Target length: 160-240 words.\n"
                "User details/request:\n"
                f"{message or 'Create the best plan from the provided subject and time.'}"
            )

        if normalized_action == "summarize":
            source = (text or message or "").strip()
            return (
                "Task: Summarize the following content in a clear student-friendly format.\n"
                f"Subject: {subject_text}\n"
                f"Focus score: {focus_text}\n"
                "Output format:\n"
                "- Summary (6-10 bullets)\n"
                "- Key takeaway (2-3 lines)\n"
                "- One recall question\n"
                "Target length: 140-220 words.\n"
                "Content to summarize:\n"
                f"{source}"
            )

        if normalized_action == "focus_help":
            focus_message = message or "I can't focus."
            return (
                "Task: Student is struggling to focus. Provide immediate actionable help.\n"
                f"Subject: {subject_text}\n"
                f"Focus score: {focus_text}\n"
                "Output format:\n"
                "- Why focus may be dropping (short)\n"
                "- 4 immediate actions (2-5 minutes each)\n"
                "- 1 short restart routine\n"
                "- 1 encouragement line\n"
                "Target length: 120-180 words.\n"
                "User message:\n"
                f"{focus_message}"
            )

        return (
            "Task: Answer the student's question clearly and with enough detail.\n"
            f"Subject: {subject_text}\n"
            f"Focus score: {focus_text}\n"
            "Output structure:\n"
            "1) Direct answer\n"
            "2) Step-by-step explanation\n"
            "3) Example\n"
            "4) One quick practice question\n"
            "Target length: 150-240 words unless the user asks for a short answer.\n"
            "Student question:\n"
            f"{message}"
        )

    def _cache_key(
        self,
        *,
        action: str,
        message: str,
        subject: str,
        study_time: str,
        focus_score: Optional[int],
        text: str,
        chat_id: str = "",
    ) -> str:
        payload = {
            "action": action,
            "message": message,
            "subject": subject,
            "study_time": study_time,
            "focus_score": focus_score,
            "text": text,
            "chat_id": chat_id,
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> Optional[str]:
        item = self._response_cache.get(key)
        if not item:
            return None
        ts, value = item
        if (time.time() - ts) > self.cache_ttl_seconds:
            self._response_cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: str, value: str) -> None:
        self._response_cache[key] = (time.time(), value)
        # Keep memory bounded.
        if len(self._response_cache) > 200:
            oldest_key = min(self._response_cache.items(), key=lambda x: x[1][0])[0]
            self._response_cache.pop(oldest_key, None)

    def _ask_deepseek(self, messages: List[Dict[str, str]]) -> str:
        if not self.api_key:
            raise VisionAIError("DEEPSEEK_API_KEY is not set in environment.")

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 900,
        }

        body = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(
            url=f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urlrequest.urlopen(req, timeout=self.deepseek_timeout_seconds) as response:
                response_body = response.read().decode("utf-8", errors="replace")
        except urlerror.HTTPError as exc:
            if exc.code in {401, 403}:
                raise VisionAIError(
                    "DeepSeek authentication failed. Check DEEPSEEK_API_KEY in your environment."
                ) from exc
            if exc.code == 402:
                raise VisionAIError("DeepSeek quota/billing issue (HTTP 402).") from exc
            raise VisionAIError(f"DeepSeek HTTP error {exc.code}. Please try again later.") from exc
        except urlerror.URLError as exc:
            raise VisionAIError(f"DeepSeek connection error: {exc.reason}") from exc
        except Exception as exc:
            raise VisionAIError(f"DeepSeek request failed: {exc}") from exc

        try:
            parsed = json.loads(response_body)
            content = (
                parsed.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not content:
                raise VisionAIError("DeepSeek returned an empty response.")
            return content
        except VisionAIError:
            raise
        except Exception as exc:
            raise VisionAIError("Invalid response format received from DeepSeek.") from exc

    def _fetch_ollama_models(self) -> List[str]:
        now = time.time()
        if self._ollama_model_cache and (now - self._ollama_model_cache_ts) < 120:
            return self._ollama_model_cache

        models: List[str] = []
        try:
            req = urlrequest.Request(
                url=f"{self.ollama_base_url}/api/tags",
                headers={"Content-Type": "application/json"},
                method="GET",
            )
            with urlrequest.urlopen(req, timeout=5) as response:
                payload = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(payload)
            installed = [
                str(item.get("name", "")).strip()
                for item in parsed.get("models", [])
                if isinstance(item, dict)
            ]
            installed = [item for item in installed if item]

            if self.local_model and self.local_model in installed:
                models.append(self.local_model)
            for candidate in installed:
                if candidate not in models:
                    models.append(candidate)
            if not models and self.local_model:
                models.append(self.local_model)
        except Exception:
            if self.local_model:
                models.append(self.local_model)

        for fallback in ["llama3:latest", "llama3"]:
            if fallback not in models:
                models.append(fallback)

        models = models[:1]
        self._ollama_model_cache = models
        self._ollama_model_cache_ts = now
        return models

    def _ask_ollama(self, messages: List[Dict[str, str]]) -> str:
        models = self._fetch_ollama_models()
        last_error = ""

        for model in models:
            payload: Dict[str, Any] = {
                "model": model,
                "stream": False,
                "keep_alive": "15m",
                "messages": messages,
                "options": {
                    "temperature": 0.35,
                    "num_predict": self.ollama_num_predict,
                },
            }

            body = json.dumps(payload).encode("utf-8")
            req = urlrequest.Request(
                url=f"{self.ollama_base_url}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urlrequest.urlopen(req, timeout=self.ollama_timeout_seconds) as response:
                    response_body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(response_body)
                content = (
                    parsed.get("message", {})
                    .get("content", "")
                    .strip()
                )
                if content:
                    return content
                last_error = f"Ollama returned empty content for model '{model}'."
            except urlerror.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = ""
                detail_low = detail.lower()
                # model-not-found: try next model quickly
                if exc.code == 404 and ("model" in detail_low and "not found" in detail_low):
                    last_error = f"Ollama model '{model}' not found."
                    continue
                last_error = f"Ollama HTTP error {exc.code} for model '{model}'."
            except urlerror.URLError as exc:
                raise VisionAIError(f"Ollama connection error: {exc.reason}") from exc
            except Exception as exc:
                last_error = f"Ollama request failed for '{model}': {exc}"

        raise VisionAIError(last_error or "Ollama did not return a valid response.")

    def _generate_answer(
        self,
        *,
        messages: List[Dict[str, str]],
        action: str,
        message: str,
        subject: str,
        study_time: str,
        focus_score: Optional[int],
        text: str,
    ) -> str:
        order = self.provider_order or ["deepseek", "ollama"]
        errors: List[str] = []

        for provider in order:
            if provider == "deepseek":
                try:
                    return self._ask_deepseek(messages)
                except VisionAIError as exc:
                    errors.append(f"deepseek: {exc}")
            elif provider == "ollama":
                try:
                    return self._ask_ollama(messages)
                except VisionAIError as exc:
                    errors.append(f"ollama: {exc}")

        return self._smart_fallback(
            action=action,
            message=message,
            subject=subject,
            study_time=study_time,
            focus_score=focus_score,
            text=text,
            provider_errors=errors,
        )

    def ask_with_session(
        self,
        *,
        chat_id: str,
        action: str,
        message: str,
        subject: str = "",
        study_time: str = "",
        focus_score: Optional[int] = None,
        text: str = "",
    ) -> Tuple[str, str]:
        normalized_message = self._normalize_whitespace(message)
        normalized_text = self._normalize_whitespace(text)

        # Large note summaries are handled quickly with deterministic local summarization
        # to keep upload UX responsive even when cloud/local LLMs are slow.
        if action == "summarize" and len(normalized_text) > 2500:
            fast_summary = self._rule_based_fallback(
                action=action,
                message=normalized_message,
                subject=subject,
                study_time=study_time,
                focus_score=focus_score,
                text=normalized_text,
            )
            session_id, _ = self._get_or_create_session_state(chat_id)
            return fast_summary, session_id

        user_prompt = self._build_user_prompt(
            action=action,
            message=normalized_message,
            subject=subject,
            study_time=study_time,
            focus_score=focus_score,
            text=normalized_text,
        )

        session_id, _ = self._get_or_create_session_state(chat_id)
        with self._lock:
            state = self._sessions.get(session_id) or self._new_session_state()
            history = list(state.get("messages", []))
            history.append({"role": "user", "content": user_prompt})
            state["messages"] = self._trim_messages(history)
            state["updated_at"] = time.time()
            self._sessions[session_id] = state
            request_messages = [dict(item) for item in state["messages"]]

        answer = self._generate_answer(
            messages=request_messages,
            action=action,
            message=normalized_message,
            subject=subject,
            study_time=study_time,
            focus_score=focus_score,
            text=normalized_text,
        )

        with self._lock:
            state = self._sessions.get(session_id) or self._new_session_state()
            history = list(state.get("messages", []))
            history.append({"role": "assistant", "content": answer})
            state["messages"] = self._trim_messages(history)
            state["updated_at"] = time.time()
            self._sessions[session_id] = state

        return answer, session_id

    def ask(
        self,
        *,
        action: str,
        message: str,
        subject: str = "",
        study_time: str = "",
        focus_score: Optional[int] = None,
        text: str = "",
    ) -> str:
        normalized_message = self._normalize_whitespace(message)
        normalized_text = self._normalize_whitespace(text)

        cache_key = self._cache_key(
            action=action,
            message=normalized_message,
            subject=subject,
            study_time=study_time,
            focus_score=focus_score,
            text=normalized_text,
            chat_id="stateless",
        )
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        # Large note summaries are handled quickly with deterministic local summarization
        # to keep upload UX responsive even when cloud/local LLMs are slow.
        if action == "summarize" and len(normalized_text) > 2500:
            fast_summary = self._rule_based_fallback(
                action=action,
                message=normalized_message,
                subject=subject,
                study_time=study_time,
                focus_score=focus_score,
                text=normalized_text,
            )
            self._cache_set(cache_key, fast_summary)
            return fast_summary

        user_prompt = self._build_user_prompt(
            action=action,
            message=normalized_message,
            subject=subject,
            study_time=study_time,
            focus_score=focus_score,
            text=normalized_text,
        )
        request_messages = [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        answer = self._generate_answer(
            messages=request_messages,
            action=action,
            message=normalized_message,
            subject=subject,
            study_time=study_time,
            focus_score=focus_score,
            text=normalized_text,
        )
        self._cache_set(cache_key, answer)
        return answer

    def _smart_fallback(
        self,
        *,
        action: str,
        message: str,
        subject: str,
        study_time: str,
        focus_score: Optional[int],
        text: str,
        provider_errors: List[str],
    ) -> str:
        if action == "chat":
            targeted = self._topic_specific_fallback(message=message)
            if targeted:
                return targeted

            topic = self._extract_topic(message)
            wiki = self._fetch_wikipedia_summary(topic)
            if wiki:
                return self._format_wiki_tutor_answer(topic=topic, summary=wiki, subject=subject)
            return self._chat_fallback(message=message, subject=subject, provider_errors=provider_errors)

        return self._rule_based_fallback(
            action=action,
            message=message,
            subject=subject,
            study_time=study_time,
            focus_score=focus_score,
            text=text,
        )

    def _topic_specific_fallback(self, *, message: str) -> str:
        prompt = self._normalize_whitespace(message).lower()
        if not prompt:
            return ""

        if "lambda" in prompt and "java" in prompt:
            return (
                "Use a lambda in Java by implementing a functional interface (an interface with exactly one abstract method).\n"
                "Syntax: `(parameters) -> expression` or `(parameters) -> { statements; }`.\n"
                "\n"
                "Example 1 (custom functional interface):\n"
                "@FunctionalInterface\n"
                "interface MathOp {\n"
                "    int apply(int a, int b);\n"
                "}\n"
                "\n"
                "MathOp add = (a, b) -> a + b;\n"
                "System.out.println(add.apply(5, 3)); // 8\n"
                "Example 2 (Streams):\n"
                "List<Integer> nums = Arrays.asList(1, 2, 3, 4, 5, 6);\n"
                "List<Integer> evens = nums.stream()\n"
                "    .filter(n -> n % 2 == 0)\n"
                "    .map(n -> n * 10)\n"
                "    .toList();\n"
                "Quick rule: use lambdas when you pass behavior (predicate, mapper, callback) as an argument."
            )

        if "jdk" in prompt and "jre" in prompt and "difference" in prompt:
            return (
                "JDK is for building Java programs; JRE is for running them.\n"
                "- JDK = JRE + compiler (`javac`) + developer tools.\n"
                "- JRE = JVM + core libraries needed to execute Java apps.\n"
                "If you are writing code, install JDK. If you only run apps, JRE is enough."
            )

        return ""

    def _chat_fallback(self, *, message: str, subject: str, provider_errors: List[str]) -> str:
        question = self._normalize_whitespace(message) or "your topic"
        subject_text = subject.strip() or "General Study Help"
        error_note = ""
        if provider_errors:
            error_note = (
                "Note: Live AI provider was unavailable for this request, so this is a local tutor answer.\n\n"
            )

        return (
            f"{error_note}Here is a practical answer for: {question}\n"
            f"Subject context: {subject_text}\n"
            "1) Start with the core definition in one line.\n"
            "2) Break the task into 3 small steps.\n"
            "3) Write one short worked example.\n"
            "4) Solve one practice problem and explain your steps aloud.\n"
            "If you want coding help, include language + expected output and I will give exact code."
        )

    def _extract_topic(self, message: str) -> str:
        value = (message or "").strip()
        if not value:
            return "study skills"

        cleaned = re.sub(
            r"^(what is|what are|what's|explain|define|tell me about|can you explain|how to|how do i|how can i)\s+",
            "",
            value,
            flags=re.IGNORECASE,
        )
        cleaned = cleaned.strip(" ?.!")
        if len(cleaned) < 2:
            return value[:60]
        # Use first phrase as page candidate.
        tokens = cleaned.split()
        return " ".join(tokens[:6])

    def _fetch_wikipedia_summary(self, topic: str) -> str:
        title = (topic or "").strip()
        if not title:
            return ""

        candidates = [title]
        if "(" not in title:
            candidates.extend(
                [
                    f"{title} (computer science)",
                    f"{title} (software)",
                ]
            )

        for candidate in candidates:
            url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urlparse.quote(candidate)
            req = urlrequest.Request(
                url=url,
                headers={"Accept": "application/json", "User-Agent": "VisionTutor/1.0"},
                method="GET",
            )
            try:
                with urlrequest.urlopen(req, timeout=5) as response:
                    body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                extract = str(parsed.get("extract", "") or "").strip()
                if not extract:
                    continue
                if "may refer to" in extract.lower():
                    continue
                return extract[:1200]
            except Exception:
                continue
        return ""

    def _format_wiki_tutor_answer(self, *, topic: str, summary: str, subject: str) -> str:
        text = self._normalize_whitespace(summary)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        intro = sentences[0] if sentences else text[:180]
        detail = sentences[1] if len(sentences) > 1 else ""

        example = f"In {subject or 'study work'}, think of {topic} as a way to split tasks so work can progress efficiently."
        practice = f"Practice: Explain {topic} in your own words, then write one real-world use case."

        chunks = [f"{topic.title()} is {intro}"]
        if detail:
            chunks.append(detail)
        chunks.append(f"Example: {example}")
        chunks.append(practice)
        return "\n\n".join(chunks)

    def summarize_text_fast(self, text: str, subject: str = "", focus_score: Optional[int] = None) -> str:
        source = self._normalize_whitespace(text or "")
        if not source:
            raise VisionAIError("No text found to summarize.")

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", source) if s.strip()]
        picked: List[str] = []
        seen = set()
        for sentence in sentences:
            key = sentence.lower()
            if key in seen:
                continue
            seen.add(key)
            picked.append(sentence)
            if len(picked) >= 8:
                break

        if not picked:
            picked = [source[:220]]

        bullets = "\n".join(f"- {line}" for line in picked[:6])
        subject_text = subject.strip() or "your subject"
        focus = "low" if (focus_score is not None and int(focus_score) < 40) else "normal"

        tip = (
            "Read one bullet at a time and pause 10 seconds to recall it."
            if focus == "low"
            else "After reading, explain the summary aloud once for retention."
        )

        return (
            "Summary:\n"
            f"{bullets}\n"
            f"Key takeaway: This text mainly explains core points in {subject_text}; focus on definitions and process flow.\n"
            "Recall question: Which point is most important for solving problems, and why?\n"
            f"Study tip: {tip}"
        )

    def _rule_based_fallback(
        self,
        *,
        action: str,
        message: str,
        subject: str,
        study_time: str,
        focus_score: Optional[int],
        text: str,
    ) -> str:
        subject_text = subject.strip() or "General Study"
        focus = 0 if focus_score is None else max(0, min(100, int(focus_score)))

        if action == "focus_help":
            return (
                f"Focus reset plan for {subject_text}:\n"
                "- Do 90 seconds of deep breathing (inhale 4s, exhale 6s).\n"
                "- Remove one distraction now (mute phone or close extra tabs).\n"
                "- Start a 15-minute micro-session on one small task.\n"
                "- After 15 minutes, take a 2-minute break and restart.\n"
                "- Write one sentence: 'My next target is ____.'\n"
                + ("- Keep it simple until your focus rises above 50.\n" if focus < 50 else "- Keep momentum with one challenging problem.\n")
                + "Quick check: what single task will you finish in the next 15 minutes?"
            )

        if action == "study_plan":
            duration = study_time.strip() or "2 hours"
            return (
                f"Study plan for {subject_text} ({duration}):\n"
                "- Block 1 (25 min): Learn core concept from notes.\n"
                "- Break (5 min): walk, hydrate, no social media.\n"
                "- Block 2 (25 min): Solve 3 practice questions.\n"
                "- Break (5 min): quick breathing reset.\n"
                "- Block 3 (25 min): Review mistakes and make short revision notes.\n"
                "- Block 4 (20 min): Self-test + explain topic in your own words.\n"
                "- Final (10 min): Write tomorrow’s priority topic.\n"
                "Practice prompt: teach one concept in 5 lines without looking at notes."
            )

        if action == "summarize":
            source = (text or message).strip()
            if not source:
                return "Please provide text or upload a file so I can summarize it."
            sentences = re.split(r"(?<=[.!?])\s+", source)
            top = [s.strip() for s in sentences if s.strip()][:8]
            if not top:
                top = [source[:300]]
            bullets = "\n".join(f"- {line}" for line in top[:6])
            return (
                "Summary:\n"
                f"{bullets}\n"
                "Key takeaway: Focus on the main definitions, process flow, and one practical use-case.\n"
                "Recall question: Which point in this summary is most likely to appear in an exam, and why?"
            )

        question = message or "Explain this topic."
        return (
            f"Here is a practical explanation for: {question}\n"
            f"Subject: {subject_text}\n"
            "1) Define the core concept in one sentence.\n"
            "2) Break it into smaller steps.\n"
            "3) Apply one short real example.\n"
            "4) Test yourself with one practice question."
        )


def extract_text_from_uploaded_file(file_name: str, file_bytes: bytes) -> str:
    extension = os.path.splitext(str(file_name or "").lower())[1]
    if extension not in {".pdf", ".txt", ".md"}:
        raise VisionAIError("Unsupported file type. Upload PDF, TXT, or MD.")

    if not file_bytes:
        raise VisionAIError("Uploaded file is empty.")

    if len(file_bytes) > 10 * 1024 * 1024:
        raise VisionAIError("File too large. Max upload size is 10MB.")

    if extension == ".pdf":
        if PyPDF2 is None:
            raise VisionAIError("PDF parser missing. Install PyPDF2.")
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            text = "\n".join(pages).strip()
        except Exception as exc:
            raise VisionAIError(f"Unable to read PDF: {exc}") from exc
    else:
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1", errors="ignore")

    normalized = re.sub(r"[ \t]+", " ", text or "")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if not normalized:
        raise VisionAIError("No readable text found in uploaded file.")

    # Keep model input efficient.
    return normalized[:40_000]
