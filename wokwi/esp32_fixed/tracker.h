/*
 * Two-axis tracker for ESP32 #2 (section 2.5), hybrid control:
 *   coarse target = sun position from the synced clock (sun.h)
 *   fine trim     = four-LDR differences with deadband + hysteresis, limited to +-FINE_MAX
 *                   so a bright cloud edge cannot pull the panel far from the sun
 *   night         = park (flat, facing east) while the sun is below MIN_EL
 * Each axis moves only when its error exceeds POS_DB_ON and stops below POS_DB_OFF,
 * so the motors run in short bursts (the clean-sample logic of section 2.7.1).
 *
 * Angles: az = panel azimuth (deg from north, clockwise), el = elevation of the panel
 * normal (90 = panel flat).  Motors: STEP/DIR drivers (TMC2209 on the rig, A4988 in Wokwi).
 * Position comes from counting steps since the home position (limit switches on the rig).
 */
#pragma once
#include <math.h>
#include "config.h"
#include "sun.h"

typedef enum { TM_TRACK = 0, TM_PARK = 1, TM_HOME = 2 } tracker_mode_t;
typedef enum { TC_AUTO = 0, TC_PARK = 1, TC_HOME = 2 } tracker_cmd_t;

static const char *tracker_mode_name(tracker_mode_t m) {
  return m == TM_TRACK ? "track" : (m == TM_PARK ? "park" : "home");
}
static int tracker_cmd_parse(const char *v) {
  if (!strcmp(v, "auto")) return TC_AUTO;
  if (!strcmp(v, "park")) return TC_PARK;
  if (!strcmp(v, "home")) return TC_HOME;
  return -1;
}

typedef struct {
  int pin_step, pin_dir;
  float home_deg, lo_deg, hi_deg;
  volatile long pos;  // microsteps from home
  bool moving;
  float frac;         // fractional steps carried between ticks
} axis_t;

static axis_t ax_az = {PIN_AZ_STEP, PIN_AZ_DIR, HOME_AZ, AZ_MIN, AZ_MAX, 0, false, 0};
static axis_t ax_el = {PIN_EL_STEP, PIN_EL_DIR, HOME_EL, EL_MIN, 90.0f, 0, false, 0};
static volatile tracker_cmd_t tr_cmd = TC_AUTO;
static volatile tracker_mode_t tr_mode = TM_HOME;
static volatile float tr_e_az = 0, tr_e_el = 0, tr_sun_az = 0, tr_sun_el = -90;
static volatile float tr_motor_wh = 0;  // motor energy, read as a difference per record
static float tr_off_az = 0, tr_off_el = 0;
static bool tr_fine_az = false, tr_fine_el = false;

static float axis_deg(const axis_t *a) { return a->home_deg + a->pos / STEPS_PER_DEG; }

static float ldr_lux(int pin) {  // Wokwi photoresistor module on 3.3 V (10k divider)
  float v = analogRead(pin) * (3.3f / 4095.0f);
  if (v >= 3.29f) return 0.0f;
  if (v <= 0.001f) return 1e5f;
  float r = 10000.0f * v / (3.3f - v);
  float lux = powf(LDR_RL10 * 1e3f * powf(10.0f, LDR_GAMMA) / r, 1.0f / LDR_GAMMA);
  return isfinite(lux) ? lux : 1e5f;
}

static void tracker_command(tracker_cmd_t c) { tr_cmd = c; }

static void tracker_begin() {
  pinMode(PIN_AZ_STEP, OUTPUT);
  pinMode(PIN_AZ_DIR, OUTPUT);
  pinMode(PIN_EL_STEP, OUTPUT);
  pinMode(PIN_EL_DIR, OUTPUT);
}

// move one axis toward target with deadband/hysteresis; returns 1 if it stepped
static int axis_drive(axis_t *a, float target, float speed_deg_s, float dt) {
  if (target < a->lo_deg) target = a->lo_deg;
  if (target > a->hi_deg) target = a->hi_deg;
  float err = target - axis_deg(a);
  if (!a->moving && fabsf(err) > POS_DB_ON) a->moving = true;
  if (a->moving && fabsf(err) < POS_DB_OFF) { a->moving = false; a->frac = 0; }
  if (!a->moving) return 0;
  a->frac += speed_deg_s * STEPS_PER_DEG * dt;
  long n = (long)a->frac;
  long need = (long)(fabsf(err) * STEPS_PER_DEG + 0.5f);
  if (n > need) n = need;
  a->frac -= n;
  int dir = err > 0 ? 1 : -1;
  digitalWrite(a->pin_dir, dir > 0 ? HIGH : LOW);
  delayMicroseconds(2);
  for (long k = 0; k < n; k++) {
    digitalWrite(a->pin_step, HIGH);
    delayMicroseconds(3);
    digitalWrite(a->pin_step, LOW);
    delayMicroseconds(3);
    a->pos += dir;
  }
  return 1;
}

static void task_tracker(void *) {
  const float dt = TRACKER_DT_MS / 1000.0f;
  uint32_t last_sun = 0;
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(TRACKER_DT_MS));
    if (millis() - last_sun > 1000 && time(nullptr) > 1700000000) {
      last_sun = millis();
      sun_t s = sun_position((double)time(nullptr) + SUN_CLOCK_OFFSET_MIN * 60.0, SITE_LAT, SITE_LON);
      tr_sun_az = s.azimuth;
      tr_sun_el = s.elevation;
    }
    // four-quadrant LDR: normalised differences
    float up = ldr_lux(PIN_LDR_UP), dn = ldr_lux(PIN_LDR_DOWN);
    float ea = ldr_lux(PIN_LDR_EAST), we = ldr_lux(PIN_LDR_WEST);
    bool bright = (up + dn + ea + we) > LDR_MIN_LUX;
    tr_e_el = bright && (up + dn) > 0 ? (up - dn) / (up + dn) : 0.0f;
    tr_e_az = bright && (ea + we) > 0 ? (we - ea) / (we + ea) : 0.0f;

    float t_az, t_el, speed = TRACK_SPEED;
    if (tr_cmd == TC_PARK || (tr_cmd == TC_AUTO && tr_sun_el < MIN_EL)) {
      tr_mode = TM_PARK;
      t_az = PARK_AZ; t_el = PARK_EL; speed = SLEW_SPEED;
      tr_off_az = tr_off_el = 0;
    } else if (tr_cmd == TC_HOME) {
      tr_mode = TM_HOME;
      t_az = HOME_AZ; t_el = HOME_EL; speed = SLEW_SPEED;
    } else {
      tr_mode = TM_TRACK;
      // fine trim with hysteresis: start above DB_ON, stop below DB_OFF
      if (!tr_fine_az && fabsf(tr_e_az) > LDR_DB_ON) tr_fine_az = true;
      if (tr_fine_az && fabsf(tr_e_az) < LDR_DB_OFF) tr_fine_az = false;
      if (!tr_fine_el && fabsf(tr_e_el) > LDR_DB_ON) tr_fine_el = true;
      if (tr_fine_el && fabsf(tr_e_el) < LDR_DB_OFF) tr_fine_el = false;
      if (tr_fine_az) tr_off_az += (tr_e_az > 0 ? 1 : -1) * FINE_RATE * dt;
      if (tr_fine_el) tr_off_el += (tr_e_el > 0 ? 1 : -1) * FINE_RATE * dt;
      tr_off_az = fmaxf(-FINE_MAX, fminf(FINE_MAX, tr_off_az));
      tr_off_el = fmaxf(-FINE_MAX, fminf(FINE_MAX, tr_off_el));
      t_az = tr_sun_az + tr_off_az;
      t_el = fmaxf(tr_sun_el, EL_MIN) + tr_off_el;
      // far from the target (start-up, after park): slew fast
      if (fabsf(t_az - axis_deg(&ax_az)) > 10 || fabsf(t_el - axis_deg(&ax_el)) > 10) speed = SLEW_SPEED;
    }
    int moving = axis_drive(&ax_az, t_az, speed, dt) + axis_drive(&ax_el, t_el, speed, dt);
    tr_motor_wh += moving * P_MOTOR_W * dt / 3600.0f;
  }
}

// cosine loss of the actual pointing error (scales the potentiometer irradiance of the demo)
static float tracker_irradiance_factor() {
  if (tr_sun_el < MIN_EL) return 1.0f;  // night / no sun: keep the potentiometer value
  const float D = (float)M_PI / 180.0f;
  float pel = axis_deg(&ax_el) * D, paz = axis_deg(&ax_az) * D;
  float sel = tr_sun_el * D, saz = tr_sun_az * D;
  float c = sinf(pel) * sinf(sel) + cosf(pel) * cosf(sel) * cosf(saz - paz);
  return c > 0 ? c : 0;
}

// values for one telemetry record (called once per T_PUB by task_control)
static void tracker_snapshot(float *az, float *el, float *e_az, float *e_el, int8_t *m_az, int8_t *m_el,
                             float *p_mot, uint8_t *mode) {
  static long pa0 = 0, pe0 = 0;
  static float wh0 = 0;
  static uint32_t t0 = 0;
  uint32_t now = millis();
  float span = t0 ? (now - t0) / 1000.0f : T_PUB_MS / 1000.0f;
  long pa = ax_az.pos, pe = ax_el.pos;
  float wh = tr_motor_wh;
  *az = axis_deg(&ax_az);
  *el = axis_deg(&ax_el);
  *e_az = tr_e_az;
  *e_el = tr_e_el;
  *m_az = pa > pa0 ? 1 : (pa < pa0 ? -1 : 0);
  *m_el = pe > pe0 ? 1 : (pe < pe0 ? -1 : 0);
  *p_mot = span > 0 ? (wh - wh0) * 3600.0f / span : 0;
  *mode = (uint8_t)tr_mode;
  pa0 = pa; pe0 = pe; wh0 = wh; t0 = now;
}
