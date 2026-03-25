(() => {
  const startBtn = document.getElementById("startBtn");
  const stopBtn = document.getElementById("stopBtn");
  const notice = document.getElementById("notice");
  const alertSound = document.getElementById("alertSound");
  const adaptiveMusic = document.getElementById("adaptiveMusic");
  const webcamPreview = document.getElementById("webcamPreview");
  const cameraStatus = document.getElementById("cameraStatus");

  const gaugeRing = document.getElementById("focusGauge");
  const gaugeValue = document.getElementById("focusValue");
  const focusProgressFill = document.getElementById("focusProgressFill");
  const predictedValue = document.getElementById("predictedValue");
  const predictionStatus = document.getElementById("predictionStatus");

  const envMode = document.getElementById("envMode");
  const lightColor = document.getElementById("lightColor");
  const fanSpeed = document.getElementById("fanSpeed");
  const musicState = document.getElementById("musicState");

  const brainwaveWave = document.getElementById("brainwaveWave");
  const brainwaveBeat = document.getElementById("brainwaveBeat");
  const brainwaveCarrier = document.getElementById("brainwaveCarrier");
  const brainwaveToggle = document.getElementById("brainwaveToggle");

  const avgFocus = document.getElementById("avgFocus");
  const bestSession = document.getElementById("bestSession");
  const worstSession = document.getElementById("worstSession");
  const distractionFreq = document.getElementById("distractionFreq");
  const cameraFps = document.getElementById("cameraFps");

  const subjectSelect = document.getElementById("subjectSelect");
  const pomodoroBtn = document.getElementById("pomodoroBtn");
  const pomodoroCountdownEl = document.getElementById("pomodoroCountdown");
  const pomodoroStatusEl = document.getElementById("pomodoroStatus");
  const pomodoroToggleBtn = document.getElementById("pomodoroToggleBtn");
  const pomodoroResetBtn = document.getElementById("pomodoroResetBtn");
  const stopwatchEl = document.getElementById("pomodoroStopwatch");
  const stopwatchToggleBtn = document.getElementById("stopwatchToggleBtn");
  const stopwatchResetBtn = document.getElementById("stopwatchResetBtn");

  let tracking = false;
  let poller = null;
  let lowFocusActive = false;
  let lowFocusAlertPlayed = false;
  let backgroundMusicVolume = 0.35;
  let lastFocusScore = 0;
  let currentBrainwave = {
    enabled: true,
    wave: "delta",
    label: "Delta Waves",
    beat_hz: 2,
    carrier_hz: 180,
    left_hz: 179,
    right_hz: 181,
    volume: 0.18,
  };

  const CAMERA_WIDTH = 640;
  const CAMERA_HEIGHT = 480;
  const FRAME_UPLOAD_INTERVAL_MS = 100;

  let cameraStream = null;
  let cameraReady = false;
  let cameraInitPromise = null;
  let cameraErrorMessage = "";
  let activeCaptureMode = "server_camera";
  let frameUploader = null;
  let frameUploadInFlight = false;
  const esp32BaseUrl = String(window.ESP32_BASE_URL || "")
    .trim()
    .replace(/\/+$/, "");
  let lastEsp32FocusSent = -1;
  let lastEsp32SendMs = 0;
  let esp32SendWarned = false;
  const POMODORO_DURATION_SECONDS = 25 * 60;
  let pomodoroRemainingSeconds = POMODORO_DURATION_SECONDS;
  let pomodoroRunning = false;
  let pomodoroTimerId = null;
  let stopwatchElapsedSeconds = 0;
  let stopwatchRunning = false;
  let stopwatchTimerId = null;

  const frameCanvas = document.createElement("canvas");
  frameCanvas.width = CAMERA_WIDTH;
  frameCanvas.height = CAMERA_HEIGHT;
  const frameContext = frameCanvas.getContext("2d", { alpha: false });

  const DASHBOARD_THEME = {
    primary: "#0F766E",
    secondary: "#0EA5E9",
    accent: "#94A3B8",
    text: "#0F172A",
    muted: "#475569",
    lime: "#22C55E",
    grid: "rgba(71, 85, 105, 0.24)",
  };

  if (alertSound) {
    alertSound.volume = 1.0;
    alertSound.loop = false;
    alertSound.preload = "auto";
  }

  if (adaptiveMusic) {
    adaptiveMusic.volume = backgroundMusicVolume;
  }
  function setCameraStatus(message, level = "muted") {
    if (!cameraStatus) {
      return;
    }

    cameraStatus.classList.remove("ok-text", "warn-text", "alert-text");
    if (level === "ok") {
      cameraStatus.classList.add("ok-text");
    } else if (level === "warn") {
      cameraStatus.classList.add("warn-text");
    } else if (level === "alert") {
      cameraStatus.classList.add("alert-text");
    }

    cameraStatus.textContent = message;
  }

  function cameraErrorToMessage(error) {
    const name = String(error?.name || "");
    if (name === "NotAllowedError" || name === "SecurityError") {
      return "Camera permission denied. Allow camera access and reload.";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return "No camera found. Connect a webcam and retry.";
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return "Camera is busy in another app. Close it and retry.";
    }
    return "Unable to initialize camera. Tracking can still run with server camera mode.";
  }

  async function initializeCameraOnLoad() {
    if (cameraInitPromise) {
      return cameraInitPromise;
    }

    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      cameraReady = false;
      cameraErrorMessage = "Browser does not support getUserMedia.";
      setCameraStatus(cameraErrorMessage, "warn");
      return Promise.resolve(false);
    }

    setCameraStatus("Requesting webcam permission...", "muted");

    cameraInitPromise = navigator.mediaDevices
      .getUserMedia({
        video: {
          width: { ideal: CAMERA_WIDTH, max: CAMERA_WIDTH },
          height: { ideal: CAMERA_HEIGHT, max: CAMERA_HEIGHT },
          frameRate: { ideal: 30, max: 30 },
          facingMode: "user",
        },
        audio: false,
      })
      .then(async (stream) => {
        cameraStream = stream;
        cameraReady = true;
        cameraErrorMessage = "";

        if (webcamPreview) {
          webcamPreview.srcObject = stream;
          await webcamPreview.play().catch(() => {
            // Some browsers require a user gesture. Stream is still initialized.
          });
        }

        setCameraStatus("Webcam ready (640x480). Start tracking anytime.", "ok");
        return true;
      })
      .catch((error) => {
        cameraReady = false;
        cameraErrorMessage = cameraErrorToMessage(error);
        // Allow retry on explicit user action (Start button click).
        cameraInitPromise = null;
        setCameraStatus(cameraErrorMessage, "alert");
        return false;
      });

    return cameraInitPromise;
  }

  async function uploadBrowserFrame() {
    if (!tracking || activeCaptureMode !== "browser_stream" || !cameraReady || frameUploadInFlight) {
      return;
    }

    if (!webcamPreview || webcamPreview.readyState < 2 || !frameContext) {
      return;
    }

    frameUploadInFlight = true;

    try {
      frameContext.drawImage(webcamPreview, 0, 0, CAMERA_WIDTH, CAMERA_HEIGHT);
      const image = frameCanvas.toDataURL("image/jpeg", 0.62);

      const response = await fetch("/focus/frame", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image }),
      });

      if (!response.ok) {
        if (response.status >= 500) {
          setCameraStatus("Frame processing latency detected. Retrying...", "warn");
        }
        return;
      }

      if (cameraErrorMessage) {
        cameraErrorMessage = "";
        setCameraStatus("Live frame stream active.", "ok");
      }
    } catch (error) {
      cameraErrorMessage = "Frame upload interrupted.";
      setCameraStatus(cameraErrorMessage, "warn");
    } finally {
      frameUploadInFlight = false;
    }
  }

  function startFrameUploadLoop() {
    stopFrameUploadLoop();

    if (!cameraReady) {
      return;
    }

    frameUploader = setInterval(() => {
      uploadBrowserFrame().catch(() => {
        setCameraStatus("Unable to send webcam frames.", "warn");
      });
    }, FRAME_UPLOAD_INTERVAL_MS);
  }

  function stopFrameUploadLoop() {
    if (frameUploader) {
      clearInterval(frameUploader);
      frameUploader = null;
    }
    frameUploadInFlight = false;
  }

  class BinauralSynth {
    constructor() {
      this.context = null;
      this.masterGain = null;
      this.leftOsc = null;
      this.rightOsc = null;
      this.leftGain = null;
      this.rightGain = null;
      this.leftPanner = null;
      this.rightPanner = null;
      this.started = false;
    }

    async ensureContext() {
      if (!window.AudioContext && !window.webkitAudioContext) {
        return;
      }

      if (!this.context) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        this.context = new Ctx();
        this.masterGain = this.context.createGain();
        this.masterGain.gain.value = 0;
        this.masterGain.connect(this.context.destination);
      }

      if (this.context.state === "suspended") {
        await this.context.resume();
      }
    }

    ensureOscillators() {
      if (this.started || !this.context || !this.masterGain) {
        return;
      }

      this.leftOsc = this.context.createOscillator();
      this.rightOsc = this.context.createOscillator();
      this.leftOsc.type = "sine";
      this.rightOsc.type = "sine";

      this.leftGain = this.context.createGain();
      this.rightGain = this.context.createGain();
      this.leftGain.gain.value = 0.5;
      this.rightGain.gain.value = 0.5;

      this.leftPanner = this.context.createStereoPanner();
      this.rightPanner = this.context.createStereoPanner();
      this.leftPanner.pan.value = -1;
      this.rightPanner.pan.value = 1;

      this.leftOsc.connect(this.leftGain);
      this.leftGain.connect(this.leftPanner);
      this.leftPanner.connect(this.masterGain);

      this.rightOsc.connect(this.rightGain);
      this.rightGain.connect(this.rightPanner);
      this.rightPanner.connect(this.masterGain);

      this.leftOsc.start();
      this.rightOsc.start();
      this.started = true;
    }

    async apply(brainwaveState, shouldPlay) {
      await this.ensureContext();
      if (!this.context || !this.masterGain) {
        return;
      }

      this.ensureOscillators();

      if (!this.leftOsc || !this.rightOsc) {
        return;
      }

      const leftHz = Math.max(20, Number(brainwaveState?.left_hz || 180));
      const rightHz = Math.max(20, Number(brainwaveState?.right_hz || 182));
      const enabled = Boolean(brainwaveState?.enabled);
      const volume = Math.max(0, Math.min(1, Number(brainwaveState?.volume || 0.18)));

      this.leftOsc.frequency.setTargetAtTime(leftHz, this.context.currentTime, 0.05);
      this.rightOsc.frequency.setTargetAtTime(rightHz, this.context.currentTime, 0.05);

      const targetGain = shouldPlay && enabled ? volume * 0.22 : 0;
      this.masterGain.gain.setTargetAtTime(targetGain, this.context.currentTime, 0.08);
    }

    async mute() {
      if (!this.context || !this.masterGain) {
        return;
      }
      this.masterGain.gain.setTargetAtTime(0, this.context.currentTime, 0.05);
    }
  }

  const binauralSynth = new BinauralSynth();

  const focusChart = new Chart(document.getElementById("focusChart"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Focus Score",
          data: [],
          borderColor: DASHBOARD_THEME.primary,
          borderWidth: 3,
          pointRadius: 0,
          tension: 0.38,
          fill: true,
          backgroundColor: "rgba(113, 111, 243, 0.12)",
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: DASHBOARD_THEME.text } },
        tooltip: {
          enabled: true,
          backgroundColor: "#FFFFFF",
          titleColor: DASHBOARD_THEME.text,
          bodyColor: DASHBOARD_THEME.muted,
          borderColor: DASHBOARD_THEME.accent,
          borderWidth: 1,
          displayColors: false,
        },
      },
      scales: {
        x: {
          ticks: { color: DASHBOARD_THEME.muted, maxTicksLimit: 8 },
          grid: { color: DASHBOARD_THEME.grid },
        },
        y: {
          min: 0,
          max: 100,
          ticks: { color: DASHBOARD_THEME.muted },
          grid: { color: DASHBOARD_THEME.grid },
        },
      },
    },
  });

  const distributionChart = new Chart(document.getElementById("distributionChart"), {
    type: "doughnut",
    data: {
      labels: ["High", "Medium", "Low"],
      datasets: [
        {
          data: [0, 0, 0],
          backgroundColor: [DASHBOARD_THEME.lime, DASHBOARD_THEME.secondary, DASHBOARD_THEME.accent],
          borderColor: "#FFFFFF",
          borderWidth: 2,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: DASHBOARD_THEME.text } },
        tooltip: {
          enabled: true,
          backgroundColor: "#FFFFFF",
          titleColor: DASHBOARD_THEME.text,
          bodyColor: DASHBOARD_THEME.muted,
          borderColor: DASHBOARD_THEME.accent,
          borderWidth: 1,
        },
      },
    },
  });

  function setGauge(score) {
    const safe = Math.max(0, Math.min(100, Number(score) || 0));
    gaugeValue.textContent = String(Math.round(safe));

    const color = safe > 70 ? DASHBOARD_THEME.lime : safe > 40 ? DASHBOARD_THEME.secondary : DASHBOARD_THEME.accent;
    const angle = Math.round((safe / 100) * 360);
    gaugeRing.style.background = `conic-gradient(${color} ${angle}deg, rgba(179, 180, 204, 0.30) ${angle}deg)`;

    if (focusProgressFill) {
      focusProgressFill.style.width = `${safe}%`;
    }
  }

  function updateHistoryChart(history) {
    const labels = history.map((point) => point.time);
    const values = history.map((point) => Number(point.score) || 0);

    focusChart.data.labels = labels;
    focusChart.data.datasets[0].data = values;
    focusChart.update();
  }

  function updateDistributionChart(distribution) {
    distributionChart.data.datasets[0].data = [
      Number(distribution?.high || 0),
      Number(distribution?.medium || 0),
      Number(distribution?.low || 0),
    ];
    distributionChart.update();
  }

  function updateEnvironment(environment) {
    const modeText = environment?.mode || "idle";
    const lightText = environment?.light_color || "off";
    const fanText = environment?.fan_speed || "off";
    const musicText = environment?.music_state?.label || environment?.music_state?.mode || "off";

    envMode.textContent = modeText;
    lightColor.textContent = lightText;
    fanSpeed.textContent = fanText;
    musicState.textContent = musicText;

    syncAdaptiveMusic(environment?.music_state);
  }

  function updateBrainwave(brainwave) {
    if (!brainwave || typeof brainwave !== "object") {
      return;
    }

    currentBrainwave = { ...currentBrainwave, ...brainwave };

    brainwaveWave.textContent = currentBrainwave.label || currentBrainwave.wave || "delta";
    brainwaveBeat.textContent = `${Number(currentBrainwave.beat_hz || 0).toFixed(1)} Hz`;
    brainwaveCarrier.textContent = `${Number(currentBrainwave.carrier_hz || 0).toFixed(1)} Hz`;

    if (brainwaveToggle) {
      brainwaveToggle.textContent = currentBrainwave.enabled
        ? "Disable Brainwave Audio"
        : "Enable Brainwave Audio";
    }

    binauralSynth.apply(currentBrainwave, tracking).catch(() => {
      // Browser may block audio until explicit gesture.
    });
  }

  function updateAnalytics(analytics) {
    avgFocus.textContent = `${Number(analytics?.average_focus || 0)}%`;

    const best = analytics?.best_session || {};
    const worst = analytics?.worst_session || {};

    bestSession.textContent = Number(best.index || 0) > 0 ? `S${best.index}: ${best.score}%` : "-";
    worstSession.textContent = Number(worst.index || 0) > 0 ? `S${worst.index}: ${worst.score}%` : "-";

    distractionFreq.textContent = `${Number(analytics?.distraction_frequency_per_minute || 0).toFixed(2)}/min`;
    cameraFps.textContent = `${Number(analytics?.camera_fps || 0).toFixed(1)} fps`;
  }

  function updatePrediction(prediction) {
    const score = Number(prediction?.predicted_score || 0);
    const confidence = Number(prediction?.confidence || 0);

    predictedValue.textContent = String(Math.round(score));

    if (prediction?.drop_expected) {
      predictionStatus.textContent = `Drop risk in 10s (${Math.round(confidence * 100)}% confidence)`;
      predictionStatus.classList.add("prediction-alert");
    } else {
      predictionStatus.textContent = `Stable trend (${Math.round(confidence * 100)}% confidence)`;
      predictionStatus.classList.remove("prediction-alert");
    }
  }

  function syncAdaptiveMusic(music) {
    if (!music || !adaptiveMusic) {
      return;
    }

    backgroundMusicVolume = currentBrainwave.enabled ? 0.24 : 0.35;
    adaptiveMusic.volume = backgroundMusicVolume;

    const hasTrack = typeof music.track_url === "string" && music.track_url.length > 0;

    if (hasTrack) {
      const expectedSrc = `/${music.track_url.replace(/^\/+/, "")}`;
      if (!adaptiveMusic.src.endsWith(expectedSrc)) {
        adaptiveMusic.src = expectedSrc;
      }

      if (tracking && adaptiveMusic.paused) {
        adaptiveMusic.play().catch(() => {
          // Browser autoplay policies may block this until user interacts again.
        });
      }
      return;
    }

    if (music.mode === "stopped" || !tracking) {
      adaptiveMusic.pause();
    }
  }

  function stopAndResetAlertSound() {
    if (!alertSound) {
      return;
    }
    alertSound.pause();
    alertSound.currentTime = 0;
  }

  function showLowFocus(suggestion) {
    const message = suggestion || "Low focus detected. Try a quick reset technique.";
    notice.innerHTML = `<span class="alert-text">Low Focus Detected - <a href="/improve">Improve</a><br>${message}</span>`;

    if (lowFocusAlertPlayed || !alertSound) {
      return;
    }

    lowFocusAlertPlayed = true;
    const previousMusicVolume = adaptiveMusic ? adaptiveMusic.volume : null;

    if (adaptiveMusic) {
      adaptiveMusic.volume = Math.min(0.12, adaptiveMusic.volume);
    }

    alertSound.currentTime = 0;
    alertSound.play().catch(() => {
      if (adaptiveMusic && previousMusicVolume !== null) {
        adaptiveMusic.volume = previousMusicVolume;
      }
    });

    setTimeout(() => {
      stopAndResetAlertSound();
      if (adaptiveMusic && previousMusicVolume !== null && tracking) {
        adaptiveMusic.volume = previousMusicVolume;
      }
    }, 1100);
  }

  function clearNotice() {
    notice.textContent = "";
  }

  function getSelectedSubject() {
    return subjectSelect?.value || "Programming";
  }

  function formatClock(totalSeconds) {
    const safe = Math.max(0, Number(totalSeconds) || 0);
    const mins = Math.floor(safe / 60);
    const secs = safe % 60;
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }

  function syncPomodoroUi() {
    if (pomodoroCountdownEl) {
      pomodoroCountdownEl.textContent = formatClock(pomodoroRemainingSeconds);
    }
    if (pomodoroToggleBtn) {
      pomodoroToggleBtn.textContent = pomodoroRunning ? "Pause Timer" : "Start Timer";
    }
    if (pomodoroStatusEl && !pomodoroRunning && pomodoroRemainingSeconds === POMODORO_DURATION_SECONDS) {
      pomodoroStatusEl.textContent = "Ready for a 25-minute deep work block.";
    }
  }

  function clearPomodoroInterval() {
    if (pomodoroTimerId) {
      clearInterval(pomodoroTimerId);
      pomodoroTimerId = null;
    }
  }

  function startPomodoroCountdown() {
    if (pomodoroRunning) {
      return;
    }

    pomodoroRunning = true;
    syncPomodoroUi();

    clearPomodoroInterval();
    pomodoroTimerId = setInterval(() => {
      pomodoroRemainingSeconds = Math.max(0, pomodoroRemainingSeconds - 1);
      syncPomodoroUi();

      if (pomodoroRemainingSeconds <= 0) {
        clearPomodoroInterval();
        pomodoroRunning = false;
        if (pomodoroStatusEl) {
          pomodoroStatusEl.textContent = "Pomodoro complete. Take a mindful 5-minute break.";
        }
        stopAndResetAlertSound();
        if (alertSound) {
          alertSound.currentTime = 0;
          alertSound.play().catch(() => {});
          setTimeout(() => {
            stopAndResetAlertSound();
          }, 1000);
        }
        syncPomodoroUi();
      }
    }, 1000);
  }

  function pausePomodoroCountdown() {
    if (!pomodoroRunning) {
      return;
    }
    pomodoroRunning = false;
    clearPomodoroInterval();
    if (pomodoroStatusEl) {
      pomodoroStatusEl.textContent = "Pomodoro paused.";
    }
    syncPomodoroUi();
  }

  function resetPomodoroCountdown() {
    pomodoroRunning = false;
    clearPomodoroInterval();
    pomodoroRemainingSeconds = POMODORO_DURATION_SECONDS;
    syncPomodoroUi();
  }

  function syncStopwatchUi() {
    if (stopwatchEl) {
      stopwatchEl.textContent = formatClock(stopwatchElapsedSeconds);
    }
    if (stopwatchToggleBtn) {
      stopwatchToggleBtn.textContent = stopwatchRunning ? "Pause Stopwatch" : "Start Stopwatch";
    }
  }

  function clearStopwatchInterval() {
    if (stopwatchTimerId) {
      clearInterval(stopwatchTimerId);
      stopwatchTimerId = null;
    }
  }

  function startStopwatch() {
    if (stopwatchRunning) {
      return;
    }
    stopwatchRunning = true;
    syncStopwatchUi();
    clearStopwatchInterval();
    stopwatchTimerId = setInterval(() => {
      stopwatchElapsedSeconds += 1;
      syncStopwatchUi();
    }, 1000);
  }

  function pauseStopwatch() {
    if (!stopwatchRunning) {
      return;
    }
    stopwatchRunning = false;
    clearStopwatchInterval();
    syncStopwatchUi();
  }

  function resetStopwatch() {
    stopwatchRunning = false;
    clearStopwatchInterval();
    stopwatchElapsedSeconds = 0;
    syncStopwatchUi();
  }

  async function sendFocusToEsp32(score) {
    if (!esp32BaseUrl) {
      return;
    }

    const focus = Math.max(0, Math.min(100, Math.round(Number(score) || 0)));
    const now = Date.now();
    if (focus === lastEsp32FocusSent && now - lastEsp32SendMs < 250) {
      return;
    }

    lastEsp32FocusSent = focus;
    lastEsp32SendMs = now;

    const aborter = new AbortController();
    const timeoutId = setTimeout(() => aborter.abort(), 700);

    try {
      await fetch(`${esp32BaseUrl}/set?focus=${focus}`, {
        method: "GET",
        mode: "cors",
        cache: "no-store",
        signal: aborter.signal,
      });
      esp32SendWarned = false;
    } catch (error) {
      if (!esp32SendWarned) {
        esp32SendWarned = true;
        console.warn("ESP32 focus push failed:", error);
      }
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async function startPomodoroSession() {
    const subject = getSelectedSubject();

    const response = await fetch("/pomodoro/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject,
        duration_minutes: 25,
      }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const errorMessage = data.error || "Unable to start Pomodoro session.";
      notice.innerHTML = `<span class="warn-text">${errorMessage}</span>`;
      return;
    }

    const message = String(data.message || `Start 25 minute deep study session on ${subject}.`);
    notice.innerHTML = `<span class="ok-text">${message}</span>`;
    if (pomodoroStatusEl) {
      pomodoroStatusEl.textContent = `Running: ${subject} session in progress.`;
    }
    startPomodoroCountdown();
    if (!stopwatchRunning) {
      startStopwatch();
    }
  }

  async function updateDashboard() {
    try {
      const response = await fetch("/focus", { cache: "no-store" });
      if (!response.ok) {
        return;
      }

      const data = await response.json();

      const focusScore = Number(data.focus || 0);
      lastFocusScore = focusScore;
      if (tracking) {
        sendFocusToEsp32(focusScore).catch(() => {});
      }

      setGauge(focusScore);
      updatePrediction(data.prediction || {});
      updateEnvironment(data.environment || {});
      updateBrainwave(data.brainwave || {});
      updateHistoryChart(Array.isArray(data.history) ? data.history : []);
      updateDistributionChart(data.distribution || {});
      updateAnalytics(data.analytics || {});

      if (focusScore < 40) {
        if (!lowFocusActive) {
          lowFocusActive = true;
          lowFocusAlertPlayed = false;
        }
        showLowFocus(data.environment?.suggestion);
      } else if (data.prediction?.drop_expected) {
        lowFocusActive = false;
        lowFocusAlertPlayed = false;
        stopAndResetAlertSound();
        notice.innerHTML = `<span class="warn-text">Focus drop predicted in 10s. Stabilize now.</span>`;
      } else {
        lowFocusActive = false;
        lowFocusAlertPlayed = false;
        stopAndResetAlertSound();
        clearNotice();
      }
    } catch (error) {
      notice.innerHTML = `<span class="warn-text">Connection issue while fetching focus stream.</span>`;
    }
  }

  async function toggleBrainwave() {
    const desiredState = !Boolean(currentBrainwave.enabled);

    const response = await fetch("/brainwave/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: desiredState }),
    });

    if (!response.ok) {
      notice.innerHTML = `<span class="warn-text">Unable to toggle brainwave audio.</span>`;
      return;
    }

    const data = await response.json();
    updateBrainwave(data.brainwave || {});
  }

  async function startTracking() {
    if (tracking) {
      return;
    }

    await initializeCameraOnLoad();

    const mode = cameraReady ? "browser_stream" : "server_camera";

    const response = await fetch("/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });

    if (!response.ok) {
      notice.innerHTML = `<span class="warn-text">Unable to start tracking.</span>`;
      return;
    }

    const data = await response.json();

    if (!data.started) {
      notice.innerHTML = `<span class="warn-text">Tracking is already running.</span>`;
      return;
    }

    activeCaptureMode = data.mode || mode;
    tracking = true;
    startBtn.disabled = true;
    stopBtn.disabled = false;

    if (activeCaptureMode === "browser_stream") {
      startFrameUploadLoop();
      setCameraStatus("Tracking active (browser webcam stream).", "ok");
    } else if (cameraErrorMessage) {
      setCameraStatus("Tracking active in server camera mode. " + cameraErrorMessage, "warn");
    } else {
      setCameraStatus("Tracking active in server camera mode.", "warn");
    }

    if (data.brainwave) {
      updateBrainwave(data.brainwave);
    }

    await updateDashboard();
    poller = setInterval(updateDashboard, 1000);
  }
  async function stopTracking() {
    if (!tracking) {
      return;
    }

    tracking = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;

    if (poller) {
      clearInterval(poller);
      poller = null;
    }

    stopFrameUploadLoop();
    activeCaptureMode = "server_camera";

    adaptiveMusic.pause();
    await binauralSynth.mute();
    lowFocusActive = false;
    lowFocusAlertPlayed = false;
    stopAndResetAlertSound();

    const response = await fetch("/stop", { method: "POST" });
    if (!response.ok) {
      return;
    }

    const data = await response.json();
    const average = Number(data.average || 0);
    if (data.saved) {
      notice.innerHTML = `<span class="ok-text">Session saved. Average focus: ${average}%</span>`;
    } else {
      notice.innerHTML = `<span class="warn-text">Session not saved. No valid focus session was completed.</span>`;
    }

    if (data.analytics) {
      updateAnalytics(data.analytics);
    }

    if (data.brainwave) {
      updateBrainwave(data.brainwave);
    }

    if (cameraReady) {
      setCameraStatus("Webcam still ready. Start tracking anytime.", "ok");
    }

    setGauge(0);
  }
  startBtn?.addEventListener("click", () => {
    startTracking().catch(() => {
      notice.innerHTML = `<span class="warn-text">Unable to start tracking.</span>`;
    });
  });

  stopBtn?.addEventListener("click", () => {
    stopTracking().catch(() => {
      notice.innerHTML = `<span class="warn-text">Unable to stop tracking cleanly.</span>`;
    });
  });

  brainwaveToggle?.addEventListener("click", () => {
    toggleBrainwave().catch(() => {
      notice.innerHTML = `<span class="warn-text">Unable to update brainwave settings.</span>`;
    });
  });

  pomodoroBtn?.addEventListener("click", () => {
    startPomodoroSession().catch(() => {
      notice.innerHTML = `<span class="warn-text">Unable to start Pomodoro.</span>`;
    });
  });

  pomodoroToggleBtn?.addEventListener("click", () => {
    if (pomodoroRunning) {
      pausePomodoroCountdown();
    } else {
      if (pomodoroRemainingSeconds <= 0) {
        pomodoroRemainingSeconds = POMODORO_DURATION_SECONDS;
      }
      startPomodoroCountdown();
    }
  });

  pomodoroResetBtn?.addEventListener("click", () => {
    resetPomodoroCountdown();
  });

  stopwatchToggleBtn?.addEventListener("click", () => {
    if (stopwatchRunning) {
      pauseStopwatch();
    } else {
      startStopwatch();
    }
  });

  stopwatchResetBtn?.addEventListener("click", () => {
    resetStopwatch();
  });

  stopBtn.disabled = true;
  setGauge(0);
  updatePrediction({ predicted_score: 0, drop_expected: false, confidence: 0 });
  updateBrainwave(currentBrainwave);
  syncPomodoroUi();
  syncStopwatchUi();

  initializeCameraOnLoad();

  window.addEventListener("beforeunload", () => {
    stopFrameUploadLoop();
    clearPomodoroInterval();
    clearStopwatchInterval();
    if (cameraStream) {
      cameraStream.getTracks().forEach((track) => track.stop());
      cameraStream = null;
    }
  });
})();























