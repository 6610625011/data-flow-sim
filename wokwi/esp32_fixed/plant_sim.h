/*
 * Simulated plant for Wokwi (SIM_PLANT = 1): 50 W module + buck + resistive load.
 * Wokwi has no solar-panel or INA226 part, so the firmware asks this model for the
 * readings the two INA226 would give.  On the real board replace plant_measure()
 * by the INA226 driver; nothing else in the firmware changes.
 *
 * PV curve: simplified explicit model built from the datasheet points
 *     I(V) = Isc [1 - C1 (exp(V / (C2 Voc)) - 1)]
 *     C2 = (Vmp/Voc - 1) / ln(1 - Imp/Isc),  C1 = (1 - Imp/Isc) exp(-Vmp / (C2 Voc))
 * which passes exactly through (0, Isc), (Vmp, Imp) and (Voc, 0).  Isc scales with G
 * and alpha, Voc with ln(G) and beta.  Buck: ideal CCM, R_in = R_load / D^2, 95 %
 * efficiency.  ASSUMPTION: only a stand-in for the Wokwi demo - the accurate physics
 * (single-diode model, bypass diodes, buck losses) is pvsim.
 */
#pragma once
#include <math.h>
#include <stdlib.h>

typedef struct {
  float v_pv, i_pv, v_out, i_out;
} meas_t;

// same datasheet as pvsim's default 50 W, 36-cell module
#define PV_VOC 21.6f
#define PV_ISC 3.0f
#define PV_VMP 18.0f
#define PV_IMP 2.78f
#define PV_ALPHA 0.0005f   // Isc  +0.05 %/K
#define PV_BETA (-0.0032f) // Voc  -0.32 %/K
#define PV_NS 36
#define LOAD_OHM 3.0f      // electronic load, CR mode (as in pvsim's live channel)
#define BUCK_EFF 0.95f

static float pv_current(float v, float isc, float voc, float c1, float c2) {
  float i = isc * (1.0f - c1 * (expf(v / (c2 * voc)) - 1.0f));
  return i > 0.0f ? i : 0.0f;
}

static float noise(float sigma) {  // Box-Muller, enough for sensor noise
  float u1 = (rand() + 1.0f) / ((float)RAND_MAX + 2.0f);
  float u2 = (rand() + 1.0f) / ((float)RAND_MAX + 2.0f);
  return sigma * sqrtf(-2.0f * logf(u1)) * cosf(6.2831853f * u2);
}

static meas_t plant_measure(float duty, float g, float t_cell) {
  static const float c2 = (PV_VMP / PV_VOC - 1.0f) / logf(1.0f - PV_IMP / PV_ISC);
  static const float c1 = (1.0f - PV_IMP / PV_ISC) * expf(-PV_VMP / (c2 * PV_VOC));
  meas_t m = {0.0f, 0.0f, 0.0f, 0.0f};
  float dT = t_cell - 25.0f;
  if (g > 1.0f) {
    float isc = PV_ISC * (g / 1000.0f) * (1.0f + PV_ALPHA * dT);
    float voc = (PV_VOC + PV_NS * 1.3f * 0.025693f * logf(g / 1000.0f)) * (1.0f + PV_BETA * dT);
    if (voc < 0.5f) voc = 0.5f;
    if (duty < 0.02f) {  // converter off: open circuit
      m.v_pv = voc;
    } else {             // operating point: I(V) = V / R_in, bisection
      float rin = LOAD_OHM / (duty * duty);
      float lo = 0.0f, hi = voc;
      for (int k = 0; k < 40; k++) {
        float mid = 0.5f * (lo + hi);
        if (pv_current(mid, isc, voc, c1, c2) - mid / rin > 0.0f) lo = mid; else hi = mid;
      }
      m.v_pv = 0.5f * (lo + hi);
      m.i_pv = m.v_pv / rin;
    }
  }
  float p_out = BUCK_EFF * m.v_pv * m.i_pv;
  m.v_out = sqrtf(p_out * LOAD_OHM);
  m.i_out = m.v_out / LOAD_OHM;
  // INA226-like reading noise (pvsim/sensors/ina226.py uses 2 mV / ~1 mA)
  m.v_pv = fmaxf(0.0f, m.v_pv + noise(0.002f));
  m.i_pv = fmaxf(0.0f, m.i_pv + noise(0.001f));
  m.v_out = fmaxf(0.0f, m.v_out + noise(0.002f));
  m.i_out = fmaxf(0.0f, m.i_out + noise(0.001f));
  return m;
}
