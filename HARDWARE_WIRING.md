# NeuroFocus Hardware Wiring (ESP32 + DHT22 + PIR + Relay)

This setup extends your current Focus Tracker IoT module without changing frontend/dashboard code.

## Components
- ESP32 DevKit (3.3V logic)
- Common-cathode RGB LED
- 3x `220 ohm` resistors (RGB channels)
- Fan driver or PWM fan module
- `DHT22` temperature/humidity sensor
- `HC-SR501` PIR motion sensor
- 1-channel relay module (prefer opto-isolated, 3.3V-compatible input)
- Breadboard + jumper wires
- External `5V` supply (for relay/fan if needed)

## ESP32 Pin Mapping (matches `esp32_focus_controller.ino`)
- `GPIO25` -> RGB Red (through `220 ohm`)
- `GPIO26` -> RGB Green (through `220 ohm`)
- `GPIO27` -> RGB Blue (through `220 ohm`)
- RGB common cathode -> `GND`

- `GPIO14` -> Fan PWM input (driver/module control pin)
- `GPIO4` -> DHT22 data pin
- `GPIO33` -> PIR OUT
- `GPIO23` -> Relay IN

## Breadboard Wiring
1. ESP32 `3V3` -> breadboard `+` rail (logic sensors)
2. ESP32 `GND` -> breadboard `-` rail
3. DHT22:
   - `VCC` -> `3V3`
   - `GND` -> `GND`
   - `DATA` -> `GPIO4`
   - Add `10k` pull-up between `DATA` and `3V3` (required)
4. PIR (HC-SR501):
   - `VCC` -> `5V` (or `3V3` if your module supports it)
   - `GND` -> `GND`
   - `OUT` -> `GPIO33`
5. Relay module:
   - `VCC` -> `5V`
   - `GND` -> `GND`
   - `IN` -> `GPIO23`
6. Fan:
   - PWM/control pin -> `GPIO14` (through proper fan driver/module)
   - Fan power from external supply
   - Common ground: external supply GND <-> ESP32 GND

## Relay Load Wiring (Light/Fan Power Switching)
Use relay contacts for load power line:
- Supply line -> `COM`
- Load line -> `NO` (normally off) or `NC` (normally on)
- Neutral/return line goes directly to load

Important:
- If switching AC mains, use certified modules, insulation, fuse/MCB, and electrician supervision.
- Do not route high-voltage AC through a breadboard.

## Firmware Notes
- `RELAY_ACTIVE_LOW` is set to `true` in sketch (common relay behavior).
- If your relay logic is inverted, set `RELAY_ACTIVE_LOW = false`.

## Flask Integration (No UI changes required)
Set `.env`:
```env
IOT_ENABLED=1
ESP32_BASE_URL=http://<esp32-ip>
ESP32_TIMEOUT=0.2
```

Existing route (extended):
- `POST /iot/update` now accepts:
  - `focus` (0-100)
  - `light_color` (`green|yellow|red|blue|off`)
  - `fan_speed` (`off|low|medium|normal|high`)
  - `relay_state` (`on|off`, also accepts boolean)

New routes:
- `POST /iot/relay`
- `GET /iot/sensors`

## Quick API Tests
Turn relay ON:
```bash
curl -X POST http://127.0.0.1:5000/iot/relay ^
  -H "Content-Type: application/json" ^
  -d "{\"relay_state\":\"on\"}"
```

Push focus + environment:
```bash
curl -X POST http://127.0.0.1:5000/iot/update ^
  -H "Content-Type: application/json" ^
  -d "{\"focus\":72,\"relay_state\":\"on\"}"
```

Read sensors:
```bash
curl http://127.0.0.1:5000/iot/sensors
```

Expected sensor JSON fields:
- `temp_c`
- `humidity`
- `pir_motion`
- `relay_state`
- `focus`, `light_color`, `fan_speed`
