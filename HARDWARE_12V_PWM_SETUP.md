# Focus Tracker 12V PWM Hardware Setup (ESP32 + Fan + LED)

This guide links your Focus Tracker website to ESP32 and controls:
- 12V DC fan speed (direct with focus score)
- 12V LED brightness (inverse with focus score)

## 1) Components
- ESP32 dev board (3.3V logic)
- 12V DC fan
- 12V LED strip/light
- 2x IRLZ44N MOSFET (logic-level N-channel)
- 2x 220 ohm resistor (gate series)
- 2x 10k ohm resistor (gate pull-down)
- 1x 1N4007 diode (flyback across fan)
- 12V DC supply
- Breadboard/jumper wires

## 2) Pin Mapping (matches `esp32_focus_controller.ino`)
- `GPIO18` -> fan MOSFET gate (via 220 ohm)
- `GPIO19` -> light MOSFET gate (via 220 ohm)

## 3) Text Wiring Diagram
```text
                 +12V SUPPLY
                    |
                    +---------------------+-------------------+
                    |                     |                   |
                    |                 Fan +               LED +
                    |                     |                   |
                    |                    FAN                 LED
                    |                     |                   |
                    |                 Fan -               LED -
                    |                     |                   |
                    |                 DRAIN Q1            DRAIN Q2
                    |                   |                    |
ESP32 GND ----------+-------------------+--------------------+---- 12V GND
                                       SOURCE               SOURCE

GPIO18 ---220R--- GATE Q1
                 |
                10k
                 |
                GND

GPIO19 ---220R--- GATE Q2
                 |
                10k
                 |
                GND

Flyback diode on fan:
  Cathode -> +12V (fan +)
  Anode   -> fan negative / Q1 drain
```

## 4) Important Safety
- Use only DC loads (no AC mains here).
- ESP32 and 12V supply must share common ground.
- Do not power ESP32 directly from 12V.
  - Use USB or a buck converter (12V -> 5V) for ESP32 power.
- IRLZ44N can get hot at high current; add heatsink if needed.

## 5) Website Linking
Your dashboard JS now sends focus directly to:
- `http://<ESP32_IP>/set?focus=<0-100>`

Set `.env`:
```env
ESP32_BASE_URL=http://192.168.1.55
ESP32_BROWSER_URL=http://192.168.1.55
IOT_ENABLED=1
ESP32_TIMEOUT=0.2
```

`ESP32_BROWSER_URL` is injected into frontend as `window.ESP32_BASE_URL`.

## 6) Quick Test
1. Flash firmware `esp32_focus_controller.ino`.
2. Open serial monitor, copy ESP32 IP.
3. Start Flask app.
4. Open browser dashboard, click Start Tracking.
5. Manual test:
```bash
curl "http://<esp32-ip>/set?focus=75"
curl "http://<esp32-ip>/status"
```

## 7) Mapping Used
- Fan PWM: `focus 0 -> 0`, `focus 100 -> max`
- Light PWM: `focus 0 -> max`, `focus 100 -> 0`
- Smooth transitions:
  - EMA filter on incoming focus
  - PWM ramp steps every 10ms
