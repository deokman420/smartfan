"""Per-device credentials. Copy to secrets.py and fill in.

secrets.py is gitignored. This file is the template and carries no real
values.

MicroPython has no dotenv, so this is the .env equivalent: a gitignored
credentials module, imported rather than parsed. Tuning parameters live in
config.py and ARE versioned -- only secrets belong here.

If secrets.py is absent the controller runs perfectly well offline. The
network layer is additive; nothing about fan behaviour depends on it.
"""

# --- WiFi -------------------------------------------------------------------
WIFI_SSID = "your-ssid"
WIFI_PASS = "your-password"
WIFI_COUNTRY = "US"          # regulatory domain; wrong value limits channels

# --- MQTT -------------------------------------------------------------------
# Mosquitto add-on on the Home Assistant host. Use a dedicated broker account
# per device, not the Home Assistant login -- so one compromised or misbehaving
# device can be revoked without touching the others.
MQTT_HOST = "192.168.0.5"
MQTT_PORT = 1883
MQTT_USER = "pico-fan-bathroom"
MQTT_PASS = "your-mqtt-password"

# Client ID must be unique across the broker. Two clients sharing an ID will
# disconnect each other in a loop that looks exactly like a flaky network.
MQTT_CLIENT_ID = "pico-fan-bathroom"
