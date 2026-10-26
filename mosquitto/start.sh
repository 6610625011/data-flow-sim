#!/bin/sh
# Builds the password file from MQTT_USER / MQTT_PASSWORD on every start, so no
# secret is stored in Git, then runs the broker.  Invoked as "sh /start.sh",
# so it does not need the executable bit (Windows checkouts often lose it).
set -e
PW=/mosquitto/passwd
USER_NAME="${MQTT_USER:-esp32}"
USER_PASS="${MQTT_PASSWORD:-esp32-sim-2026}"

rm -f "$PW"
mosquitto_passwd -c -b "$PW" "$USER_NAME" "$USER_PASS"
if [ -n "$MQTT_USER2" ] && [ -n "$MQTT_PASSWORD2" ]; then   # optional second account (e.g. Wokwi bridge)
  mosquitto_passwd -b "$PW" "$MQTT_USER2" "$MQTT_PASSWORD2"
fi
chown mosquitto:mosquitto "$PW"
chmod 0700 "$PW"
chown -R mosquitto:mosquitto /mosquitto/data

echo "mosquitto: device account '$USER_NAME' ready on port 1883; Node-RED uses internal port 1884"
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
