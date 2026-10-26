/*
 * ESP32 solar node - IoT side of section 2.7 and the firmware structure of 2.9.2
 * (runs in Wokwi now, and on the real ESP32 #1 / #2 later).
 *
 *   Core 1  task_control  every 100 ms: measure -> MPPT (pvsim's mppt.c) -> PWM duty,
 *                         average 1 s -> one telemetry record into queue q_tel
 *   Core 1  task_tracker  (ESP32 #2 only) hybrid sun tracking: sun position + 4-LDR fine trim
 *   Core 0  task_comm     ring buffer -> MQTT QoS 1 (ESP-MQTT), backlog replay after Wi-Fi loss
 *
 * Topics (Table 2.7-1): solar/<node>/telemetry, solar/<node>/status (retained + Last Will),
 * solar/cmd.  All node-specific settings are in config.h.
 *
 * Wokwi parts used instead of the real hardware (SIM_PLANT = 1):
 *   potentiometer  -> irradiance on the module (stands in for the reference cell)
 *   NTC sensors    -> panel / ambient temperature (stand in for DS18B20 / BME280)
 *   plant_sim.h    -> PV module + buck + load (Wokwi has no INA226 or solar panel part)
 *   push button    -> "Wi-Fi off/on" to test the Last Will and the ring buffer
 */
#include <WiFi.h>
#include <sys/time.h>
#include <time.h>
#include "mqtt_client.h"
#include "esp_sntp.h"

#include "config.h"
#include "mppt.h"
#include "plant_sim.h"
#include "sun.h"
#if ENABLE_TRACKER
#include "tracker.h"
#endif

// ------------------------------------------------------------------ types
typedef struct {
  int64_t ts;
  uint32_t seq;
  float v_pv, i_pv, p_pv, v_out, i_out, p_out, duty, t_panel, g, t_amb;
  uint8_t algo;  // mppt_algo_t
#if ENABLE_TRACKER
  float az, el, e_az, e_el, p_mot;
  int8_t m_az, m_el;
  uint8_t mode;
#endif
} tel_t;  // ~80 bytes -> 600 records = 48 kB (eq. 2.7-1)

typedef struct {
  int8_t algo;     // -1 = no change
  int8_t run;      // -1 = no change, 0 = stop, 1 = start
  int8_t tracker;  // -1 = no change, else tracker_cmd_t
} cmd_t;

// ------------------------------------------------------------------ state
static QueueHandle_t q_tel, q_cmd;
static esp_mqtt_client_handle_t mqtt = nullptr;
static volatile bool mqtt_up = false;
static volatile bool wifi_on = true;
static int inflight = 0;  // QoS 1 messages awaiting PUBACK (atomic: two tasks touch it)
static char t_tel[96], t_status[96], t_cmd[96];

static tel_t ring[RING_SIZE];  // owned by task_comm
static int r_head = 0, r_count = 0;
static uint32_t r_lost = 0, n_sent = 0, n_acked = 0;  // r_lost: atomic (three tasks)
#define ATOMIC_INC(x) __atomic_fetch_add(&(x), 1, __ATOMIC_RELAXED)
#define ATOMIC_DEC_POS(x) do { if (__atomic_load_n(&(x), __ATOMIC_RELAXED) > 0) __atomic_fetch_sub(&(x), 1, __ATOMIC_RELAXED); } while (0)

static int64_t now_ms() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}
static bool time_valid() { return time(nullptr) > 1700000000; }  // after SNTP sync

// --------------------------------------------------------------- analog in
static float read_ntc_c(int pin) {  // Wokwi NTC module (BETA 3950, 10k)
  int a = analogRead(pin);
  if (a <= 0 || a >= 4095) return NAN;
  return 1.0f / (logf(1.0f / (4095.0f / a - 1.0f)) / 3950.0f + 1.0f / 298.15f) - 273.15f;
}
static float read_irradiance() {  // potentiometer 0..4095 -> 0..1200 W/m2
  return analogRead(PIN_IRRADIANCE) * (1200.0f / 4095.0f);
}

// ------------------------------------------------------------------ PWM
static void pwm_begin() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(PIN_PWM, PWM_FREQ, PWM_BITS);
#else
  ledcSetup(0, PWM_FREQ, PWM_BITS);
  ledcAttachPin(PIN_PWM, 0);
#endif
}
static void pwm_write(float d) {
  uint32_t counts = (uint32_t)(d * ((1 << PWM_BITS) - 1) + 0.5f);
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PIN_PWM, counts);
#else
  ledcWrite(0, counts);
#endif
}

// ------------------------------------------------------------ JSON helpers
static const char *algo_name(uint8_t a) { return a == MPPT_INCCOND ? "inc" : "po"; }

static int to_json(const tel_t *t, char *buf, size_t len) {
  int n = snprintf(buf, len,
                   "{\"ts\":%lld,\"seq\":%u,\"v_pv\":%.2f,\"i_pv\":%.3f,\"p_pv\":%.2f,"
                   "\"v_out\":%.2f,\"i_out\":%.3f,\"p_out\":%.2f,\"duty\":%.3f,\"t_panel\":%.1f",
                   (long long)t->ts, (unsigned)t->seq, t->v_pv, t->i_pv, t->p_pv, t->v_out, t->i_out,
                   t->p_out, t->duty, t->t_panel);
#if ENABLE_TRACKER
  n += snprintf(buf + n, len - n,
                ",\"mppt\":\"%s\",\"az\":%.1f,\"el\":%.1f,\"e_az\":%.3f,\"e_el\":%.3f,"
                "\"m_az\":%d,\"m_el\":%d,\"p_mot\":%.2f,\"mode\":\"%s\"}",
                algo_name(t->algo), t->az, t->el, t->e_az, t->e_el, t->m_az, t->m_el, t->p_mot,
                tracker_mode_name((tracker_mode_t)t->mode));
#else
  n += snprintf(buf + n, len - n, ",\"g\":%.1f,\"t_amb\":%.1f,\"mppt\":\"%s\"}", t->g, t->t_amb,
                algo_name(t->algo));
#endif
  return n;
}

// find "key":"value" in a small JSON text (enough for solar/cmd)
static bool json_str(const char *js, int len, const char *key, char *out, size_t outlen) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\"", key);
  const char *end = js + len;
  const char *p = strstr(js, pat);
  if (!p || p >= end) return false;
  p = strchr(p + strlen(pat), ':');
  if (!p) return false;
  p = strchr(p, '"');
  if (!p || p >= end) return false;
  const char *q = strchr(p + 1, '"');
  if (!q || q >= end) return false;
  size_t n = min((size_t)(q - p - 1), outlen - 1);
  memcpy(out, p + 1, n);
  out[n] = 0;
  return true;
}

// --------------------------------------------------------------- MQTT events
static void on_mqtt(void *arg, esp_event_base_t base, int32_t id, void *data) {
  esp_mqtt_event_handle_t e = (esp_mqtt_event_handle_t)data;
  switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
      mqtt_up = true;
      esp_mqtt_client_publish(mqtt, t_status, "online", 0, 1, 1);  // retained
      esp_mqtt_client_subscribe(mqtt, t_cmd, 1);
      Serial.printf("[mqtt] connected, backlog %d records\n", r_count);
      digitalWrite(PIN_LED, HIGH);
      break;
    case MQTT_EVENT_DISCONNECTED:
      if (mqtt_up) Serial.println("[mqtt] disconnected");
      mqtt_up = false;
      digitalWrite(PIN_LED, LOW);
      break;
    case MQTT_EVENT_PUBLISHED:  // PUBACK of a QoS 1 message
      ATOMIC_DEC_POS(inflight);
      n_acked++;
      break;
    case MQTT_EVENT_DELETED:  // expired in the outbox: counts as lost
      ATOMIC_DEC_POS(inflight);
      ATOMIC_INC(r_lost);
      break;
    case MQTT_EVENT_DATA: {
      if (e->topic_len != (int)strlen(t_cmd) || strncmp(e->topic, t_cmd, e->topic_len) != 0) break;
      char v[16];
      cmd_t c = {-1, -1, -1};
      if (json_str(e->data, e->data_len, "target", v, sizeof(v)) && strcmp(v, "all") != 0 &&
          strcmp(v, NODE_ID) != 0)
        break;  // addressed to the other board
      if (json_str(e->data, e->data_len, "mppt", v, sizeof(v)))
        c.algo = !strcmp(v, "inc") ? MPPT_INCCOND : (!strcmp(v, "po") ? MPPT_PO : -1);
      if (json_str(e->data, e->data_len, "run", v, sizeof(v)))
        c.run = !strcmp(v, "start") ? 1 : (!strcmp(v, "stop") ? 0 : -1);
#if ENABLE_TRACKER
      if (json_str(e->data, e->data_len, "tracker", v, sizeof(v))) c.tracker = tracker_cmd_parse(v);
#endif
      xQueueSend(q_cmd, &c, 0);
      Serial.printf("[cmd] %.*s\n", e->data_len, e->data);
      break;
    }
    default:
      break;
  }
}

static void mqtt_start() {
  snprintf(t_tel, sizeof(t_tel), "%ssolar/%s/telemetry", TOPIC_PREFIX, NODE_ID);
  snprintf(t_status, sizeof(t_status), "%ssolar/%s/status", TOPIC_PREFIX, NODE_ID);
  snprintf(t_cmd, sizeof(t_cmd), "%ssolar/cmd", TOPIC_PREFIX);
  esp_mqtt_client_config_t cfg = {};
  cfg.broker.address.uri = MQTT_URI;
  cfg.credentials.client_id = "esp32-" NODE_ID;
  if (strlen(MQTT_USER)) {
    cfg.credentials.username = MQTT_USER;
    cfg.credentials.authentication.password = MQTT_PASS;
  }
  cfg.session.keepalive = KEEPALIVE_S;  // broker publishes the Will after 1.5 x keep-alive
  cfg.session.last_will.topic = t_status;
  cfg.session.last_will.msg = "offline";
  cfg.session.last_will.qos = 1;
  cfg.session.last_will.retain = 1;
  cfg.network.reconnect_timeout_ms = 2000;
  mqtt = esp_mqtt_client_init(&cfg);
  esp_mqtt_client_register_event(mqtt, (esp_mqtt_event_id_t)ESP_EVENT_ANY_ID, on_mqtt, nullptr);
  esp_mqtt_client_start(mqtt);
}

// ------------------------------------------------------------------ tasks
static void task_comm(void *) {
  static char json[420];
  uint32_t last_log = 0;
  for (;;) {
    tel_t t;
    while (xQueueReceive(q_tel, &t, pdMS_TO_TICKS(20)) == pdTRUE) {
      if (r_count == RING_SIZE) {  // full: overwrite the oldest record
        r_head = (r_head + 1) % RING_SIZE;
        r_count--;
        ATOMIC_INC(r_lost);
      }
      ring[(r_head + r_count) % RING_SIZE] = t;
      r_count++;
    }
    while (mqtt_up && wifi_on && r_count > 0 && __atomic_load_n(&inflight, __ATOMIC_RELAXED) < MAX_INFLIGHT) {
      const tel_t *x = &ring[r_head];
      int n = to_json(x, json, sizeof(json));
      int id = esp_mqtt_client_publish(mqtt, t_tel, json, n, 1, 0);
      if (id < 0) break;
      ATOMIC_INC(inflight);
      n_sent++;
      r_head = (r_head + 1) % RING_SIZE;
      r_count--;
    }
    if (millis() - last_log > 10000) {
      last_log = millis();
      Serial.printf("[comm] wifi=%s mqtt=%s buffer=%d sent=%u acked=%u lost=%u\n", wifi_on ? "on" : "OFF",
                    mqtt_up ? "up" : "down", r_count, (unsigned)n_sent, (unsigned)n_acked,
                    (unsigned)__atomic_load_n(&r_lost, __ATOMIC_RELAXED));
    }
  }
}

static void task_control(void *) {
  mppt_params_t p;
  mppt_default_params(&p);
  p.d_init = 0.3f;
  mppt_t m;
  uint8_t algo = DEFAULT_MPPT;
  mppt_init(&m, algo, &p);
  bool running = true;
  uint32_t seq = 0;
  float acc[10] = {0};
  int n = 0;
  float duty = m.s.d;
  int64_t next_pub = 0;
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(T_MPPT_MS));
    cmd_t c;
    while (xQueueReceive(q_cmd, &c, 0) == pdTRUE) {
      if (c.algo >= 0 && c.algo != algo) {  // switch algorithm, keep the present duty
        algo = c.algo;
        p.d_init = duty;
        mppt_init(&m, algo, &p);
      }
      if (c.run == 0) running = false;
      if (c.run == 1 && !running) { running = true; p.d_init = 0.3f; mppt_init(&m, algo, &p); }
#if ENABLE_TRACKER
      if (c.tracker >= 0) tracker_command((tracker_cmd_t)c.tracker);
#endif
    }
    // ---- measure (simulated plant in Wokwi; INA226 x 2 on the real board)
    float g = read_irradiance();
    float t_panel = read_ntc_c(PIN_NTC_PANEL);
    if (isnan(t_panel)) t_panel = 25.0f + g * (25.0f / 800.0f);  // NOCT estimate if no sensor
#if ENABLE_TRACKER
    g *= tracker_irradiance_factor();  // cosine loss of the actual pointing error
#endif
    meas_t s = plant_measure(running ? duty : 0.0f, g, t_panel);
    // ---- MPPT (same code on both boards, section 2.7.1)
    duty = running ? (float)mppt_step(&m, s.v_pv, s.i_pv) : 0.0f;
    pwm_write(duty);
    float a[10] = {s.v_pv, s.i_pv, s.v_pv * s.i_pv, s.v_out, s.i_out, s.v_out * s.i_out, duty, t_panel, g,
                   0.0f};
#if !ENABLE_TRACKER
    a[9] = read_ntc_c(PIN_NTC_AMBIENT);
#endif
    for (int k = 0; k < 10; k++) acc[k] += a[k];
    n++;
    // ---- one record per T_PUB, stamped with synced UTC time
    int64_t now = now_ms();
    if (!time_valid()) continue;
    if (next_pub == 0) next_pub = (now / T_PUB_MS + 1) * T_PUB_MS;
    if (now < next_pub) continue;
    tel_t t = {};
    t.ts = now;
    t.seq = seq++;
    t.v_pv = acc[0] / n; t.i_pv = acc[1] / n; t.p_pv = acc[2] / n;
    t.v_out = acc[3] / n; t.i_out = acc[4] / n; t.p_out = acc[5] / n;
    t.duty = acc[6] / n; t.t_panel = acc[7] / n; t.g = acc[8] / n; t.t_amb = acc[9] / n;
    t.algo = algo;
#if ENABLE_TRACKER
    tracker_snapshot(&t.az, &t.el, &t.e_az, &t.e_el, &t.m_az, &t.m_el, &t.p_mot, &t.mode);
#endif
    if (xQueueSend(q_tel, &t, 0) != pdTRUE) ATOMIC_INC(r_lost);
    memset(acc, 0, sizeof(acc));
    n = 0;
    next_pub += T_PUB_MS;
    if (next_pub < now) next_pub = (now / T_PUB_MS + 1) * T_PUB_MS;
  }
}

// --------------------------------------------------------- Wi-Fi on/off
static void wifi_connect() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS, WIFI_CHANNEL);
}

static void check_button() {
  static bool last = true;
  static uint32_t t_last = 0;
  bool b = digitalRead(PIN_BUTTON);
  if (b != last && millis() - t_last > 50) {
    t_last = millis();
    last = b;
    if (!b) {  // pressed
      wifi_on = !wifi_on;
      if (!wifi_on) {
        // drop the link without an MQTT DISCONNECT: the broker sends the Last Will
        Serial.println("[wifi] OFF (test): records go to the ring buffer");
        WiFi.setAutoReconnect(false);
        WiFi.disconnect(false, false);
      } else {
        Serial.println("[wifi] ON: reconnecting, backlog will be sent in order");
        wifi_connect();
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  analogReadResolution(12);
  pwm_begin();
  Serial.printf("\nESP32 solar node '%s' (tracker %s)\n", NODE_ID, ENABLE_TRACKER ? "on" : "off");
  wifi_connect();
  Serial.print("[wifi] connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(200);
    Serial.print(".");
  }
  Serial.printf(" ok, IP %s\n", WiFi.localIP().toString().c_str());
  configTime(0, 0, NTP_SERVER);  // UTC; real system: NTP server = the Raspberry Pi (chrony)
  Serial.print("[time] waiting for SNTP");
  for (int k = 0; k < 100 && !time_valid(); k++) {
    delay(200);
    Serial.print(".");
  }
  Serial.printf(" %s\n", time_valid() ? "synced" : "NOT synced (records wait)");

  q_tel = xQueueCreate(32, sizeof(tel_t));
  q_cmd = xQueueCreate(8, sizeof(cmd_t));
  mqtt_start();
#if ENABLE_TRACKER
  tracker_begin();
  xTaskCreatePinnedToCore(task_tracker, "tracker", 4096, nullptr, 3, nullptr, 1);
#endif
  xTaskCreatePinnedToCore(task_control, "control", 6144, nullptr, 5, nullptr, 1);  // real-time core
  xTaskCreatePinnedToCore(task_comm, "comm", 8192, nullptr, 2, nullptr, 0);        // with the Wi-Fi task
}

void loop() {
  check_button();
  delay(20);
}
