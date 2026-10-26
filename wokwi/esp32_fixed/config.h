/*
 * ESP32 #1 - Fixed channel (MPPT + environment sensors).  Section 2.7.1: both boards run
 * the same firmware; this file switches the tracker module off.
 */
#pragma once

// ---------------------------------------------------------------- identity
#define NODE_ID "fixed"
#define ENABLE_TRACKER 0
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
#define PIN_IRRADIANCE 36   // potentiometer: reference cell, plane-of-array irradiance 0..1200 W/m2
#define PIN_NTC_PANEL 39    // NTC: panel temperature
#define PIN_NTC_AMBIENT 34  // NTC: ambient temperature (BME280 on the rig)
#define PIN_PWM 18          // gate PWM of the buck (look at it with a logic analyzer)
#define PIN_LED 23          // on = MQTT connected
#define PIN_BUTTON 13       // press = Wi-Fi off / on (tests Last Will + ring buffer)
#define PWM_FREQ 40000      // as BuckParams.f_sw in pvsim
#define PWM_BITS 10

// -------------------------------------------------------------------- site
#define SITE_LAT 14.07
#define SITE_LON 100.60
