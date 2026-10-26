"""Virtual ESP32 #1 (fixed) and ESP32 #2 (track) for the full-system simulation.

    python -m esp32_sim                       # real time, today's sun, both boards
    python -m esp32_sim --start 2026-03-21T06:00 --speed 60 --sky clear
    python -m esp32_sim --host 192.168.1.50 --user esp32 --password ...

pvsim computes the physics; this program behaves like the two boards on the
network (topics, payloads, QoS, Last Will, ring buffer) so Mosquitto,
Node-RED, InfluxDB and the dashboard can be tested before the hardware exists.

Real-system topics (Table 2.7-1)            Simulation-only topics
    solar/fixed/telemetry  ESP32 #1             sim/cmd          inject sky, shade, faults,
    solar/track/telemetry  ESP32 #2                              Wi-Fi drops, reboots, speed
    solar/{node}/status    online / offline     sim/truth/{node} ground truth (P_mpp, label...)
    solar/cmd              Node-RED -> boards   sim/state        simulator state (retained)

Every option can also come from an environment variable (see --help), which
is how docker-compose configures it.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from pvsim.environment.solar_position import Site

from .link import MqttLink
from .plant import ALGO, FAULTS, SHADE_PRESETS, World, make_fixed, make_track

SKIES = ("clear", "partly_cloudy", "cloudy", "overcast", "random")


def env(name: str, default):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return type(default)(v) if default is not None and not isinstance(default, bool) else v


_LOG_TZ = timezone(timedelta(hours=float(os.environ.get("SIM_TZ", "7") or 7)))
_log_lock = threading.Lock()


def log(*a):
    """One line per call (threads do not interleave), stamped with the site's local time."""
    line = datetime.now(_LOG_TZ).strftime("%H:%M:%S") + " " + " ".join(str(x) for x in a) + "\n"
    with _log_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


# ------------------------------------------------------------- sim control
class SimControl:
    """The simulator's own MQTT client: listens on sim/cmd, publishes ground truth."""

    def __init__(self, host, port, user, password, inbox: queue.Queue):
        c = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2,
                        client_id="pvsim-control", protocol=mqtt.MQTTv311)
        if user:
            c.username_pw_set(user, password or "")
        c.on_connect = lambda cl, u, f, rc, p: (cl.subscribe("sim/cmd", 1), log("[sim] control connected"))
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 8)
        self.c, self.inbox = c, inbox
        c.connect_async(host, port, keepalive=30)
        c.loop_start()

    def _on_message(self, client, userdata, msg):
        try:
            self.inbox.put(("sim", msg.topic, json.loads(msg.payload.decode("utf-8"))))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log("[sim] ignored non-JSON sim/cmd")

    def truth(self, node: str, rec: dict) -> None:
        if self.c.is_connected():
            self.c.publish(f"sim/truth/{node}", json.dumps(rec, separators=(",", ":")), qos=0)

    def state(self, rec: dict) -> None:
        if self.c.is_connected():
            self.c.publish("sim/state", json.dumps(rec, separators=(",", ":")), qos=0, retain=True)

    def stop(self) -> None:
        self.c.disconnect()
        self.c.loop_stop()


# -------------------------------------------------------------------- main
def parse_start(text: str, tz_hours: float) -> float:
    """'now' or local ISO time 'YYYY-MM-DDTHH:MM[:SS]' -> UTC epoch seconds."""
    if text in ("", "now"):
        return float(int(time.time()))
    loc = datetime.fromisoformat(text)
    if loc.tzinfo is None:
        loc = loc.replace(tzinfo=timezone(timedelta(hours=tz_hours)))
    return loc.timestamp()


def order_fields(node: str, ts: int, seq: int, tel: dict, mppt: str) -> dict:
    """Field order of the JSON examples in section 2.7.2."""
    rec = {"ts": ts, "seq": seq}
    for k in ("v_pv", "i_pv", "p_pv", "v_out", "i_out", "p_out", "duty", "t_panel"):
        rec[k] = tel[k]
    if node == "fixed":
        rec["g"], rec["t_amb"] = tel["g"], tel["t_amb"]
        rec["mppt"] = mppt
    else:
        rec["mppt"] = mppt
        for k in ("az", "el", "e_az", "e_el", "m_az", "m_el", "p_mot", "mode"):
            rec[k] = tel[k]
    return rec


class Simulator:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.site = Site(a.lat, a.lon, a.tz)
        self.world = World(self.site, a.sky, a.seed)
        self.plants = {}
        if "fixed" in a.nodes:
            self.plants["fixed"] = make_fixed(a.mppt, a.mppt_dt, seed=a.seed * 10 + 1)
        if "track" in a.nodes:
            self.plants["track"] = make_track(a.mppt, a.mppt_dt, seed=a.seed * 10 + 2)
        if not self.plants:
            raise SystemExit("no nodes selected (use --nodes fixed,track)")
        self.inbox: queue.Queue = queue.Queue()
        self.links = {n: MqttLink(n, a.host, a.port, a.user, a.password, keepalive=a.keepalive,
                                  buffer_size=a.buffer, inbox=self.inbox, listen_sim=False, log=log)
                      for n in self.plants}
        self.sim = SimControl(a.host, a.port, a.user, a.password, self.inbox)
        self.seq = {n: 0 for n in self.plants}
        self.last_rec: dict[str, list] = {n: [] for n in self.plants}
        self.wifi_until: dict[str, float] = {}
        self.speed = a.speed
        self.t = parse_start(a.start, a.tz)
        self.realtime = a.start in ("", "now") and a.speed == 1.0
        self._anchor()
        self.running = True
        _, s0 = self.world.sample(self.t)
        for p in self.plants.values():
            if hasattr(p.mount, "align"):
                p.mount.align(s0.sun)

    def _anchor(self) -> None:
        self.wall0, self.sim0 = time.time(), self.t

    # ------------------------------------------------------- commands
    def _targets(self, which) -> list[str]:
        if which in (None, "", "all", "both"):
            return list(self.plants)
        return [n for n in str(which).split(",") if n in self.plants]

    def handle(self, src: str, topic: str, p: dict) -> None:
        if not isinstance(p, dict):
            return
        try:
            if topic == "solar/cmd":
                self._solar_cmd(src, p)
            elif topic == "sim/cmd":
                self._sim_cmd(p)
        except (ValueError, KeyError, TypeError) as e:
            log(f"[cmd] rejected {topic} {p}: {e}")

    def _solar_cmd(self, node: str, p: dict) -> None:
        """One board received solar/cmd; act only if it is addressed."""
        if node not in self._targets(p.get("target", "all")):
            return
        plant = self.plants[node]
        if "mppt" in p:
            if p["mppt"] not in ALGO:
                raise ValueError(f"mppt must be one of {list(ALGO)}")
            plant.set_mppt(p["mppt"])
            log(f"[{node}] MPPT -> {p['mppt']}")
        run = p.get("run", p.get("action"))
        if run in ("start", "stop"):
            plant.set_running(run == "start")
            log(f"[{node}] run -> {run}")
        if "tracker" in p and hasattr(plant.mount, "command"):
            if p["tracker"] not in ("auto", "home", "park"):
                raise ValueError("tracker must be auto, home or park")
            plant.mount.command = p["tracker"]
            log(f"[{node}] tracker -> {p['tracker']}")

    def _sim_cmd(self, p: dict) -> None:
        nodes = self._targets(p.get("node", "all"))
        if "sky" in p:
            if p["sky"] not in SKIES:
                raise ValueError(f"sky must be one of {SKIES}")
            self.world.set_sky(p["sky"])
            log(f"[sim] sky -> {p['sky']}")
        if "shade" in p:
            for n in nodes:
                s = p["shade"]
                if isinstance(s, list):
                    self.plants[n].set_shade(p.get("name", "custom"), depth=s)
                else:
                    self.plants[n].set_shade(s)
            log(f"[sim] shade {p['shade']} on {nodes}")
        if "fault" in p:
            for n in nodes:
                self.plants[n].set_fault(p["fault"])
            log(f"[sim] fault {p['fault']} on {nodes}")
        if "wifi_drop" in p:
            secs = float(p["wifi_drop"])
            for n in nodes:
                self.links[n].wifi_drop()
                self.wifi_until[n] = time.monotonic() + secs
        if "reboot" in p:
            for n in self._targets(p["reboot"]):
                self.plants[n].reboot()
                self.links[n].reboot()
                self.seq[n] = 0
        if "dup" in p:
            k = int(p["dup"])
            for n in nodes:
                for rec in self.last_rec[n][-k:]:
                    self.links[n].resend_last(rec)
            log(f"[sim] re-sending last {k} record(s) of {nodes} (duplicates)")
        if "speed" in p:
            v = float(p["speed"])
            if not 0.1 <= v <= 600:
                raise ValueError("speed must be 0.1..600")
            self.speed = v
            self.realtime = False
            self._anchor()
            log(f"[sim] speed -> {v}x")

    # ------------------------------------------------------- stepping
    def simulate_window(self) -> None:
        a = self.a
        n_steps = max(1, round(a.pub / a.mppt_dt))
        env_every = max(1, round(1.0 / a.mppt_dt))  # environment refresh: 1 s, like pvsim
        for k in range(n_steps):
            t = self.t + k * a.mppt_dt
            if k % env_every == 0:
                env, s = self.world.sample(t)
                for p in self.plants.values():
                    p.apply_env(env, s, 1.0)
            for p in self.plants.values():
                p.mppt_step()
        self.t += a.pub

    def emit(self) -> None:
        ts = int(round(self.t * 1000))
        for n, plant in self.plants.items():
            tel, truth = plant.close_window(self.a.pub)
            rec = order_fields(n, ts, self.seq[n], tel, plant.mppt)
            self.seq[n] += 1
            self.links[n].submit(rec)
            self.last_rec[n] = (self.last_rec[n] + [rec])[-20:]
            if self.a.truth:
                self.sim.truth(n, {"ts": ts, **truth})

    def _backlog_high(self) -> bool:
        """An online board's outbox is falling behind (not a simulated Wi-Fi drop)."""
        return any(l.online and len(l.buffer) > max(20, l.buffer_size // 4) for l in self.links.values())

    def wait_until_due(self) -> None:
        """Pace the loop (real time or speed x) while draining backlogs."""
        if self.realtime:
            due = self.t  # publish when the wall clock reaches the window end
        else:
            # Accelerated or replayed time: when this computer cannot publish as fast as the
            # chosen speed, pause the simulated clock instead of letting the ring buffer
            # overflow.  Such a loss would come from the host's CPU, not from the modelled
            # network; simulated Wi-Fi drops (link offline) still fill and overflow the buffer.
            if self._backlog_high():
                stalled = time.time()
                while self.running and self._backlog_high():
                    for l in self.links.values():
                        l.pump()
                    time.sleep(0.005)
                self.wall0 += time.time() - stalled
            due = self.wall0 + (self.t - self.sim0) / self.speed
            late = time.time() - due
            if late > 2.0:  # fell behind (slow host): slip the clock rather than burst to catch up
                self.wall0 += late
                due += late
        while True:
            now = time.time()
            if now >= due:
                break
            for l in self.links.values():
                l.pump()
            time.sleep(min(due - now, 0.01))

    def status_line(self) -> str:
        loc = self.world.local(self.t)
        parts = [f"sim {loc:%Y-%m-%d %H:%M:%S} x{self.speed:g} sky={self.world.sky}"]
        for n, p in self.plants.items():
            l = self.links[n]
            last = self.last_rec[n][-1] if self.last_rec[n] else {}
            st = "up" if l.online else ("WIFI-DOWN" if not l.wifi_up else "connecting")
            parts.append(f"{n}: P={last.get('p_out', 0):5.1f}W {st} buf={len(l.buffer)} "
                         f"acked={l.acked} lost={l.dropped}")
        return " | ".join(parts)

    def run(self) -> None:
        for l in self.links.values():
            l.start()
        log(f"virtual ESP32s {list(self.plants)} -> mqtt://{self.a.host}:{self.a.port} "
            f"start={self.world.local(self.t):%Y-%m-%d %H:%M} speed={self.speed}x sky={self.world.sky}")
        next_status = time.monotonic()
        t_end = self.t + self.a.duration if self.a.duration > 0 else None
        while self.running:
            while True:  # commands from Node-RED (solar/cmd) and the sim page (sim/cmd)
                try:
                    self.handle(*self.inbox.get_nowait())
                except queue.Empty:
                    break
            now_m = time.monotonic()
            for n, until in list(self.wifi_until.items()):
                if now_m >= until:
                    self.links[n].wifi_restore()
                    del self.wifi_until[n]
            self.simulate_window()
            self.wait_until_due()
            self.emit()
            for l in self.links.values():
                l.pump()
            if time.monotonic() >= next_status:
                if not self.a.quiet:
                    log(self.status_line())
                self.sim.state({"t": int(self.t * 1000), "speed": self.speed, "sky": self.world.sky,
                                "realtime": self.realtime,
                                "nodes": {n: {"shade": p.shade_name, "fault": p.fault, "mppt": p.mppt,
                                              "running": p.running,
                                              "wifi": self.links[n].wifi_up,
                                              "buffer": len(self.links[n].buffer)}
                                          for n, p in self.plants.items()}})
                next_status = time.monotonic() + self.a.status_every
            if t_end is not None and self.t >= t_end:
                break
        self.shutdown()

    def shutdown(self) -> None:
        # give the backlog a moment to drain
        end = time.monotonic() + 5
        while time.monotonic() < end and any(l.buffer or l.inflight for l in self.links.values()):
            for l in self.links.values():
                l.pump()
            time.sleep(0.02)
        for l in self.links.values():
            l.stop()
        self.sim.stop()
        log("stopped: " + self.status_line())


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="virtual_esp32", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=env("MQTT_HOST", "localhost"), help="broker [MQTT_HOST]")
    ap.add_argument("--port", type=int, default=env("MQTT_PORT", 1883), help="[MQTT_PORT]")
    ap.add_argument("--user", default=env("MQTT_USER", ""), help="[MQTT_USER]")
    ap.add_argument("--password", default=env("MQTT_PASSWORD", ""), help="[MQTT_PASSWORD]")
    ap.add_argument("--nodes", default=env("SIM_NODES", "fixed,track"), help="[SIM_NODES]")
    ap.add_argument("--start", default=env("SIM_START", "now"),
                    help="'now' or local time YYYY-MM-DDTHH:MM [SIM_START]")
    ap.add_argument("--speed", type=float, default=env("SIM_SPEED", 1.0),
                    help="simulated seconds per real second [SIM_SPEED]")
    ap.add_argument("--duration", type=float, default=env("SIM_DURATION", 0.0),
                    help="stop after this many simulated seconds, 0 = run forever [SIM_DURATION]")
    ap.add_argument("--sky", default=env("SIM_SKY", "partly_cloudy"), choices=SKIES, help="[SIM_SKY]")
    ap.add_argument("--seed", type=int, default=env("SIM_SEED", 1), help="[SIM_SEED]")
    ap.add_argument("--mppt", default=env("SIM_MPPT", "po"), choices=list(ALGO), help="[SIM_MPPT]")
    ap.add_argument("--mppt-dt", type=float, default=env("SIM_MPPT_DT", 0.1),
                    help="MPPT period [s] [SIM_MPPT_DT]")
    ap.add_argument("--pub", type=float, default=env("SIM_PUB", 1.0), help="publish period T_pub [s]")
    ap.add_argument("--keepalive", type=int, default=env("SIM_KEEPALIVE", 10), help="MQTT keep-alive [s]")
    ap.add_argument("--buffer", type=int, default=env("SIM_BUFFER", 600),
                    help="ring buffer records, 600 = 10 min at 1 s (eq. 2.7-1) [SIM_BUFFER]")
    ap.add_argument("--lat", type=float, default=env("SIM_LAT", Site.latitude))
    ap.add_argument("--lon", type=float, default=env("SIM_LON", Site.longitude))
    ap.add_argument("--tz", type=float, default=env("SIM_TZ", Site.tz_hours))
    ap.add_argument("--no-truth", dest="truth", action="store_false",
                    default=env("SIM_TRUTH", "1") not in ("0", "false", "no"),
                    help="do not publish sim/truth/{node}")
    ap.add_argument("--status-every", type=float, default=10.0, help="console status period [s]")
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    a.nodes = [n.strip() for n in a.nodes.split(",") if n.strip()]
    if a.mppt_dt <= 0 or a.pub <= 0 or a.pub < a.mppt_dt:
        print("need 0 < mppt-dt <= pub", file=sys.stderr)
        return 2
    sim = Simulator(a)

    def stop(*_):
        sim.running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    sim.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
