"""Bridge between a public MQTT broker (where Wokwi boards publish) and the simulated Pi.

    python -m esp32_sim.wokwi_bridge                     settings from the environment
    python -m esp32_sim.wokwi_bridge --prefix tu-solar-32b21d/

Remote (broker.hivemq.com)                      Local (Mosquitto of the simulated Pi)
  <prefix>solar/+/telemetry   ── in, QoS 1 ──►    solar/+/telemetry
  <prefix>solar/+/status      ── in, retain ─►    solar/+/status
  <prefix>solar/cmd           ◄─ out, QoS 1 ──    solar/cmd

Mosquitto's own bridge (mosquitto/bridge/bridge-wokwi.conf.example) does the same job,
but on the test computer broker.hivemq.com closed every bridge connection within a second
while plain clients worked, so this small client is the default way.  It only copies
messages; Node-RED still checks every record exactly as it would from a real board.
"""

from __future__ import annotations

import argparse
import signal
import threading
import time

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .virtual_esp32 import env, log


class Bridge:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.prefix = a.prefix
        self.count_in = 0
        self.count_out = 0
        self.stop_event = threading.Event()
        uid = a.prefix.strip("/").replace("/", "-")
        self.remote = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2,
                                  client_id=f"{uid}-pi-bridge", protocol=mqtt.MQTTv311, clean_session=True)
        self.remote.on_connect = self._remote_connect
        self.remote.on_disconnect = lambda c, u, f, rc, p: log(f"[bridge] remote disconnected ({rc})")
        self.remote.on_message = self._from_remote
        self.remote.reconnect_delay_set(1, 30)
        self.local = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2,
                                 client_id=f"{uid}-local", protocol=mqtt.MQTTv311, clean_session=True)
        if a.user:
            self.local.username_pw_set(a.user, a.password or "")
        self.local.on_connect = self._local_connect
        self.local.on_disconnect = lambda c, u, f, rc, p: log(f"[bridge] local disconnected ({rc})")
        self.local.on_message = self._from_local
        self.local.reconnect_delay_set(1, 30)

    # remote -> local
    def _remote_connect(self, client, userdata, flags, rc, props):
        if rc.is_failure:
            log(f"[bridge] remote refused: {rc}")
            return
        client.subscribe([(f"{self.prefix}solar/+/telemetry", 1), (f"{self.prefix}solar/+/status", 1)])
        log(f"[bridge] remote {self.a.remote}:{self.a.remote_port} connected, prefix '{self.prefix}'")

    def _from_remote(self, client, userdata, msg):
        topic = msg.topic[len(self.prefix):] if msg.topic.startswith(self.prefix) else None
        if not topic or topic == "solar/cmd":
            return
        self.local.publish(topic, msg.payload, qos=1, retain=msg.retain)
        self.count_in += 1

    # local -> remote (commands from Node-RED to the Wokwi boards)
    def _local_connect(self, client, userdata, flags, rc, props):
        if rc.is_failure:
            log(f"[bridge] local refused: {rc}")
            return
        client.subscribe("solar/cmd", 1)
        log(f"[bridge] local {self.a.host}:{self.a.port} connected")

    def _from_local(self, client, userdata, msg):
        self.remote.publish(self.prefix + msg.topic, msg.payload, qos=1)
        self.count_out += 1

    def run(self) -> None:
        self.local.connect_async(self.a.host, self.a.port, keepalive=30)
        self.remote.connect_async(self.a.remote, self.a.remote_port, keepalive=30)
        self.local.loop_start()
        self.remote.loop_start()
        next_log = time.monotonic() + 30
        while not self.stop_event.wait(1.0):
            if time.monotonic() >= next_log:
                log(f"[bridge] in {self.count_in} message(s), out {self.count_out} command(s)")
                next_log = time.monotonic() + 30
        self.remote.disconnect()
        self.local.disconnect()
        self.remote.loop_stop()
        self.local.loop_stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wokwi_bridge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", default=env("WOKWI_PREFIX", "tu-solar-CHANGE-ME/"),
                    help="TOPIC_PREFIX of wokwi/*/config.h [WOKWI_PREFIX]")
    ap.add_argument("--remote", default=env("WOKWI_BROKER", "broker.hivemq.com"))
    ap.add_argument("--remote-port", type=int, default=env("WOKWI_BROKER_PORT", 1883))
    ap.add_argument("--host", default=env("MQTT_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=env("MQTT_PORT", 1883))
    ap.add_argument("--user", default=env("MQTT_USER", ""))
    ap.add_argument("--password", default=env("MQTT_PASSWORD", ""))
    a = ap.parse_args(argv)
    if not a.prefix.endswith("/"):
        a.prefix += "/"
    b = Bridge(a)
    signal.signal(signal.SIGTERM, lambda *_: b.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: b.stop_event.set())
    b.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
