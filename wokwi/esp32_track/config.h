/*
 * ESP32 #2 - Tracking channel (MPPT + two-axis tracker).  Section 2.7.1: both boards run
 * the same firmware; this file switches the tracker module on.
 */
#pragma once

// ---------------------------------------------------------------- identity
#define NODE_ID "track"
#define ENABLE_TRACKER 1
#define DEFAULT_MPPT MPPT_PO  // MPPT_PO or MPPT_INCCOND (changeable with solar/cmd)

// ----------------------------------------------------------------- network
// Wokwi: open access point "Wokwi-GUEST" with internet access (channel 6 skips the scan).
// The free Wokwi gateway reaches only the internet, so use a public broker and a unique
// topic prefix; the Mosquitto bridge (system_sim/mosquitto/bridge) copies these topics
// into the simulated Pi.  On the real rig: your Wi-Fi, mqtt://<ip of the Pi>, prefix "".
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASS ""
#define WIFI_CHANNEL 6
#define MQTT_URI "mqtt://broker.hivemq.com:1883"
#define MQTT_USER ""
#define MQTT_PASS ""
#define TOPIC_PREFIX "tu-solar-32b21d/"  // must match the bridge; make it unique
#define NTP_SERVER "pool.ntp.org"           // real rig: the Pi running chrony
#define KEEPALIVE_S 10                      // the Will arrives after 1.5 x 10 s
#define T_PUB_MS 1000                       // telemetry period T_pub
#define T_MPPT_MS 100                       // MPPT period
#define RING_SIZE 600                       // 10 min at 1 s (eq. 2.7-1)
#define MAX_INFLIGHT 20                     // QoS 1 messages waiting for PUBACK

// -------------------------------------------------------------------- pins
// ADC1 pins only (ADC2 cannot be read while Wi-Fi is on)
#define PIN_IRRADIANCE 36   // potentiometer: irradiance on an ideally aimed panel, 0..1200 W/m2
#define PIN_NTC_PANEL 39    // NTC: panel temperature
#define PIN_NTC_AMBIENT 39  // (not used on the tracking board)
#define PIN_LDR_UP 34
#define PIN_LDR_DOWN 35
#define PIN_LDR_EAST 32
#define PIN_LDR_WEST 33
#define PIN_AZ_STEP 26
#define PIN_AZ_DIR 27
#define PIN_EL_STEP 25
#define PIN_EL_DIR 14
#define PIN_PWM 18          // gate PWM of the buck (look at it with a logic analyzer)
#define PIN_LED 23          // on = MQTT connected
#define PIN_BUTTON 13       // press = Wi-Fi off / on (tests Last Will + ring buffer)
#define PWM_FREQ 40000      // as BuckParams.f_sw in pvsim
#define PWM_BITS 10

// -------------------------------------------------------------------- site
#define SITE_LAT 14.07
#define SITE_LON 100.60
#define SUN_CLOCK_OFFSET_MIN 0  // e.g. 600 to watch daytime tracking while testing at night

// ----------------------------------------------------------------- tracker
#define STEPS_PER_DEG (200.0f * 16.0f * 1.0f / 360.0f)  // 1.8 deg motor x 1/16 microstep x gear 1:1 (Wokwi)
#define HOME_AZ 180.0f
#define HOME_EL 90.0f      // flat, facing south (mechanical reference)
#define PARK_AZ 90.0f
#define PARK_EL 90.0f      // flat, facing east for the next sunrise
#define AZ_MIN 60.0f
#define AZ_MAX 300.0f
#define EL_MIN 10.0f       // tilt limit 80 deg (pvsim TrackerParams.tilt_max)
#define MIN_EL 3.0f        // below this sun elevation: park (pvsim min_elevation)
#define TRACK_SPEED 0.5f   // deg/s per axis while tracking (pvsim TrackerParams.speed)
#define SLEW_SPEED 3.0f    // deg/s for park / home / start-up
#define POS_DB_ON 2.0f     // start moving above this error (pvsim deadband 2 deg)
#define POS_DB_OFF 0.2f    // stop below this error (hysteresis)
#define LDR_DB_ON 0.08f    // fine trim starts above |e| = 0.08
#define LDR_DB_OFF 0.03f   // and stops below 0.03
#define FINE_RATE 0.2f     // deg/s
#define FINE_MAX 10.0f     // the LDR may move the target at most +-10 deg from the sun
#define LDR_MIN_LUX 2000.0f  // too dark / overcast: ignore the LDRs
#define LDR_GAMMA 0.7f
#define LDR_RL10 50.0f
#define P_MOTOR_W 2.0f     // per moving axis (pvsim: 4 W for both)
#define TRACKER_DT_MS 20
