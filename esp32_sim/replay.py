"""Replay a pvsim dataset through the real IoT pipeline, as if the two boards sent it.

    python -m esp32_sim.replay /datasets/batch_30d                 every day_*.csv, in order
    python -m esp32_sim.replay day_003.csv --speed 3600 --loop     one file, forever

Input is the CSV written by `pvsim batch` or `pvsim run` (long format, one row per
panel per time step; see pvsim/data/schema.py).  Rows of panel "fixed" become
solar/fixed/telemetry messages and rows of panel "tracker" become solar/track/telemetry
messages with the JSON fields of Table 2.7-1, QoS 1, Last Will, seq and ring buffer
(the same MqttLink as the live simulator).  ts is the row's own timestamp, so the
dataset lands in InfluxDB at its original dates.

The row's fault_label rides along in an extra field "label".  Real boards never send
it (the experimenter sets the label in Node-RED, section 2.7.4); for a replay it lets
Node-RED tag every point with the dataset's ground truth, panel by panel.

Fields the dataset does not contain are derived, and marked ASSUMPTION:
  e_az, e_el   four-quadrant LDR signal from the sun position (pvsim) and the panel angles
  m_az, m_el   sign of the angle change since the previous row
  p_mot        motor power from the angle change at the tracker's slew speed
  mode         stuck (tracker_stuck label) / park (sun below 3 deg) / track
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from pvsim.environment.solar_position import Site, sun_position
from pvsim.tracking.mounts import TrackerParams

from .link import MqttLink
from .plant import ldr_errors
from .virtual_esp32 import env, log, order_fields

NODE_OF_PANEL = {"fixed": "fixed", "tracker": "track", "track": "track"}
MPPT_NAME = {"po": "po", "inccond": "inc", "inc": "inc"}
TRK = TrackerParams()  # slew speed and motor power of pvsim's tracker (ASSUMPTION there)


def dataset_files(path: Path) -> list[Path]:
    if not path.exists():
        # Git Bash on Windows rewrites "/datasets/..." into "C:/Program Files/Git/datasets/..."
        # before docker sees it; map such a path back to the folder mounted in the container.
        tail = str(path).replace("\\", "/").split("/Git/", 1)
        if len(tail) == 2 and Path("/" + tail[1]).exists():
            path = Path("/" + tail[1])
        else:
            raise SystemExit(f"dataset not found: {path} (inside the container the folder is /datasets/...)")
    if path.is_dir():
        files = sorted(path.glob("day_*.csv")) or sorted(path.glob("*.csv"))
    else:
        files = [path]
    if not files:
        raise SystemExit(f"no CSV files in {path}")
    return files


def load_day(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    need = {"timestamp", "panel_id", "v_pv", "i_pv", "p_pv", "v_out", "i_out", "p_out", "duty", "t_panel"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"{path.name}: not a pvsim CSV (missing {sorted(missing)})")
    t = pd.to_datetime(df["timestamp"], utc=True)  # unit-safe (pandas may use ns, us or ms)
    df["ts"] = ((t - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).astype("int64")
    return df.sort_values(["ts", "panel_id"], kind="stable", ignore_index=True)


class Replayer:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.site = Site(a.lat, a.lon, a.tz)
        self.links = {n: MqttLink(n, a.host, a.port, a.user, a.password, keepalive=a.keepalive,
                                  buffer_size=a.buffer, listen_sim=False, log=log,
                                  client_prefix="replay")
                      for n in ("fixed", "track")}
        self.seq = {"fixed": 0, "track": 0}
        self.prev: dict[str, dict] = {}
        self.rng = np.random.default_rng(a.seed)
        self.truth = None
        if a.truth:
            c = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id="pvsim-replay-truth",
                            protocol=mqtt.MQTTv311)
            if a.user:
                c.username_pw_set(a.user, a.password or "")
            c.connect_async(a.host, a.port, keepalive=30)
            c.loop_start()
            self.truth = c
        self.running = True
        self.sent_rows = 0
        self.days_done: set = set()

    # ------------------------------------------------------------ records
    def record(self, node: str, row, sun) -> tuple[dict, dict]:
        ts = int(row.ts)
        tel = {"v_pv": round(float(row.v_pv), 2), "i_pv": round(float(row.i_pv), 3),
               "p_pv": round(float(row.p_pv), 2), "v_out": round(float(row.v_out), 2),
               "i_out": round(float(row.i_out), 3), "p_out": round(float(row.p_out), 2),
               "duty": round(float(row.duty), 3), "t_panel": round(float(row.t_panel), 1)}
        label = str(getattr(row, "fault_label", "normal") or "normal")
        g_poa = float(getattr(row, "irradiance_poa", 0.0) or 0.0)
        if node == "fixed":
            tel["g"] = round(max(g_poa, 0.0), 1)
            tel["t_amb"] = round(float(getattr(row, "t_ambient", 30.0)), 1)
        else:
            tilt = float(getattr(row, "tilt_deg", 0.0))
            az = float(getattr(row, "azimuth_deg", 180.0)) % 360.0
            beam = min(1.0, max(0.0, g_poa / 1000.0))
            e_az, e_el = ldr_errors(sun, tilt, az, beam, self.rng)
            p = self.prev.get(node)
            m_az = m_el = 0
            p_mot = 0.0
            if p is not None and ts > p["ts"]:
                dt = (ts - p["ts"]) / 1000.0
                d_az = (az - p["az"] + 180.0) % 360.0 - 180.0
                d_el = (90.0 - tilt) - (90.0 - p["tilt"])
                m_az = int(math.copysign(1, d_az)) if abs(d_az) > 0.05 else 0
                m_el = int(math.copysign(1, d_el)) if abs(d_el) > 0.05 else 0
                move_s = max(abs(d_az), abs(d_el)) / TRK.speed
                p_mot = TRK.p_motor * min(move_s, dt) / dt
            self.prev[node] = {"ts": ts, "az": az, "tilt": tilt}
            mode = "stuck" if label == "tracker_stuck" else ("park" if sun.elevation < TRK.min_elevation else "track")
            tel.update({"az": round(az, 1), "el": round(90.0 - tilt, 1), "e_az": round(e_az, 3),
                        "e_el": round(e_el, 3), "m_az": m_az, "m_el": m_el, "p_mot": round(p_mot, 2),
                        "mode": mode})
        mppt = MPPT_NAME.get(str(getattr(row, "algorithm", "po")), "po")
        rec = order_fields(node, ts, self.seq[node], tel, mppt)
        rec["label"] = label  # replay only: ground-truth label of the dataset row
        truth = {"ts": ts, "label": label, "g_poa": round(g_poa, 1),
                 "sun_el": round(sun.elevation, 2), "sun_az": round(sun.azimuth, 2), "running": 1}
        if hasattr(row, "p_mpp_true"):
            truth["p_mpp"] = round(float(row.p_mpp_true), 3)
            truth["p_pv_true"] = round(float(row.p_pv_true), 3)
        return rec, truth

    # ------------------------------------------------------------ pacing
    def _backlog_high(self) -> bool:
        return any(l.online and len(l.buffer) > max(20, l.buffer_size // 4) for l in self.links.values())

    def pace(self, seconds: float) -> None:
        """Wait `seconds` of real time while publishing; never let the outbox overflow."""
        end = time.monotonic() + seconds
        while self.running:
            for l in self.links.values():
                l.pump()
            if time.monotonic() >= end and not self._backlog_high():
                break
            time.sleep(0.002)

    # ------------------------------------------------------------ main loop
    def play_file(self, path: Path) -> None:
        df = load_day(path)
        a = self.a
        day = pd.to_datetime(df["timestamp"].iloc[0]).date()
        if day in self.days_done and not a.loop:
            # pvsim batch draws random dates and may draw one twice; a second copy would land
            # on the same timestamps in InfluxDB and mix two different days
            log(f"[replay] skip {path.name}: date {day} was already replayed")
            return
        self.days_done.add(day)
        prev_ts = None
        n0 = self.sent_rows
        e = {"fixed": 0.0, "track": 0.0}
        for ts, grp in df.groupby("ts", sort=True):
            if not self.running:
                return
            if prev_ts is not None:
                gap = (ts - prev_ts) / 1000.0
                self.pace(min(gap / a.speed, a.max_wait))
            when = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).replace(tzinfo=None)
            sun = sun_position(when, self.site)
            for row in grp.itertuples(index=False):
                node = NODE_OF_PANEL.get(str(row.panel_id))
                if node is None:
                    continue
                rec, truth = self.record(node, row, sun)
                self.seq[node] += 1
                self.links[node].submit(rec)
                if self.truth is not None and self.truth.is_connected():
                    self.truth.publish(f"sim/truth/{node}", json.dumps(truth, separators=(",", ":")), qos=0)
                if prev_ts is not None:
                    e[node] += rec["p_out"] * (ts - prev_ts) / 3.6e6
                self.sent_rows += 1
            prev_ts = ts
            for l in self.links.values():
                l.pump()
        labels = df.loc[df.fault_label != "normal", "fault_label"].unique().tolist() \
            if "fault_label" in df.columns else []
        log(f"[replay] {path.name} {day}: {self.sent_rows - n0} rows, E_out fixed {e['fixed']:.1f} Wh, "
            f"track {e['track']:.1f} Wh, labels {labels or ['normal']}")

    def run(self, files: list[Path]) -> None:
        for l in self.links.values():
            l.start()
        t0 = time.monotonic()
        while self.running and not all(l.online for l in self.links.values()):
            if time.monotonic() - t0 > 20:
                raise SystemExit(f"cannot reach the broker at {self.a.host}:{self.a.port}")
            time.sleep(0.1)
        log(f"[replay] {len(files)} file(s) -> mqtt://{self.a.host}:{self.a.port} speed {self.a.speed:g}x")
        while self.running:
            for f in files:
                if not self.running:
                    break
                self.play_file(f)
            if not self.a.loop:
                break
        self.pace(1.0)
        end = time.monotonic() + 10
        while time.monotonic() < end and any(l.buffer or l.inflight for l in self.links.values()):
            self.pace(0.05)
        for l in self.links.values():
            l.stop()
        if self.truth is not None:
            self.truth.disconnect()
            self.truth.loop_stop()
        log(f"[replay] done: {self.sent_rows} rows, acked fixed {self.links['fixed'].acked} "
            f"track {self.links['track'].acked}, lost {sum(l.dropped for l in self.links.values())}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="replay", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", help="a pvsim CSV, or a folder of day_*.csv from `pvsim batch`")
    ap.add_argument("--host", default=env("MQTT_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=env("MQTT_PORT", 1883))
    ap.add_argument("--user", default=env("MQTT_USER", ""))
    ap.add_argument("--password", default=env("MQTT_PASSWORD", ""))
    ap.add_argument("--speed", type=float, default=env("REPLAY_SPEED", 3600.0),
                    help="dataset seconds per real second (3600: a 1-min row every 1/60 s)")
    ap.add_argument("--max-wait", type=float, default=1.0,
                    help="longest real pause between rows [s]; skips nights and jumps between days")
    ap.add_argument("--loop", action="store_true", help="start again after the last file")
    ap.add_argument("--buffer", type=int, default=600)
    ap.add_argument("--keepalive", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1, help="seed of the derived LDR noise")
    ap.add_argument("--no-truth", dest="truth", action="store_false", help="do not publish sim/truth/*")
    ap.add_argument("--lat", type=float, default=env("SIM_LAT", Site.latitude))
    ap.add_argument("--lon", type=float, default=env("SIM_LON", Site.longitude))
    ap.add_argument("--tz", type=float, default=env("SIM_TZ", Site.tz_hours))
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.speed <= 0:
        print("speed must be > 0", file=sys.stderr)
        return 2
    rp = Replayer(a)

    def stop(*_):
        rp.running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    rp.run(dataset_files(Path(a.dataset)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
