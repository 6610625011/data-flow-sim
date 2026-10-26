"""MQTT side of one virtual ESP32, as designed in sections 2.7.2-2.7.3.

    solar/{node}/telemetry   QoS 1, not retained, one record per second
    solar/{node}/status      "online" (retained) on connect; "offline" as Last Will
    solar/cmd                commands from Node-RED (subscribed, QoS 1)
    sim/cmd                  simulation-only commands (never used by real boards)

Records are queued in a ring buffer.  While Wi-Fi is down the buffer keeps
filling (seq keeps counting); when it overflows the oldest record is lost,
exactly the failure the ring-buffer sizing of eq. (2.7-1) is about.  After
reconnecting, the backlog is sent in order with its original ts.  At most
`max_inflight` QoS 1 messages wait for PUBACK at a time, like the outbox of
ESP-MQTT.  Unacknowledged messages are re-sent by paho after a reconnect, so
duplicates can reach the broker - which is what QoS 1 allows and what the
database deduplicates (section 2.7.5).

A Wi-Fi drop is emulated by silencing the client (no packets, no PINGREQ):
the broker notices after 1.5 x keep-alive and publishes the Last Will.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion


class MqttLink:
    def __init__(self, node: str, host: str, port: int = 1883, user: str | None = None,
                 password: str | None = None, keepalive: int = 10, buffer_size: int = 600,
                 max_inflight: int = 20, inbox: queue.Queue | None = None,
                 listen_sim: bool = True, log=print, client_prefix: str = "esp32"):
        self.node = node
        self.client_id = f"{client_prefix}-{node}"  # two clients with one id keep kicking each other off
        self.host, self.port, self.keepalive = host, port, keepalive
        self.topic_tel = f"solar/{node}/telemetry"
        self.topic_status = f"solar/{node}/status"
        self.buffer: deque[str] = deque()
        self.buffer_size = buffer_size
        self.max_inflight = max_inflight
        self.inbox = inbox if inbox is not None else queue.Queue()
        self.listen_sim = listen_sim
        self.log = log
        self.wifi_up = True
        self.connected = False
        self.inflight: set[int] = set()
        self._lock = threading.Lock()
        # counters (shown in the console status line)
        self.sent = 0
        self.acked = 0
        self.dropped = 0  # lost to ring-buffer overflow
        self.connects = 0

        self._user, self._password = user, password
        self.client = self._new_client(user, password)

    def _new_client(self, user, password) -> mqtt.Client:
        c = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2,
                        client_id=self.client_id, protocol=mqtt.MQTTv311, clean_session=True)
        if user:
            c.username_pw_set(user, password or "")
        c.will_set(self.topic_status, "offline", qos=1, retain=True)
        c.max_inflight_messages_set(self.max_inflight)
        c.reconnect_delay_set(min_delay=1, max_delay=8)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_publish = self._on_publish
        c.on_message = self._on_message
        return c

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=self.keepalive)
        self.client.loop_start()

    def stop(self) -> None:
        try:
            if self.connected:
                # graceful shutdown: say goodbye so the broker does not need the Will
                self.client.publish(self.topic_status, "offline", qos=1, retain=True).wait_for_publish(2)
            self.client.disconnect()
        except Exception:
            pass
        self.client.loop_stop()

    # ------------------------------------------------------------ callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.log(f"[{self.node}] MQTT connect refused: {reason_code}")
            return
        self.connected = True
        self.connects += 1
        client.publish(self.topic_status, "online", qos=1, retain=True)
        client.subscribe([("solar/cmd", 1)] + ([("sim/cmd", 1)] if self.listen_sim else []))
        self.log(f"[{self.node}] MQTT connected to {self.host}:{self.port} (backlog {len(self.buffer)})")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected:
            self.log(f"[{self.node}] MQTT disconnected ({reason_code})")
        self.connected = False

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        with self._lock:
            if mid in self.inflight:
                self.inflight.discard(mid)
                self.acked += 1

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.log(f"[{self.node}] ignored non-JSON message on {msg.topic}")
            return
        self.inbox.put((self.node, msg.topic, payload))

    # ------------------------------------------------------------ data path
    def submit(self, record: dict) -> None:
        """Queue one telemetry record (already carrying ts and seq)."""
        self.buffer.append(json.dumps(record, separators=(",", ":")))
        while len(self.buffer) > self.buffer_size:
            self.buffer.popleft()
            self.dropped += 1

    def resend_last(self, record: dict) -> None:
        """Publish a record a second time (simulates a lost PUBACK -> duplicate)."""
        self.buffer.appendleft(json.dumps(record, separators=(",", ":")))

    @property
    def online(self) -> bool:
        return self.connected and self.wifi_up

    def pump(self) -> int:
        """Publish from the ring buffer while the outbox has room; returns messages sent."""
        n = 0
        while self.online and self.buffer:
            with self._lock:
                if len(self.inflight) >= self.max_inflight:
                    break
            msg = self.buffer.popleft()
            info = self.client.publish(self.topic_tel, msg, qos=1)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                self.buffer.appendleft(msg)
                break
            with self._lock:
                self.inflight.add(info.mid)
            self.sent += 1
            n += 1
        return n

    def pump_for(self, seconds: float) -> None:
        """Keep draining the backlog for up to `seconds` (used while idling between records)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump()
            if not self.buffer or not self.online:
                break
            time.sleep(0.002)

    # ------------------------------------------------------------ faults
    def wifi_drop(self) -> None:
        """Go silent: stop the network loop without DISCONNECT."""
        if not self.wifi_up:
            return
        self.wifi_up = False
        self.client.loop_stop()
        self.log(f"[{self.node}] Wi-Fi DOWN (buffering; broker publishes the Will after "
                 f"{1.5 * self.keepalive:.0f} s)")

    def wifi_restore(self) -> None:
        if self.wifi_up:
            return
        self.wifi_up = True
        self.connected = False
        try:
            self.client.reconnect()
        except Exception as e:  # broker unreachable: the loop keeps retrying
            self.log(f"[{self.node}] reconnect failed ({e}); retrying")
        self.client.loop_start()
        self.log(f"[{self.node}] Wi-Fi UP (backlog {len(self.buffer)} records)")

    def reboot(self) -> None:
        """Power cycle: RAM (ring buffer, outbox) is lost, the TCP session dies without DISCONNECT."""
        self.client.loop_stop()
        try:
            sock = self.client.socket()
            if sock is not None:
                sock.close()
        except Exception:
            pass
        self.buffer.clear()
        with self._lock:
            self.inflight.clear()
        self.connected = False
        self.wifi_up = True
        self.client = self._new_client(self._user, self._password)
        self.start()
        self.log(f"[{self.node}] REBOOT (seq restarts at 0, buffer cleared)")
