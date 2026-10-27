# data-flow-sim — ระบบ IoT จำลองสำหรับโครงงานแผงเซลล์แสงอาทิตย์อัจฉริยะ

ระบบจำลอง IoT pipeline สำหรับโครงงานเปรียบเทียบแผงเซลล์แสงอาทิตย์แบบคงที่กับระบบติดตามแสงสองแกน ต่อยอดจาก [`pvsim`](https://github.com/ItsRatcha/solar_panel_simulation) (ฟิสิกส์ของแผง 50 W + Buck + MPPT + ระบบติดตามแสง) ให้ครบทั้งระบบตามที่ออกแบบไว้ในบทที่ 2:

ESP32 สองบอร์ดส่งข้อมูลผ่าน MQTT ไปยังเซิร์ฟเวอร์ที่รัน Mosquitto, Node-RED และ InfluxDB แล้วเปิดหน้าเว็บ Dashboard ให้คนในกลุ่มเข้าดูได้ ทุกส่วนต่อกันจริงด้วยโปรโตคอลและไฟล์ตั้งค่าเดียวกับที่จะใช้ตอนติดตั้งจริง

> **Live Dashboard:** http://20.222.88.183/dashboard
> **Landing Page (Wokwi Embed):** http://20.222.88.183/index.html

## สถาปัตยกรรม

```
 pvsim (ItsRatcha/solar_panel_simulation)
   └── mount เป็น volume ใน Docker ──┐
                                     ▼
 data-flow-sim (repo นี้)
 ┌─ esp32-sim: ESP32 #1 fixed / #2 track เสมือน (Python) ──────────────┐
 ├─ pvsim batch → dataset CSV → replay (ส่งซ้ำเหมือนบอร์ดส่ง) ────────┤ MQTT 1883
 └─ Wokwi: ESP32 รันเฟิร์มแวร์ C++ จริง → HiveMQ → wokwi-bridge ─────┤ QoS 1, LWT
                                                                       ▼
 ┌──────────── Azure VM (Docker Compose) ──────────────────────────────┐
 │ Mosquitto ─1884─► Node-RED ─► InfluxDB 2.9.1 solar_raw ─► solar_1m │
 │                     ├► Dashboard 2.0  /dashboard                    │
 │                     └► Landing Page   /index.html (Wokwi embed)     │
 └─────────────────────────────────────────────────────────────────────┘
     Port 80 (iptables → 1880)                    ▲ HTTP API
   เพื่อนและอาจารย์เปิด Dashboard               MATLAB (วิเคราะห์ + ML)
```

### สองสถาปัตยกรรม repo

| repo | เจ้าของ | เนื้อหา |
|---|---|---|
| [`ItsRatcha/solar_panel_simulation`](https://github.com/ItsRatcha/solar_panel_simulation) (sim-daniel) | เพื่อน | pvsim — ฟิสิกส์ของแผง, สภาพแวดล้อม, tracking, MPPT algorithms |
| `data-flow-sim` (repo นี้) | Chutikan | IoT pipeline: ESP32 จำลอง, MQTT, Node-RED, InfluxDB, Dashboard, Wokwi firmware, MATLAB |

บน Azure VM ทั้งสอง repo ถูก clone ไว้เคียงกัน และเชื่อมด้วย Docker volume mount:
```
~/project/
├── sim-daniel/          ← pvsim (mount เป็น /app/pvsim ใน container)
└── data-flow-sim/       ← repo นี้
```

## 1. วิธีรัน

### บน Azure VM (ใช้งานอยู่)

```bash
ssh chutikan@20.222.88.183
cd ~/project/data-flow-sim
docker compose up -d --build
docker compose ps
```

### บน Docker Desktop (Windows)

```powershell
# clone ทั้งสอง repo ไว้ข้างกัน
git clone https://github.com/ItsRatcha/solar_panel_simulation sim-daniel
git clone https://github.com/6610625011/data-flow-sim
cd data-flow-sim
cp .env.example .env        # แก้รหัสผ่านตามต้องการ
docker compose up -d --build
```

### บน Raspberry Pi จริง

ใช้ Raspberry Pi 4/5 กับ Raspberry Pi OS 64 บิต ลง Docker แบบเดียวกัน รันเฉพาะส่วนเซิร์ฟเวอร์:

```bash
docker compose up -d mosquitto influxdb influx-init nodered
```

| URL | ใช้ทำอะไร | บัญชีเริ่มต้น |
|---|---|---|
| `http://<IP>/dashboard` | **หน้าเว็บสำหรับกลุ่ม** | ไม่ต้องล็อกอิน (ตั้ง `DASHBOARD_PASSWORD` ได้) |
| `http://<IP>/index.html` | **Landing Page + Wokwi Embed** | – |
| `http://<IP>:1880/` | Node-RED editor (แก้ Flow) | `admin` / `solar-admin-2026` |
| `http://<IP>:8086` | InfluxDB (Data Explorer) | `admin` / `solar-admin-2026` |
| `<IP>:1883` | MQTT สำหรับ ESP32 / MQTT Explorer | `esp32` / `esp32-sim-2026` |

หยุด: `docker compose down` (ข้อมูลยังอยู่) · ล้างข้อมูลทั้งหมด: `docker compose down -v`

## 2. CI/CD (GitHub Actions)

ทุกครั้งที่ push ไป `master` → GitHub Actions จะ SSH เข้า Azure VM แล้ว pull + rebuild + restart อัตโนมัติ

**ตั้งค่า GitHub Secrets** (Settings → Secrets and variables → Actions):

| Secret | ค่า |
|---|---|
| `VM_HOST` | IP ของ Azure VM |
| `VM_USER` | username SSH |
| `VM_SSH_KEY` | private key (ed25519) |

Workflow อยู่ที่ `.github/workflows/deploy.yml`

## 3. ปรับการจำลอง

ตั้งใน `.env` แล้วรัน `docker compose up -d esp32-sim` อีกครั้ง

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `SIM_START` | `now` | `now` = เวลาจริง หรือเวลาท้องถิ่น เช่น `2026-03-21T05:45` |
| `SIM_SPEED` | `1` | `60` = 1 วันใช้เวลา 24 นาที, `120` = 12 นาที |
| `SIM_SKY` | `partly_cloudy` | `clear`, `partly_cloudy`, `cloudy`, `overcast`, `random` |
| `SIM_MPPT` | `po` | `po` หรือ `inc` (เปลี่ยนระหว่างทดลองได้จาก Dashboard) |
| `SIM_BUFFER` | `600` | ขนาด ring buffer ของ ESP32 |
| `SIM_KEEPALIVE` | `10` | MQTT keep-alive (Broker ส่ง LWT หลัง 1.5 × ค่านี้) |
| `SIM_NODES` | `fixed,track` | บอร์ดที่จำลอง — ตั้ง `fixed` เมื่อ ESP32 #2 รันใน Wokwi |

`docker-compose.override.yml` (ถ้ามี) ถูกโหลดอัตโนมัติและไม่เข้า Git ใช้เก็บค่าเฉพาะเครื่อง

ถ้ารันเวลาจริงตอนกลางคืนจะได้กำลังเป็นศูนย์ตามธรรมชาติ ให้ใช้ `SIM_START=2026-03-21T09:00` เพื่อดูช่วงกลางวัน

## 4. ให้เพื่อนและอาจารย์เข้าดู

| วิธี | เหมาะกับ | ทำอย่างไร |
|---|---|---|
| **Azure VM (ใช้อยู่)** | ทุกคน เข้าจากไหนก็ได้ 24/7 | `http://20.222.88.183/dashboard` — VM รันตลอด ไม่ต้อง login SSH ค้างไว้ |
| **LAN วงเดียวกัน** | อยู่ห้องแลปเดียวกัน | `http://<IP>:1880/dashboard` |
| **Cloudflare Quick Tunnel** | ลิงก์สาธารณะชั่วคราว | `docker compose --profile tunnel up -d` → ดู log |

> **หมายเหตุ:** เครือข่ายมหาวิทยาลัย (TU) บล็อก port ที่ไม่ใช่ 80/443 ดังนั้น Azure VM ใช้ iptables redirect port 80 → 1880 เพื่อให้เข้าได้โดยไม่ต้องระบุ port

## 5. หน้าเว็บ Dashboard

| หน้า | มีอะไร |
|---|---|
| ภาพรวม | สถานะออนไลน์ ESP32, Label, เกจกำลังไฟฟ้า, พลังงานวันนี้, กราฟ P_out / P_pv / แสง / อุณหภูมิ |
| ไฟฟ้าและ MPPT | V_pv, I_pv, Duty, ประสิทธิภาพ Converter, ตารางค่าล่าสุด |
| ระบบติดตามแสง | มุม Az/El เทียบดวงอาทิตย์, สัญญาณ e_az/e_el, สถานะมอเตอร์ |
| ควบคุมการทดลอง | ตั้ง Label (normal, shade_25, …) และส่งคำสั่ง MPPT/tracker |
| คุณภาพข้อมูล | N_exp, N_rx, อัตราข้อมูลหาย, เวลาหน่วง, พลังงานที่ถูกต้อง |
| ส่งออกข้อมูล | ดาวน์โหลด CSV ตามช่วงเวลา (raw, 10s, 1min, 15min) |
| จำลอง | เปลี่ยนท้องฟ้า ความเร็ว ใส่เงา/ความผิดปกติ จำลอง Wi-Fi หลุด |

ภาพตัวอย่างอยู่ใน `docs/img/`

## 6. Wokwi: เฟิร์มแวร์ ESP32

| บอร์ด | โปรเจกต์ | ไฟล์ |
|---|---|---|
| ESP32 #1 Fixed | https://wokwi.com/projects/478933944216973313 | `wokwi/esp32_fixed` |
| ESP32 #2 Track (LDR 4 ตัว + สเต็ปเปอร์ 2 แกน) | https://wokwi.com/projects/478933751958391809 | `wokwi/esp32_track` |

ทั้งสองใช้โค้ดชุดเดียวกัน ต่างกันแค่ `config.h` ไฟล์ในโฟลเดอร์ตรงกับในโปรเจกต์ Wokwi ทุกไฟล์

**เส้นทางข้อมูล Wokwi:** บอร์ดส่งไปที่ `broker.hivemq.com` (prefix `tu-solar-32b21d/`) แล้ว `wokwi-bridge` ดึงเข้ามาใน Mosquitto ของเซิร์ฟเวอร์

```bash
docker compose --profile wokwi up -d --build    # เปิด wokwi-bridge
docker compose logs -f wokwi-bridge
```

ตั้ง `SIM_NODES=fixed` ใน `docker-compose.override.yml` เมื่อให้ Wokwi เป็น ESP32 #2

## 7. Dataset → ระบบจริง (replay)

```bash
# สร้าง dataset 30 วัน
docker compose --profile replay run --rm replay pvsim batch --days 30 --seed 7 --resample 1min --extended --workers 2 --out /datasets/batch_30d

# หยุด esp32-sim → ส่ง dataset → เปิด esp32-sim กลับ
docker compose stop esp32-sim
docker compose --profile replay run --rm replay esp32_sim.replay /datasets/batch_30d --speed 3600
docker compose start esp32-sim
```

## 8. ส่งออก CSV สำหรับวิเคราะห์ / Machine Learning

```
http://<IP>/api/export.csv?start=2026-03-21T06:00&stop=2026-03-21T18:30&m=all&every=1m&channel=all
```

`m` = `all`, `electrical`, `tracker`, `quality`, `events`, `sim_truth` · `every` = `raw`, `10s`, `1m`, `15m` · `channel` = `all`, `fixed`, `track`

```python
import pandas as pd
df = pd.read_csv("http://20.222.88.183/api/export.csv?start=2026-03-21T06:00&stop=2026-03-21T18:30&m=all&every=1m&channel=all",
                 parse_dates=["time_utc"])
```

## 9. MATLAB (ใช้ได้กับ MATLAB พื้นฐาน ไม่ต้องมี toolbox)

| ไฟล์ | ทำอะไร |
|---|---|
| `pv_buck_mppt.m` | จำลอง PV + Buck + MPPT เทียบ P&O กับ IncCond ซ้อนกราฟกับ pvsim |
| `analyze_pipeline.m` | ดึงข้อมูลจากระบบ → พยากรณ์ P_out 15 นาที + จำแนกสภาวะผิดปกติด้วย Random Forest |
| `rf_train.m`, `rf_predict.m` | Random Forest (regression/classification) เขียนด้วย MATLAB ล้วน |

```matlab
results = pv_buck_mppt
R = analyze_pipeline('http://20.222.88.183')       % จาก Azure VM
R = analyze_pipeline('../datasets/batch_30d')       % จากไฟล์ dataset
```

## 10. รายละเอียดทางเทคนิค

### MQTT

`solar/fixed/telemetry`, `solar/track/telemetry` (QoS 1 ทุก 1 วินาที), `solar/{node}/status` (retained + LWT), `solar/cmd` (สั่ง MPPT, tracker)

Mosquitto: port 1883 (อุปกรณ์, ต้องมีรหัสผ่าน) + port 1884 (Node-RED ภายใน)

### InfluxDB

org `solar`, bucket `solar_raw` (ทุก 1 วินาที) + `solar_1m` (Task downsample ทุก 1 นาที)

| measurement | Tag | Field |
|---|---|---|
| `electrical` | channel, mppt, label | v_pv, i_pv, p_pv, v_out, i_out, p_out, duty, t_panel, seq, lat_ms (+g, t_amb ของ fixed) |
| `tracker` | channel, mppt, label | az, el, e_az, e_el, m_az, m_el, p_mot, mode |
| `events` | channel, type | detail |
| `quality` | channel | rx_min, loss_pct, dup, lat_p50, lat_p95, e_pv_wh, e_out_wh, e_mot_wh |
| `sim_truth` | channel | p_mpp, p_pv_true, g_poa, shade_1..3, sun_az, sun_el, label_truth |

## 11. ไฟล์ในโปรเจกต์

```
data-flow-sim/
├── .github/workflows/deploy.yml   CI/CD: auto deploy เมื่อ push ไป master
├── docker-compose.yml             บริการทั้งหมด + esp32-sim + replay / wokwi-bridge
├── docker-compose.override.yml    ค่าเฉพาะเครื่อง (ไม่เข้า Git)
├── .env.example                   รหัสผ่านและค่าการจำลอง (คัดลอกเป็น .env)
├── mosquitto/                     mosquitto.conf, start.sh, bridge/
├── influxdb/                      setup.sh, downsample_1m.flux
├── nodered/                       Dockerfile, seed-credentials.js, data/
│   └── data/public/index.html     Landing page (Wokwi embed)
├── esp32_sim/                     ESP32 จำลอง: plant.py, link.py, virtual_esp32.py, replay.py, wokwi_bridge.py
├── wokwi/                         เฟิร์มแวร์ + diagram.json
│   ├── esp32_fixed/
│   └── esp32_track/
├── matlab/                        pv_buck_mppt.m, analyze_pipeline.m, rf_*.m
├── datasets/                      dataset จาก pvsim batch (ไม่เข้า Git)
└── docs/img/                      ภาพหน้าจอ
```

## 12. ปัญหาที่พบบ่อย

- **พอร์ตชน**: ปิดโปรแกรมที่ใช้พอร์ตนั้น หรือแก้ใน `docker-compose.yml`
- **ไม่มีกราฟ**: ดู `docker compose logs esp32-sim`; ถ้ากลางคืน ตั้ง `SIM_START`
- **Permission denied ที่ nodered/data**: `sudo chown -R 1000:1000 nodered/data`
- **เปลี่ยนรหัส InfluxDB ไม่มีผล**: ต้อง `docker compose down -v` แล้วขึ้นใหม่
- **เครือข่ายมหาลัยบล็อก**: ใช้ port 80 (iptables redirect) หรือ mobile hotspot

## 13. สมมติฐาน

ระดับเงา `shade_25/50/75` = ผ้าบังแสงบัง substring เดียว · สัญญาณ LDR อิ่มตัวที่ ±10° · มอเตอร์ 2 W ต่อแกน · Reference cell noise 0.5 % · ค่าเหล่านี้ปรับได้ใน `esp32_sim/plant.py` และ `wokwi/*/config.h`
