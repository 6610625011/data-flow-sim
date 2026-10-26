"""The physical side of the two test rigs, built from pvsim.

pvsim (the simulator in the parent folder) already models the sun, sky,
air temperature, the 50 W module with bypass diodes, the buck converter, the
electronic load, the INA226 and the MPPT algorithms.  This module only glues
those pieces into two rigs that run continuously (across midnight, for days)
instead of the fixed sunrise-to-sunset window of pvsim's DayRunner:

    World        one site, one sky; rebuilds pvsim's DayEnvironment each local day
    PanelPlant   LiveChannel + mount + output INA226 + lab shading/faults,
                 averaged into one telemetry record per publish window
    TrackerSim   pvsim's TwoAxisTracker plus the commanded modes of solar/cmd
                 (auto / home / park) and a four-quadrant LDR error model

Nothing here touches pvsim's own files.  ASSUMPTION tags follow pvsim's README
convention: values that are not from a datasheet or paper.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import numpy as np

from pvsim.converter import make_converter
from pvsim.data.faults import FAULT_KINDS
from pvsim.engine.live import Conditions, LiveChannel
from pvsim.environment.day import DayEnvironment
from pvsim.environment.solar_position import Site, SunPosition
from pvsim.environment.weather import WeatherConfig
from pvsim.load.electronic_load import ElectronicLoad
from pvsim.mppt import create_mppt
from pvsim.pv.datasheet import PanelDatasheet
from pvsim.sensors.ina226 import INA226, INA226Config
from pvsim.tracking.mounts import FixedMount, TrackerParams, TwoAxisTracker

# MQTT names (Table 2.7-1 uses "po" / "inc") -> pvsim algorithm names
ALGO = {"po": "po", "inc": "inccond"}

# Lab shading presets: a shade cloth laid on the module blocks this fraction of
# the light on each of the 3 substrings (12 cells each).  The names double as
# experiment labels (section 2.7.4 example: normal, shade_25).
# ASSUMPTION: levels chosen for the fault-detection dataset, not measured.
SHADE_PRESETS: dict[str, tuple[float, float, float]] = {
    "normal": (0.0, 0.0, 0.0),
    "shade_25": (0.0, 0.0, 0.25),
    "shade_50": (0.0, 0.0, 0.50),
    "shade_75": (0.0, 0.0, 0.75),
    "shade_2sub_50": (0.0, 0.50, 0.50),
}

# Faults that can be switched on from the simulation page (pvsim FAULT_KINDS magnitudes)
FAULTS = ("soiling", "bypass_diode_short", "sensor_offset", "open_circuit", "tracker_stuck")

LDR_SATURATION_DEG = 10.0  # ASSUMPTION: four-quadrant sensor output saturates at +-10 deg
LDR_NOISE = 0.003  # ASSUMPTION: rms noise of the normalised difference signal
REF_CELL_NOISE = 0.005  # ASSUMPTION: 0.5 % rms reference-cell noise (+ 1 W/m2)
TEMP_NOISE = 0.15  # ASSUMPTION: DS18B20 / BME280 reading noise [degC rms]


def _combine(a: float, b: float) -> float:
    """Two independent blocked fractions (same rule as pvsim.data.faults)."""
    return 1.0 - (1.0 - a) * (1.0 - b)


def _wrap180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


# --------------------------------------------------------------------- world
class World:
    """Sun, sky and air temperature for one site, valid across days."""

    def __init__(self, site: Site, sky: str = "partly_cloudy", seed: int = 1,
                 t_min: float | None = None, t_max: float | None = None):
        self.site = site
        self.sky = sky
        self.seed = seed
        self.t_min, self.t_max = t_min, t_max
        self._env: DayEnvironment | None = None
        self._day: date | None = None

    def set_sky(self, sky: str) -> None:
        self.sky = sky
        self._day = None  # rebuild today's weather on the next sample

    def local(self, t_utc: float) -> datetime:
        """Naive local wall time of the site for a UTC epoch."""
        return (datetime.fromtimestamp(t_utc, tz=timezone.utc)
                + timedelta(hours=self.site.tz_hours)).replace(tzinfo=None)

    def env_for(self, day: date) -> DayEnvironment:
        if self._env is None or self._day != day:
            kw = {}
            if self.t_min is not None:
                kw["t_min"] = self.t_min
            if self.t_max is not None:
                kw["t_max"] = self.t_max
            # a different but reproducible cloud pattern every day
            wx = WeatherConfig(sky=self.sky, seed=self.seed * 100_003 + day.toordinal(), **kw)
            self._env = DayEnvironment(day, wx, [], self.site)
            self._day = day
        return self._env

    def sample(self, t_utc: float):
        loc = self.local(t_utc)
        env = self.env_for(loc.date())
        t_s = loc.hour * 3600 + loc.minute * 60 + loc.second + loc.microsecond * 1e-6
        return env, env.sample(t_s)


# ------------------------------------------------------------------- tracker
class TrackerSim(TwoAxisTracker):
    """pvsim's 2-axis tracker plus the commanded modes of solar/cmd.

    auto : pvsim's own logic (follow the sun with a deadband, stow at night)
    park : drive to the stow position (flat, facing east) and stay
    home : drive to the mechanical reference (flat, facing south) and stay
    ASSUMPTION: the home position; the real one is set by the limit switches.
    """

    HOME = (0.0, 180.0)  # tilt, azimuth

    def __init__(self, params: TrackerParams | None = None):
        super().__init__(params)
        self.command = "auto"

    def reset(self) -> None:
        super().reset()
        self.command = "auto"

    @property
    def motor_wh(self) -> float:
        return self.p.p_motor * self.motor_s / 3600.0

    def align(self, sun: SunPosition) -> None:
        """Start already pointing at the sun (as if it had tracked since sunrise);
        used when a simulation starts in the middle of the day."""
        if sun.elevation >= self.p.min_elevation:
            self.tilt = min(sun.zenith, self.p.tilt_max)
            self.azimuth = sun.azimuth % 360.0
            self._target = None

    def update(self, sun: SunPosition, dt: float) -> tuple[float, float]:
        if self.command == "auto" or self.stuck:
            return super().update(sun, dt)
        p = self.p
        self.energy_wh += p.p_standby * dt / 3600.0
        target = (p.stow_tilt, p.stow_azimuth) if self.command == "park" else self.HOME
        step = p.speed * dt
        d_t = target[0] - self.tilt
        d_a = _wrap180(target[1] - self.azimuth)
        if abs(d_t) < 1e-6 and abs(d_a) < 1e-6:
            self._target = None
            return self.orientation
        self._target = target
        self.tilt += max(-step, min(step, d_t))
        self.azimuth = (self.azimuth + max(-step, min(step, d_a))) % 360.0
        move = min(max(abs(d_t), abs(d_a)) / p.speed, dt) if p.speed > 0 else 0.0
        self.motor_s += move
        self.energy_wh += p.p_motor * move / 3600.0
        if abs(d_t) <= step and abs(d_a) <= step:
            self._target = None
        return self.orientation

    def mode(self, sun: SunPosition | None) -> str:
        if self.stuck:
            return "stuck"
        if self.command in ("park", "home"):
            return self.command
        if sun is not None and sun.elevation < self.p.min_elevation:
            return "park"  # night stow
        return "track"


def ldr_errors(sun: SunPosition, tilt: float, azimuth: float, beam_frac: float,
               rng: np.random.Generator) -> tuple[float, float]:
    """Normalised four-quadrant LDR differences (e_az, e_el), roughly -1..1.

    Positive e_az: the sun is clockwise (west) of the panel azimuth.
    Positive e_el: the sun is higher than the panel normal.
    A shadow-wall sensor gives (L1 - L2)/(L1 + L2) ~ tan(error)/tan(sat), and
    only direct beam makes a contrast, so the signal scales with beam/POA.
    ASSUMPTION: geometric stand-in for the sensor of section 2.5.3.
    """
    if sun.elevation <= 0.0:
        return 0.0, 0.0
    panel_el = 90.0 - tilt
    d_el = sun.elevation - panel_el
    d_az = _wrap180(sun.azimuth - azimuth) * math.cos(math.radians(sun.elevation))
    k = 1.0 / math.tan(math.radians(LDR_SATURATION_DEG))

    def sig(d: float) -> float:
        d = max(-89.0, min(89.0, d))
        return max(-1.0, min(1.0, math.tan(math.radians(d)) * k))

    e_az = sig(d_az) * beam_frac + rng.normal(0.0, LDR_NOISE)
    e_el = sig(d_el) * beam_frac + rng.normal(0.0, LDR_NOISE)
    return max(-1.0, min(1.0, e_az)), max(-1.0, min(1.0, e_el))


# --------------------------------------------------------------------- plant
class PanelPlant:
    """One rig: module + buck + e-load + 2 x INA226 + MPPT + mount."""

    def __init__(self, node: str, mount, mppt: str = "po", load_ohm: float = 3.0,
                 mppt_dt: float = 0.1, seed: int = 1, d_init: float = 0.3):
        if mppt not in ALGO:
            raise ValueError(f"mppt must be one of {list(ALGO)}")
        self.node = node
        self.mount = mount
        self.mppt = mppt
        self.d_init = d_init
        cfg = INA226Config()
        self.ch = LiveChannel(
            datasheet=PanelDatasheet(),
            conv=make_converter("buck"),
            load=ElectronicLoad("CR", load_ohm),
            algo=create_mppt(ALGO[mppt], d_init=d_init),
            sensor=INA226(cfg, seed=seed * 1000 + 1),
            dt=mppt_dt,
        )
        self.out_sensor = INA226(cfg, seed=seed * 1000 + 2)
        self._sensor_cfg = cfg
        self.rng = np.random.default_rng(seed * 7919)
        self.running = True
        self.shade_name = "normal"
        self.lab_shade = SHADE_PRESETS["normal"]
        self.fault: str | None = None
        self.sun: SunPosition | None = None
        self.poa = None
        self.t_amb = 30.0
        self._reset_window()

    # ---------------------------------------------------------- commands
    def set_mppt(self, name: str) -> None:
        if name not in ALGO:
            raise ValueError(f"unknown mppt {name!r}")
        self.mppt = name
        if self.running:  # switch algorithm, keep the present duty
            self.ch.algo = create_mppt(ALGO[name], d_init=self.ch.algo.duty)

    def set_running(self, on: bool) -> None:
        if on == self.running:
            return
        self.running = on
        if on:
            self.ch.algo = create_mppt(ALGO[self.mppt], d_init=self.d_init)
        else:  # converter off: duty 0, the panel floats at open circuit
            self.ch.algo = create_mppt("manual", duty=0.0)

    def set_shade(self, preset: str | None = None, depth=None) -> None:
        if depth is not None:
            d = tuple(float(max(0.0, min(1.0, x))) for x in list(depth)[:3])
            self.lab_shade = d + (0.0,) * (3 - len(d))
            self.shade_name = preset or "custom"
        else:
            if preset not in SHADE_PRESETS:
                raise ValueError(f"unknown shade preset {preset!r}; choose from {list(SHADE_PRESETS)}")
            self.lab_shade = SHADE_PRESETS[preset]
            self.shade_name = preset

    def set_fault(self, kind: str | None) -> None:
        if kind in (None, "", "none", "normal"):
            self.fault = None
        elif kind in FAULTS:
            self.fault = kind
        else:
            raise ValueError(f"unknown fault {kind!r}; choose from {FAULTS}")

    def reboot(self) -> None:
        """Power cycle: MPPT state is lost (duty back to d_init)."""
        self.ch.algo = create_mppt(ALGO[self.mppt], d_init=self.d_init) if self.running \
            else create_mppt("manual", duty=0.0)
        self.ch.reset()

    # ------------------------------------------------------- environment
    def apply_env(self, env: DayEnvironment, s, dt_env: float) -> None:
        """Refresh orientation and module conditions (pvsim DayRunner._refresh_env)."""
        stuck = self.fault == "tracker_stuck"
        if hasattr(self.mount, "stuck"):
            self.mount.stuck = stuck
        tilt, az = self.mount.update(s.sun, dt_env)
        cond, poa = env.conditions(s, tilt, az)
        shade = [_combine(a, b) for a, b in zip(cond.shade, self.lab_shade)]
        shorted = None
        cfg = self._sensor_cfg
        if self.fault == "soiling":
            shade = [_combine(x, FAULT_KINDS["soiling"]["loss"]) for x in shade]
        elif self.fault == "bypass_diode_short":
            sh = [False] * len(shade)
            sh[FAULT_KINDS["bypass_diode_short"]["substring"] % len(sh)] = True
            shorted = tuple(sh)
        elif self.fault == "sensor_offset":
            k = FAULT_KINDS["sensor_offset"]
            cfg = replace(cfg, offset_v=cfg.offset_v + k["offset_v"], offset_i=cfg.offset_i + k["offset_i"])
        self.ch.cond = Conditions(g=cond.g, shade=tuple(round(x, 3) for x in shade), t_amb=cond.t_amb)
        self.ch.shorted = shorted
        self.ch.open_circuit = self.fault == "open_circuit"
        self.ch.sensor.cfg = cfg
        self.sun, self.poa, self.t_amb = s.sun, poa, cond.t_amb

    # ------------------------------------------------------------ stepping
    def _reset_window(self) -> None:
        self._n = 0
        self._acc = dict(v_pv=0.0, i_pv=0.0, p_pv=0.0, v_out=0.0, i_out=0.0, p_out=0.0,
                         duty=0.0, t_cell=0.0, p_mpp=0.0, p_pv_true=0.0)
        self._tilt0, self._az0 = self.mount.orientation
        self._motor_s0 = getattr(self.mount, "motor_s", 0.0)

    def mppt_step(self) -> None:
        snap = self.ch.step()
        mo = self.out_sensor.measure(snap.v_out, snap.i_out)
        a = self._acc
        a["v_pv"] += snap.v_meas
        a["i_pv"] += snap.i_meas
        a["p_pv"] += snap.v_meas * snap.i_meas
        a["v_out"] += mo.v
        a["i_out"] += mo.i
        a["p_out"] += mo.v * mo.i
        a["duty"] += snap.duty
        a["t_cell"] += snap.t_cell
        a["p_mpp"] += snap.p_mpp
        a["p_pv_true"] += snap.p_pv
        self._n += 1

    def close_window(self, window_s: float) -> tuple[dict, dict]:
        """Averages of the window -> (telemetry fields, simulation ground truth)."""
        n = max(self._n, 1)
        a = {k: v / n for k, v in self._acc.items()}
        rng = self.rng
        tel = {
            "v_pv": round(a["v_pv"], 2), "i_pv": round(a["i_pv"], 3), "p_pv": round(a["p_pv"], 2),
            "v_out": round(a["v_out"], 2), "i_out": round(a["i_out"], 3), "p_out": round(a["p_out"], 2),
            "duty": round(a["duty"], 3),
            "t_panel": round(a["t_cell"] + rng.normal(0.0, TEMP_NOISE), 1),
        }
        poa_total = self.poa.total if self.poa is not None else 0.0
        if isinstance(self.mount, TrackerSim):
            tilt, az = self.mount.orientation
            beam_frac = self.poa.beam / poa_total if poa_total > 1e-9 else 0.0
            e_az, e_el = ldr_errors(self.sun, tilt, az, beam_frac, rng) if self.sun else (0.0, 0.0)
            d_el = (90.0 - tilt) - (90.0 - self._tilt0)
            d_az = _wrap180(az - self._az0)
            motor = self.mount.motor_s - self._motor_s0
            tel.update({
                "az": round(az, 1), "el": round(90.0 - tilt, 1),
                "e_az": round(e_az, 3), "e_el": round(e_el, 3),
                "m_az": int(math.copysign(1, d_az)) if abs(d_az) > 1e-3 else 0,
                "m_el": int(math.copysign(1, d_el)) if abs(d_el) > 1e-3 else 0,
                "p_mot": round(self.mount.p.p_motor * motor / window_s, 2),
                "mode": self.mount.mode(self.sun),
            })
        else:  # ESP32 #1 also reads the reference cell and the BME280
            g = poa_total * (1.0 + rng.normal(0.0, REF_CELL_NOISE)) + rng.normal(0.0, 1.0)
            tel["g"] = round(max(g, 0.0), 1)
            tel["t_amb"] = round(self.t_amb + rng.normal(0.0, TEMP_NOISE), 1)
        label = self.shade_name if self.shade_name != "normal" else (self.fault or "normal")
        truth = {
            "p_mpp": round(a["p_mpp"], 3), "p_pv_true": round(a["p_pv_true"], 3),
            "g_poa": round(poa_total, 1),
            "shade_1": self.lab_shade[0], "shade_2": self.lab_shade[1], "shade_3": self.lab_shade[2],
            "sun_el": round(self.sun.elevation, 2) if self.sun else None,
            "sun_az": round(self.sun.azimuth, 2) if self.sun else None,
            "label": label, "running": int(self.running),
        }
        self._reset_window()
        return tel, truth


def make_fixed(mppt: str = "po", mppt_dt: float = 0.1, seed: int = 1,
               tilt: float = 14.0, azimuth: float = 180.0) -> PanelPlant:
    return PanelPlant("fixed", FixedMount(tilt, azimuth), mppt=mppt, mppt_dt=mppt_dt, seed=seed)


def make_track(mppt: str = "po", mppt_dt: float = 0.1, seed: int = 2) -> PanelPlant:
    return PanelPlant("track", TrackerSim(), mppt=mppt, mppt_dt=mppt_dt, seed=seed)
