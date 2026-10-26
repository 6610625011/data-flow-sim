/*
 * Sun position on the ESP32 - the NOAA Solar Calculator equations, the same ones pvsim
 * uses (pvsim/environment/solar_position.py, Meeus ch. 24-27), without refraction.
 * Input: UTC seconds since 1970.  Output: azimuth clockwise from north and elevation [deg].
 */
#pragma once
#include <math.h>

typedef struct {
  float azimuth;
  float elevation;
} sun_t;

static sun_t sun_position(double utc_s, double lat_deg, double lon_deg) {
  const double D = M_PI / 180.0;
  double jd = utc_s / 86400.0 + 2440587.5;
  double jc = (jd - 2451545.0) / 36525.0;
  double L0 = fmod(280.46646 + jc * (36000.76983 + 0.0003032 * jc), 360.0);
  double M = 357.52911 + jc * (35999.05029 - 0.0001537 * jc);
  double e = 0.016708634 - jc * (0.000042037 + 1.267e-7 * jc);
  double C = sin(M * D) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) +
             sin(2 * M * D) * (0.019993 - 0.000101 * jc) + sin(3 * M * D) * 0.000289;
  double omega = 125.04 - 1934.136 * jc;
  double lambda = L0 + C - 0.00569 - 0.00478 * sin(omega * D);
  double eps0 = 23.0 + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0;
  double eps = eps0 + 0.00256 * cos(omega * D);
  double decl = asin(sin(eps * D) * sin(lambda * D));
  double y = tan(eps * D / 2.0);
  y *= y;
  double eot = 4.0 / D *
               (y * sin(2 * L0 * D) - 2 * e * sin(M * D) + 4 * e * y * sin(M * D) * cos(2 * L0 * D) -
                0.5 * y * y * sin(4 * L0 * D) - 1.25 * e * e * sin(2 * M * D));  // [min]
  double minutes_utc = fmod(utc_s, 86400.0) / 60.0;
  double tst = fmod(minutes_utc + eot + 4.0 * lon_deg, 1440.0);
  if (tst < 0) tst += 1440.0;
  double ha = tst / 4.0 - 180.0;
  double lat = lat_deg * D, h = ha * D;
  double cz = sin(lat) * sin(decl) + cos(lat) * cos(decl) * cos(h);
  if (cz > 1) cz = 1;
  if (cz < -1) cz = -1;
  double zen = acos(cz);
  double az;
  double den = cos(lat) * sin(zen);
  if (fabs(den) > 1e-9) {
    double ca = (sin(lat) * cos(zen) - sin(decl)) / den;
    if (ca > 1) ca = 1;
    if (ca < -1) ca = -1;
    double a = acos(ca) / D;
    az = ha > 0 ? fmod(a + 180.0, 360.0) : fmod(540.0 - a, 360.0);
  } else {
    az = lat_deg > 0 ? 180.0 : 0.0;
  }
  sun_t s = {(float)az, (float)(90.0 - zen / D)};
  return s;
}
