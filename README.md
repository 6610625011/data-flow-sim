# system_sim — จำลองทั้งระบบ IoT ต่อจาก pvsim

โฟลเดอร์นี้ต่อยอดจาก `pvsim` ของเพื่อน (แผง 50 W + Buck + MPPT + ระบบติดตามแสง) ให้ครบทั้งระบบตามที่ออกแบบไว้ในหัวข้อ 2.7 และ 2.9:
ESP32 สองบอร์ดส่งข้อมูลผ่าน MQTT ไปยัง Raspberry Pi ที่รัน Mosquitto, Node-RED และ InfluxDB แล้วเปิดหน้าเว็บ Dashboard ให้คนในกลุ่มเข้าดูได้
ทุกส่วนต่อกันจริงด้วยโปรโตคอลและไฟล์ตั้งค่าเดียวกับที่จะใช้ตอนติดตั้งจริง ต่างกันแค่แหล่งข้อมูลที่ตอนนี้มาจากแบบจำลองและ dataset และไม่ได้แก้ไฟล์เดิมของ `pvsim` เลย

```
 pvsim (ฟิสิกส์ของเพื่อน) ─┬─ esp32-sim: ESP32 #1 fixed / #2 track เสมือน (Python) ───────────┐
                           └─ pvsim batch → dataset CSV → replay (ส่งซ้ำเหมือนบอร์ดส่ง) ──────┤ MQTT 1883
 Wokwi: ESP32 รันเฟิร์มแวร์ C++ จริง → broker.hivemq.com → bridge ของ Mosquitto ─────────────┤ QoS 1, LWT
                                                                                              ▼
 ┌──────────── Raspberry Pi จำลอง (Docker: Windows / VM / Pi จริง ใช้ไฟล์เดียวกัน) ────────────┐
 │ Mosquitto ─1884─► Node-RED (กลุ่ม ก ข ค ง) ─► InfluxDB 2.9.1 solar_raw ─Task─► solar_1m     │
 │                     └► Dashboard 2.0  /dashboard      └► CSV /api/export.csv               │
 └─────────────────────────────────────────────────────────────────────────────────────────┘
          ▲ Tailscale (เฉพาะกลุ่ม) / Cloudflare Tunnel (ลิงก์ชั่วคราว)      ▲ HTTP API
   เพื่อนและอาจารย์เปิด Dashboard ในเบราว์เซอร์                     MATLAB (วิเคราะห์ + ML)
```

## สรุปสั้น ๆ

- **ใช้ VM เป็นเซิร์ฟเวอร์จำลองได้** ทุกบริการของ Raspberry Pi อยู่ใน `docker-compose.yml` ไฟล์เดียว รันได้บน Docker Desktop (Windows), ใน VM หรือบน Raspberry Pi 4/5 จริงโดยไม่ต้องแก้ไฟล์
- **ทดสอบบนเครื่องของผู้ใช้แล้ว** (Windows 11 + Docker Desktop, 26 ต.ค. 2026): ESP32 จำลองส่งข้อมูลเข้า Node-RED/InfluxDB ข้อมูลหาย 0 % และเปิด Dashboard ผ่าน Tailscale ได้
- **Wokwi** รันเฟิร์มแวร์ ESP32 จริง ต่อ Wi-Fi ซิงก์เวลา SNTP และส่ง MQTT QoS 1 ได้ PUBACK ครบ (โปรเจกต์ Public ในบัญชีผู้ใช้ ดูหัวข้อ 8)
- **MATLAB** (เฉพาะ MATLAB พื้นฐาน ไม่ต้องมี toolbox): จำลอง PV + Buck + MPPT เทียบกับ pvsim และดึงข้อมูลจากระบบผ่าน API มาวิเคราะห์ + เทรน Random Forest (หัวข้อ 10)

## 1. วิธีรัน (Docker Desktop บน Windows)

1. ติดตั้ง [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) แบบ WSL2 เปิดโปรแกรมไว้ และติ๊ก **Start Docker Desktop when you sign in** (Settings → General) เพื่อให้ระบบขึ้นเองหลังเปิดเครื่อง
2. (ไม่บังคับ) คัดลอก `.env.example` เป็น `.env` แล้วเปลี่ยนรหัสผ่าน ถ้าไม่มี `.env` จะใช้ค่าเริ่มต้นในตารางด้านล่าง
3. เปิด PowerShell แล้วรัน

```powershell
cd "C:\Users\chuti\Computer Engineering\project\sim-daniel\system_sim"
docker compose up -d --build        # ครั้งแรกดาวน์โหลดประมาณ 1.5 GB
docker compose ps                   # ทุกบริการควรเป็น running (influx-init จะ exited 0)
docker compose logs -f esp32-sim    # ดู ESP32 จำลองส่งข้อมูล (Ctrl+C เพื่อออก)
```

หลังแก้ไฟล์ในโฟลเดอร์นี้ ให้รัน `docker compose up -d --build` ซ้ำ ถ้าแก้ `nodered/data/flows.json` หรือ `mosquitto/` ให้ตามด้วย `docker compose restart nodered mosquitto`

| เปิดที่ | ใช้ทำอะไร | บัญชีเริ่มต้น |
|---|---|---|
| http://localhost:1880/dashboard | **หน้าเว็บสำหรับกลุ่ม** | ไม่ต้องล็อกอิน (ตั้ง `DASHBOARD_PASSWORD` ได้) |
| http://localhost:1880/ | Node-RED editor (แก้ Flow) | `admin` / `solar-admin-2026` |
| http://localhost:8086 | InfluxDB (Data Explorer) | `admin` / `solar-admin-2026` |
| `localhost:1883` | MQTT สำหรับ ESP32 / MQTT Explorer | `esp32` / `esp32-sim-2026` |
| ฟอร์มบน Dashboard | PIN สำหรับสั่งงาน/ตั้ง Label | `2468` (`CONTROL_PIN`) |
| http://localhost:1880/api/health | สถานะรวมแบบ JSON (บอร์ด, seq, ข้อมูลหาย, พลังงาน) | – |

หยุด: `docker compose down` (ข้อมูลยังอยู่) · ล้างข้อมูลทั้งหมดเริ่มใหม่: `docker compose down -v`

### ปรับการจำลอง

ตั้งใน `.env` แล้วรัน `docker compose up -d esp32-sim` อีกครั้ง

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `SIM_START` | `now` | `now` = เวลาจริง หรือเวลาท้องถิ่น เช่น `2026-03-21T05:45` |
| `SIM_SPEED` | `1` | `60` = 1 วันใช้เวลา 24 นาที, `120` = 12 นาที (ถ้าเครื่องช้ากว่านี้ ตัวจำลองจะรอแทนการทิ้งข้อมูล) |
| `SIM_SKY` | `partly_cloudy` | `clear`, `partly_cloudy`, `cloudy`, `overcast`, `random` |
| `SIM_MPPT` | `po` | `po` หรือ `inc` (เปลี่ยนระหว่างทดลองได้จากหน้า "ควบคุมการทดลอง") |
| `SIM_BUFFER` | `600` | ขนาด ring buffer ของ ESP32 (สมการ 2.7-1: 10 นาทีที่คาบ 1 วินาที) |
| `SIM_KEEPALIVE` | `10` | MQTT keep-alive (Broker ส่ง LWT หลัง 1.5 × ค่านี้) |
| `SIM_NODES` | `fixed,track` | บอร์ดที่ตัวจำลองเล่น ตั้ง `fixed` เมื่อให้ Wokwi หรือบอร์ดจริงเป็น ESP32 #2 |

`docker-compose.override.yml` (ถ้ามี) ถูกโหลดอัตโนมัติและไม่เข้า Git ใช้เก็บค่าเฉพาะเครื่อง เช่นในเครื่องผู้ใช้ตั้ง `SIM_NODES: fixed` เพราะ ESP32 #2 รันใน Wokwi ลบไฟล์นี้ทิ้งเมื่ออยากให้ตัวจำลองเล่นทั้งสองบอร์ด

ถ้ารันเวลาจริงตอนกลางคืนจะได้กำลังเป็นศูนย์ตามธรรมชาติ ให้ใช้ `SIM_START=2026-03-21T09:00` เพื่อดูช่วงกลางวัน

## 2. ใช้ VM เป็น "Raspberry Pi จำลอง"

1. สร้าง VM (RAM 2–4 GB, ดิสก์ 20 GB) แล้วติดตั้ง Raspberry Pi Desktop สำหรับ PC (Debian 13) หรือ Debian 13 / Ubuntu Server 24.04
2. ตั้ง Network ของ VM เป็น **Bridged Adapter** เพื่อให้ VM ได้ IP ในวงเดียวกับเครื่องเพื่อน
3. ใน VM:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # ออกจากระบบแล้วเข้าใหม่หนึ่งครั้ง
# คัดลอกโฟลเดอร์ sim-daniel ทั้งโฟลเดอร์มาไว้ใน VM (shared folder, USB, git หรือ scp)
cd sim-daniel/system_sim
docker compose up -d --build
hostname -I                        # IP ที่เพื่อนใช้เปิด http://<IP>:1880/dashboard
```

การจำลอง Pi ด้วย QEMU (จำลอง CPU ARM) ทำได้แต่ช้ามากและไม่จำเป็น เพราะงานนี้ทดสอบซอฟต์แวร์ ไม่ได้ทดสอบ CPU ของ Pi

## 3. ย้ายไป Raspberry Pi จริง

ใช้ Raspberry Pi 4 หรือ 5 กับ Raspberry Pi OS 64 บิต (InfluxDB 2.x และ Node-RED 5 ต้องใช้ 64 บิต) ลง Docker แบบเดียวกับข้อ 2 แล้วรันเฉพาะส่วนเซิร์ฟเวอร์

```bash
docker compose up -d mosquitto influxdb influx-init nodered
```

ที่บอร์ด ESP32 จริงแก้ `config.h` ของเฟิร์มแวร์ (หัวข้อ 8): `WIFI_SSID`/`WIFI_PASS` ของวงจริง, `MQTT_URI "mqtt://<IP ของ Pi>:1883"`, `MQTT_USER`/`MQTT_PASS` ตาม `.env`, `TOPIC_PREFIX ""`, `NTP_SERVER` เป็น IP ของ Pi (ลง `chrony` บน Pi แล้วเปิด `allow 192.168.0.0/16` ตามหัวข้อ 2.7.1) และเปลี่ยน `plant_measure()` ใน `plant_sim.h` เป็นไดรเวอร์ INA226
แท็บ "เฉพาะการจำลอง" ใน Node-RED ลบทิ้งหรือปล่อยไว้ก็ได้ เพราะบอร์ดจริงไม่ส่ง `sim/*`

## 4. ให้เพื่อนในกลุ่มและอาจารย์เข้าดู

| วิธี | เหมาะกับ | ทำอย่างไร |
|---|---|---|
| **Tailscale** (ใช้อยู่) | เฉพาะคนในกลุ่ม อยู่คนละที่ได้ | ลง Tailscale บนเครื่องที่รัน Docker แล้วรัน `tailscale serve --bg 1880` ได้ลิงก์ `https://<เครื่อง>.<tailnet>.ts.net/dashboard` ชวนเพื่อน/อาจารย์ที่ login.tailscale.com/admin/users → Invite users (แผนฟรีได้ 6 ผู้ใช้) ให้เขาลง Tailscale แล้วเปิดลิงก์ |
| **LAN วงเดียวกัน** | อยู่ห้องแลปเดียวกัน | `http://<IP ของเครื่อง>:1880/dashboard` · Windows ดู IP ด้วย `ipconfig` และอนุญาต Docker Desktop ใน Windows Firewall · Wi-Fi มหาวิทยาลัยมักกันเครื่องคุยกันเอง ถ้าเปิดไม่ได้ให้ใช้ Tailscale |
| **Cloudflare Quick Tunnel** | ลิงก์สาธารณะชั่วคราว เช่น ส่งอาจารย์ที่ไม่ได้ลง Tailscale | ตั้ง `DASHBOARD_PASSWORD` ใน `.env` ก่อน แล้วรัน `docker compose --profile tunnel up -d` และ `docker compose logs cloudflared` เพื่อดูลิงก์ `https://….trycloudflare.com` แล้วต่อท้ายด้วย `/dashboard` · ลิงก์เปลี่ยนทุกครั้งที่รันใหม่ |

**ถ้าปิดเครื่องที่รัน Docker:** ลิงก์จะเปิดไม่ได้สำหรับทุกคน และไม่มีข้อมูลใหม่ช่วงนั้น ข้อมูลเก่าใน InfluxDB ไม่หาย เปิดเครื่องอีกครั้ง Docker Desktop จะเปิด container เองตาม `restart: unless-stopped` และ `tailscale serve --bg` จำค่าไว้ ลิงก์เดิมจึงใช้ต่อได้ หน้า "คุณภาพข้อมูล" จะแสดงช่วงที่ขาดหายไว้ ถ้าต้องการให้เปิดตลอด 24 ชม. ให้ย้ายชุด Docker เดียวกันไปไว้บน Raspberry Pi ของโครงงานหรือ VM บนคลาวด์ แล้วลง Tailscale ที่เครื่องนั้นแทน

สิทธิ์การใช้งาน: คนที่เปิด Dashboard ดูได้ทุกหน้า แต่การส่งคำสั่ง/ตั้ง Label ต้องใส่ PIN ส่วน editor ของ Node-RED และ InfluxDB ต้องล็อกอินแยก
ข้อจำกัด: `DASHBOARD_PASSWORD` ป้องกันหน้าเว็บและ `/api` แต่ช่อง socket ของ Dashboard 2.0 ไม่ได้อยู่หลังรหัสผ่านนี้ ถ้าเปิดลิงก์สาธารณะนาน ๆ ควรใช้ Tailscale แทน

## 5. หน้าเว็บ Dashboard

| หน้า | มีอะไร |
|---|---|
| ภาพรวม | สถานะออนไลน์ของ ESP32 ทั้งสอง, Label ปัจจุบัน, เกจกำลังไฟฟ้า, พลังงานวันนี้และส่วนเพิ่มของระบบติดตาม, กราฟ P_out / P_pv / ความเข้มแสง / อุณหภูมิ |
| ไฟฟ้าและ MPPT | V_pv, I_pv, Duty, ประสิทธิภาพ Converter, ตารางค่าล่าสุดทุกฟิลด์ของตาราง 2.7-1 |
| ระบบติดตามแสง | มุม Az/El ของแผงเทียบตำแหน่งดวงอาทิตย์จริงจากแบบจำลอง, สัญญาณ e_az/e_el, สถานะมอเตอร์และโหมด |
| ควบคุมการทดลอง | ตั้ง Label (normal, shade_25, …) และส่ง `solar/cmd` (เลือก MPPT, start/stop, auto/home/park) |
| คุณภาพข้อมูล | ตัวชี้วัดหัวข้อ 2.7.6: N_exp, N_rx, อัตราข้อมูลหาย, ข้อความซ้ำ, เวลาหน่วงมัธยฐาน/P95, เวลาที่ขาดข้อมูล, พลังงานที่ถูกต้อง + ตารางเหตุการณ์ |
| ส่งออกข้อมูล | ดาวน์โหลด CSV ตามช่วงเวลา (raw, 10 s, 1 min, 15 min) และสร้าง `solar_1m` ย้อนหลัง |
| จำลอง | ใช้กับ esp32-sim เท่านั้น: เปลี่ยนท้องฟ้า ความเร็วเวลา ใส่เงาบัง/ความผิดปกติ ทำให้ Wi-Fi หลุด รีบูตบอร์ด ส่งข้อความซ้ำ |

ภาพตัวอย่างอยู่ใน `docs/img/`

## 6. รายละเอียดการออกแบบที่ทำตามบทที่ 2

**MQTT (ตาราง 2.7-1)** `solar/fixed/telemetry`, `solar/track/telemetry` (QoS 1 ทุก 1 วินาที, `ts` เป็น Unix ms UTC, `seq` เพิ่มทีละ 1), `solar/{node}/status` (`online` แบบ retained และ `offline` เป็น LWT), `solar/cmd` (`{"target":"all|fixed|track","mppt":"po|inc","run":"start|stop","tracker":"auto|home|park"}`)
Mosquitto มี 2 พอร์ต: 1883 สำหรับอุปกรณ์ (ต้องมีรหัสผ่าน) และ 1884 สำหรับ Node-RED ภายใน Pi เท่านั้น Node-RED ใช้ persistent session (clean session = false) Broker จึงเก็บข้อความ QoS 1 ไว้ให้ระหว่าง Node-RED รีสตาร์ต

**Node-RED (หัวข้อ 2.7.4)** แท็บ "ระบบหลัก" แบ่งเป็น 4 กลุ่มตามรูปที่ 2.7-3 (ภาพหน้าจอจริงอยู่ที่ `docs/img/nodered_flow_groups.png` ใช้แทนรูปเดิมได้)

| กลุ่ม | ทำอะไร |
|---|---|
| ก | รับ `solar/+/telemetry` → ตรวจช่วงค่า (0 ≤ V_pv ≤ 23 V, I_pv ≥ 0, …) → ตรวจ seq (หาย / ซ้ำ / มาช้า / รีบูต) → เติม Tag `channel`, `mppt`, `label` → แยกข้อความของ ESP32 #2 เป็น 2 Point → รวมเขียน InfluxDB ทุก 1 วินาที ค่าที่ไม่ผ่านไปเอาต์พุตที่ 2 เป็นเหตุการณ์ผิดปกติ |
| ข | สถานะออนไลน์จาก retained + LWT, แจ้งเตือนบนหน้าเว็บ, ตรวจข้อมูลค้างเกิน 5 วินาที, บันทึกเหตุการณ์ลง InfluxDB |
| ค | ฟอร์มตั้ง Label และส่ง `solar/cmd` (ต้องใช้ PIN) |
| ง | พลังงานแบบสี่เหลี่ยมคางหมูเฉพาะช่วงที่ Δt ≤ 3·T_pub (สมการ 2.7-5; T_pub คือมัธยฐานของ Δt ล่าสุด จึงใช้ได้ทั้งข้อมูลทุก 1 วินาทีและ dataset ทุก 1 นาที), ส่วนเพิ่มพลังงานสุทธิหลังหักมอเตอร์, อัตราข้อมูลหาย (2.7-3), เวลาหน่วง (2.7-4) และสรุปทุก 1 นาทีลง measurement `quality` |

**InfluxDB (หัวข้อ 2.7.5)** org `solar`, bucket `solar_raw` (ทุก 1 วินาที) และ `solar_1m` (Task `downsample_1m` ทุก 1 นาที: ค่าเฉลี่ย + พลังงานต่อนาที)

| measurement | Tag | Field |
|---|---|---|
| `electrical` | channel, mppt, label | v_pv, i_pv, p_pv, v_out, i_out, p_out, duty, t_panel, seq, lat_ms (+ g, t_amb ของ fixed) |
| `tracker` | channel, mppt, label | az, el, e_az, e_el, m_az, m_el, p_mot, mode |
| `events` | channel, type | detail (ค่าผิดช่วง, seq ขาด, รีบูต, สถานะ, คำสั่ง, Label) |
| `quality` | channel | rx_min, loss_pct, dup, lat_p50, lat_p95, e_pv_wh, e_out_wh, e_mot_wh, missing_s |
| `sim_truth` | channel | ค่าจริงจากแบบจำลอง: p_mpp, p_pv_true, g_poa, shade_1..3, sun_az, sun_el, label_truth (เฉพาะการจำลอง) |
| `energy` (ใน solar_1m) | channel, mppt, label | p_pv_wh, p_out_wh, p_mot_wh ต่อนาที |

Point ใช้ `ts` ของ ESP32 เป็นเวลา ข้อความซ้ำจาก QoS 1 จึงเขียนทับจุดเดิม ไม่เกิดข้อมูลซ้ำ
Task ของ InfluxDB สรุปเฉพาะนาทีที่เพิ่งผ่านไปตามนาฬิกาจริง ถ้าจำลองแบบเร่งเวลา ย้อนหลัง หรือเล่น dataset ให้กดปุ่ม "สร้างข้อมูลสรุปรายนาที" ในหน้าส่งออก (หรือเรียก `/api/downsample?start=…&stop=…`)

## 7. ส่งออก CSV สำหรับวิเคราะห์ / Machine Learning

ใช้หน้า "ส่งออกข้อมูล" หรือเรียกลิงก์ตรง (เวลาเป็นเวลาท้องถิ่น +07:00)

```
http://<ip>:1880/api/export.csv?start=2026-03-21T06:00&stop=2026-03-21T18:30&m=all&every=1m&channel=all
```

`m` = `all` (ไฟฟ้า + ระบบติดตามรวมแถวตามเวลา), `electrical`, `tracker`, `quality`, `events`, `sim_truth` · `every` = `raw`, `10s`, `1m`, `15m` · `channel` = `all`, `fixed`, `track`

```python
import pandas as pd
url = "http://localhost:1880/api/export.csv?start=2026-03-21T06:00&stop=2026-03-21T18:30&m=all&every=1m&channel=all"
df = pd.read_csv(url, parse_dates=["time_utc"])
df.groupby("label")[["p_out"]].describe()
```

## 8. Wokwi: เฟิร์มแวร์ ESP32 จริงต่อเข้าระบบ

| บอร์ด | โปรเจกต์ (Public ในบัญชีผู้ใช้) | ไฟล์ |
|---|---|---|
| ESP32 #2 Track: LDR 4 ตัว, สเต็ปเปอร์ 2 แกนผ่าน A4988, ปุ่มจำลอง Wi-Fi หลุด | https://wokwi.com/projects/478933751958391809 | `wokwi/esp32_track` |
| ESP32 #1 Fixed: ตัวต้านทานปรับค่าแทน Reference cell, NTC แทนเซนเซอร์อุณหภูมิ | https://wokwi.com/projects/478933944216973313 | `wokwi/esp32_fixed` |

ทั้งสองใช้โค้ดชุดเดียวกัน ต่างกันแค่ `config.h` (เปิดโมดูลระบบติดตามด้วยค่ากำหนดตอนคอมไพล์ ตามหัวข้อ 2.7.1) ไฟล์ในโฟลเดอร์ตรงกับในโปรเจกต์ Wokwi ทุกไฟล์ (ตรวจด้วย SHA-256)

- โครงเฟิร์มแวร์ตามหัวข้อ 2.9.2: `task_control` (MPPT ทุก 100 ms ใช้ `mppt.c` ของ pvsim) และ `task_tracker` อยู่ Core 1, `task_comm` (ring buffer + ESP-MQTT QoS 1 + LWT) อยู่ Core 0 คุยกันผ่านคิว `q_tel` / `q_cmd`
- ระบบติดตามแบบ Hybrid: เป้าหมายหยาบจากตำแหน่งดวงอาทิตย์ (`sun.h` สมการเดียวกับ pvsim) + ปรับละเอียดด้วย LDR แบบมี Deadband/Hysteresis จำกัดไม่เกิน ±10° + จอดแผงตอนกลางคืน
- Wokwi ไม่มีชิ้นส่วนแผงโซลาร์และ INA226 จึงใช้ `plant_sim.h` คำนวณค่าที่ INA226 จะอ่านได้ (หมุนตัวต้านทานปรับค่าเพื่อเปลี่ยนความเข้มแสง) ตอนใช้บอร์ดจริงเปลี่ยนแค่ฟังก์ชัน `plant_measure()` เป็นไดรเวอร์ INA226
- คอมไพล์ผ่านทั้งสองแบบด้วย Arduino-ESP32 core 3.3.12 และใน Wokwi; ผลที่รันจริงใน Wokwi: ต่อ Wi-Fi (Wokwi-GUEST) → SNTP synced → MQTT connected → ส่ง QoS 1 ได้ PUBACK ครบ ไม่มีข้อมูลหาย ความเร็วจำลอง 95–100 % เมื่อแผงเบราว์เซอร์เปิดอยู่
- client id ของ MQTT ใช้ MAC ของบอร์ด (`esp32-track-xxxxxxxxxxxx`) เพราะบน broker สาธารณะ client id ที่ซ้ำกันจะเตะอีกฝ่ายหลุด

**เส้นทางข้อมูล:** Wokwi แบบฟรีออกได้แค่อินเทอร์เน็ต (Private IoT Gateway ต่อ `host.wokwi.internal` ได้เฉพาะแผนเสียเงิน) บอร์ดจึงส่งไปที่ `broker.hivemq.com` โดยมี `TOPIC_PREFIX "tu-solar-32b21d/"` นำหน้า แล้วบริการ `wokwi-bridge` (`esp32_sim/wokwi_bridge.py`) ดึงข้อความเข้ามาใน Mosquitto ของ Pi จำลอง และส่ง `solar/cmd` กลับออกไปให้บอร์ดใน Wokwi

```powershell
docker compose --profile wokwi up -d --build    # เปิด wokwi-bridge (prefix ตั้งได้ด้วย WOKWI_PREFIX ใน .env)
docker compose logs -f wokwi-bridge              # ควรเห็น remote ... connected และจำนวนข้อความ in เพิ่มขึ้น
```

1. ให้ตัวจำลอง Python เล่นแค่บอร์ดที่ไม่ได้อยู่ใน Wokwi (`SIM_NODES=fixed` ผ่าน `docker-compose.override.yml`) ไม่ให้ส่งข้อมูลชนกัน
2. กด ▶ ในโปรเจกต์ Wokwi โดยเปิดแผง/แท็บเบราว์เซอร์นั้นค้างไว้ (แท็บที่ถูกซ่อนจะจำลองช้าลงเหลือราว 3 %)
3. ทดสอบว่าเครื่องออกพอร์ต 1883 ได้และบอร์ดกำลังส่ง: `docker run --rm eclipse-mosquitto:2 mosquitto_sub -h broker.hivemq.com -t "tu-solar-32b21d/#" -C 3 -W 15 -v`

ทำไมไม่ใช้ bridge ในตัว Mosquitto: ตอนทดสอบบนเครื่องจริง broker.hivemq.com ตัดการเชื่อมต่อของ bridge ภายใน 1 วินาทีทุกครั้ง (ลองปิด `notifications` แล้วก็ยังเป็น) ขณะที่ client ธรรมดาเชื่อมต่อได้ปกติ จึงใช้ client ตัวเล็กนี้แทน ไฟล์ `mosquitto/bridge/bridge-wokwi.conf.example` ยังเก็บไว้เผื่อใช้กับ broker อื่น
ตอนทดสอบยังพบว่า DNS ของ Wokwi หา `broker.hivemq.com` ไม่เจอเป็นช่วง ๆ ระหว่างนั้นเฟิร์มแวร์เก็บข้อมูลไว้ใน ring buffer แล้วส่งตามลำดับเมื่อต่อได้อีกครั้ง ตามที่ออกแบบในหัวข้อ 2.7.3

broker สาธารณะใครก็อ่านได้ ใช้กับข้อมูลจำลองเท่านั้น

## 9. Dataset → ระบบจริง (replay)

ยังไม่มีชุดทดลองจริง ข้อมูลที่ไหลในระบบจึงมาจาก 2 แหล่ง: ESP32 จำลองแบบเวลาจริง (หัวข้อ 1) และ dataset ที่สร้างด้วย `pvsim batch` ของเพื่อน ซึ่งสุ่มวัน ท้องฟ้า อุณหภูมิ โหลด และใส่ความผิดปกติพร้อม Label (partial_shading_lvl1–3, soiling, bypass_diode_short, sensor_offset, open_circuit, tracker_stuck)

```powershell
# 1) สร้าง dataset 30 วัน ข้อมูลทุก 1 นาที (ใช้ image ของ esp32-sim ไม่ต้องลง Python)
docker compose --profile replay run --rm replay pvsim batch --days 30 --seed 7 --resample 1min --extended --workers 2 --out /datasets/batch_30d
# 2) หยุดตัวจำลองเวลาจริงก่อน ไม่ให้ seq ชนกัน
docker compose stop esp32-sim
# 3) ส่ง dataset ผ่าน MQTT → Node-RED → InfluxDB เหมือนบอร์ดส่ง (1 นาทีของ dataset = 1/60 วินาที)
docker compose --profile replay run --rm replay esp32_sim.replay /datasets/batch_30d --speed 3600
# 4) กลับไปใช้ตัวจำลองเวลาจริง
docker compose start esp32-sim
```

`esp32_sim/replay.py` แปลงแต่ละแถวเป็นข้อความ JSON ตามตาราง 2.7-1 (QoS 1, LWT, seq, ring buffer เดียวกับตัวจำลอง) ใช้ `timestamp` ของแถวเป็น `ts` ข้อมูลจึงอยู่ในวันเดิมของ dataset และแนบ `label` ของแถวมาด้วย Node-RED ใช้ค่านี้เป็น Tag แทน Label ของการทดลอง (บอร์ดจริงไม่ส่งฟิลด์นี้) ฟิลด์ที่ dataset ไม่มีถูกคำนวณเพิ่ม: e_az/e_el จากตำแหน่งดวงอาทิตย์, m_az/m_el และ p_mot จากการเปลี่ยนมุม, mode จาก Label/มุมดวงอาทิตย์
`pvsim batch` สุ่มวันที่ จึงอาจได้วันที่ซ้ำ replay จะข้ามไฟล์ที่วันที่ซ้ำเพื่อไม่ให้ข้อมูลสองวันทับกันในฐานข้อมูล

ทดสอบแล้ว: replay 1 วัน (1,380 ข้อความ) ได้ PUBACK ครบ ข้อมูลหาย 0 % และพลังงานที่ Node-RED คำนวณตรงกับที่คำนวณจากไฟล์ (35.15 / 44.65 Wh)

## 10. MATLAB (ใช้ได้กับ MATLAB พื้นฐาน R2026b ไม่ต้องมี toolbox)

เปิด MATLAB แล้ว `cd` ไปที่ `system_sim\matlab`

| ไฟล์ | ทำอะไร |
|---|---|
| `pv_buck_mppt.m` | (ก) แผง 50 W (single-diode พารามิเตอร์เดียวกับ pvsim) + Buck แบบ averaged ที่มีการสูญเสียเหมือน pvsim + MPPT ที่ port มาจาก `firmware_ref/mppt.c` ทีละบรรทัด เทียบ P&O กับ IncCond ภายใต้โปรไฟล์แสงเดียวกัน (ขอบเขตข้อ 4) ซ้อนกราฟกับผลของ pvsim (`pvsim_reference_mppt.csv`) และตรวจ ripple ของ Buck ที่ 40 kHz ด้วยการจำลองระดับสวิตชิง |
| `analyze_pipeline.m` | (ข) ดึงข้อมูลจากระบบผ่าน `/api/export.csv` (หรืออ่านโฟลเดอร์ dataset ตรง ๆ) → พลังงานรายวันและส่วนเพิ่มของระบบติดตาม → พยากรณ์ P_out ล่วงหน้า 15 นาที (Persistence, Linear Regression, Random Forest) → จำแนกสภาวะผิดปกติด้วย Random Forest พร้อม Precision/Recall/F1/Confusion matrix (ขอบเขตข้อ 9–11) |
| `rf_train.m`, `rf_predict.m` | Random Forest (regression/classification, มีแบบ class-balanced) เขียนด้วย MATLAB ล้วน แทน Statistics and Machine Learning Toolbox ที่เครื่องไม่มี |

```matlab
results = pv_buck_mppt                                       % (ก)
R = analyze_pipeline('http://localhost:1880')                % (ข) จากระบบบนเครื่องนี้
R = analyze_pipeline('https://chutikan.taildc653c.ts.net')   % (ข) จากระบบผ่าน Tailscale
R = analyze_pipeline('../datasets/batch_30d')                % (ข) จากไฟล์ dataset โดยตรง
```

ผลทั้งหมด (PNG + CSV) อยู่ใน `matlab/out/`

**ผลของ (ก)** โปรไฟล์ 100 วินาที: 1000 → 500 → 1000 W/m² แล้วลดลงเป็น 300 W/m², T_amb 30 °C

| อัลกอริทึม | แบบจำลอง | η_MPPT ทั้งช่วง | η ช่วงคงตัว | การแกว่งรอบ MPP | เวลาตอบสนอง (ขั้นที่ 20 s / 40 s) |
|---|---|---|---|---|---|
| P&O | MATLAB | 92.8 % | 99.98 % | 0.031 % | 4.0 / 3.5 s |
| P&O | pvsim | 92.6 % | 99.98 % | 0.033 % | 4.0 / 3.9 s |
| IncCond | MATLAB | 92.6 % | 99.98 % | 0.031 % | 3.8 / 3.9 s |
| IncCond | pvsim | 92.8 % | 99.98 % | 0.031 % | 4.0 / 3.7 s |

η ทั้งช่วงต่ำกว่า 100 % ส่วนใหญ่เพราะช่วงเริ่มต้น (Duty เริ่ม 0.3 ขยับทีละ 0.005 ทุก 0.1 s) และขั้นบันไดของแสง ที่ขั้นคงที่ 0.005 ทั้งสองวิธีให้ผลใกล้กันมาก ความแตกต่างจะชัดขึ้นเมื่อใช้ step แบบแปรผัน (`MPPT_PO_VARIABLE` / `MPPT_INCCOND_VARIABLE` ใน mppt.c) หรือแสงเปลี่ยนเร็ว
Ripple ของ Buck ที่ D = 0.72, V_in = 16 V: ΔI_L = 0.374 A จากการจำลองสวิตชิง เทียบสูตร 0.377 A, V_out 11.13 V ตรงกับแบบจำลอง averaged

## 11. ผลทดสอบที่รันแล้ว (ใช้เป็นแนวทางบทที่ 3)

ทดสอบบน Docker ในเครื่องเดียว (เวลาหน่วงบน Wi-Fi จริงจะสูงกว่านี้)

| สถานการณ์ | ผลที่ได้ |
|---|---|
| เวลาจริง 2 บอร์ด ทุก 1 วินาที | ข้อมูลเข้า InfluxDB ครบ 3 Point/วินาที, τ มัธยฐาน ≈ 45 ms, P95 ≈ 49 ms, อัตราข้อมูลหาย 0 % |
| Wi-Fi ของ ESP32 #2 หลุด 40 วินาที | Broker ตัดการเชื่อมต่อเพราะเกิน keep-alive ที่วินาทีที่ 15 และส่ง LWT `offline`; ต่อใหม่แล้วส่งข้อมูลค้าง 41 ระเบียนตามลำดับพร้อม `ts` เดิม; อัตราข้อมูลหาย 0 % |
| Wi-Fi หลุดนานเกิน ring buffer (900 ระเบียน > 600) | ระเบียนเก่าสุดหาย 311 ระเบียน, Node-RED ตรวจเจอ seq ขาด 1 ช่วง, รายงานอัตราข้อมูลหายและเวลาที่ขาด 311 วินาทีแยกจากพลังงาน |
| ส่งข้อความซ้ำ 5 ข้อความ (จำลอง PUBACK หาย) | นับเป็นข้อความซ้ำ 5, จำนวน Point ในฐานข้อมูลเท่ากับจำนวน seq ที่ไม่ซ้ำ (เขียนทับจุดเดิม) |
| รีบูต ESP32 #1 | seq เริ่มนับใหม่ ระบบแยกรอบการเปิดเครื่อง (boot = 2) ไม่นับเป็นข้อมูลหาย |
| เร่งเวลา 120× | 240 ข้อความ/วินาที Node-RED ใช้ CPU ราว 8–20 % |
| ทั้งวัน 21 มี.ค. 2026 ฟ้าใส (เร่งเวลา) | ดูตารางด้านล่าง: 71,821 ข้อความต่อบอร์ด ข้อมูลหาย 0 %, ไม่มีข้อความซ้ำ |
| เครื่องผู้ใช้ (Windows + Docker Desktop) | บอร์ดจำลองออนไลน์ ข้อมูลหาย 0 %, เปิด Dashboard ผ่าน Tailscale ได้ |

**พลังงานทั้งวัน 21 มี.ค. 2026 ฟ้าใส** (คำนวณจากข้อมูลที่ผ่าน MQTT → Node-RED → InfluxDB เทียบกับ pvsim ที่คำนวณตรง ๆ โดยไม่ผ่านระบบ)

| | ผ่านระบบ IoT | pvsim โดยตรง |
|---|---|---|
| Fixed: E_pv / E_out | 300.85 / 286.90 Wh | 300.83 / 286.90 Wh |
| Tracking: E_pv / E_out | 368.18 / 352.52 Wh | 368.18 / 352.52 Wh |
| พลังงานมอเตอร์ระบบติดตาม | 1.10 Wh (มอเตอร์อย่างเดียว) | 4.46 Wh (มอเตอร์ + ไฟเลี้ยงวงจร 0.3 W) |
| ส่วนเพิ่มพลังงานของ Tracking | +22.9 % (หลังหักมอเตอร์ +22.5 %) | หลังหักมอเตอร์และไฟเลี้ยง +21.3 % |
| ประสิทธิภาพ MPPT (∫P_pv / ∫P_mpp) | 99.97 % | 99.96–99.97 % |

ข้อมูลที่ผ่านระบบให้พลังงานตรงกับแบบจำลองต่างกันไม่ถึง 0.01 % จึงยืนยันได้ว่าขั้นตอนส่ง ตรวจ เก็บ และสรุปข้อมูลไม่ทำให้ผลการเปรียบเทียบ Fixed/Tracking คลาดเคลื่อน

**ปัญหาที่พบและแก้แล้วระหว่างทดสอบ**
- ตอนเร่งเวลาบนเครื่องที่ CPU ไม่พอ ring buffer ของตัวจำลองล้นและข้อมูลหายราว 1 % ซึ่งเป็นผลจากเครื่อง ไม่ใช่จากเครือข่ายที่จำลอง แก้ให้ตัวจำลองหยุดรอ (backpressure) แทนการทิ้งข้อมูล ส่วนการจำลอง Wi-Fi หลุดยังใช้ ring buffer ตามเดิม
- พลังงานรายนาทีใน measurement `quality` ขาดไป 1/60 เพราะช่วงคร่อมรอยต่อนาทีไม่ถูกนับ แก้ลำดับการปิดนาทีแล้ว ผลรวมทุกนาทีเท่ากับพลังงานทั้งวัน
- bridge ของ Mosquitto ไปยัง broker สาธารณะถูกตัดการเชื่อมต่อทันทีบนเครื่องจริง เปลี่ยนไปใช้บริการ `wokwi-bridge` (Python) ที่ทดสอบแล้วว่าส่งข้อความและสถานะเข้า Node-RED ได้
- เฟิร์มแวร์: ตัวนับ `inflight` ถูกแก้จาก 2 task พร้อมกัน เปลี่ยนเป็น atomic และ client id ไม่ซ้ำกันด้วย MAC

## 12. ไฟล์ในโฟลเดอร์

```
system_sim/
├── docker-compose.yml        บริการทั้งหมดของ Pi จำลอง + esp32-sim + replay / wokwi-bridge / cloudflared (ไม่บังคับ)
├── .env.example              รหัสผ่านและค่าการจำลอง (คัดลอกเป็น .env)
├── mosquitto/                mosquitto.conf, start.sh (สร้างไฟล์รหัสผ่าน), bridge/ (Wokwi)
├── influxdb/                 setup.sh (bucket solar_1m + Task), downsample_1m.flux
├── nodered/                  Dockerfile, seed-credentials.js, data/settings.js, data/flows.json
├── esp32_sim/                ESP32 จำลอง: plant.py (ใช้ pvsim), link.py (MQTT), virtual_esp32.py, replay.py, wokwi_bridge.py
├── wokwi/esp32_track, esp32_fixed   เฟิร์มแวร์ + diagram.json (ตรงกับโปรเจกต์ใน Wokwi)
├── matlab/                   pv_buck_mppt.m, analyze_pipeline.m, rf_train.m, rf_predict.m, out/
├── datasets/                 dataset ที่สร้างด้วย pvsim batch (ไม่เข้า Git, สร้างใหม่ได้)
└── docs/img/                 ภาพหน้าจอ Dashboard และ Flow
```

แก้ Flow ใน editor ของ Node-RED แล้วกด Deploy ไฟล์ `nodered/data/flows.json` จะถูกบันทึกทับ จึงเก็บเวอร์ชันใน Git ได้ตามหัวข้อ 2.9.1

## 13. ปัญหาที่พบบ่อย

- **พอร์ตชน** (1880, 1883, 8086 ถูกใช้อยู่): ปิดโปรแกรมที่ใช้พอร์ตนั้น หรือแก้เลขพอร์ตฝั่งซ้ายใน `ports:` ของ `docker-compose.yml`
- **หน้าเว็บไม่มีกราฟ**: ดู `docker compose logs esp32-sim` และหน้า "คุณภาพข้อมูล"; ถ้าจำลองตอนกลางคืนกำลังจะเป็น 0 ให้ตั้ง `SIM_START`
- **บอร์ด track ออฟไลน์ตอนใช้ Wokwi**: ดู `docker compose logs wokwi-bridge --tail 20`, ดู Serial Monitor ใน Wokwi ว่า `mqtt=up`, ตรวจว่า prefix ตรงกัน และแท็บ Wokwi เปิดอยู่
- **เปลี่ยนรหัส InfluxDB ใน .env แล้วไม่มีผล**: ค่าพวกนี้ใช้ตอนสร้างครั้งแรกเท่านั้น ต้อง `docker compose down -v` แล้วขึ้นใหม่ (ข้อมูลเดิมจะหาย)
- **Linux/Pi แจ้ง permission denied ที่ nodered/data**: `sudo chown -R 1000:1000 nodered/data`

## 14. สมมติฐานที่ใช้ (นอกเหนือจาก pvsim)

ระดับเงา `shade_25/50/75` = ผ้าบังแสงบัง substring เดียว 25/50/75 %, `shade_2sub_50` = บัง 2 substring 50 % · สัญญาณ LDR สร้างจากมุมคลาดเคลื่อน (อิ่มตัวที่ ±10°, คูณสัดส่วนแสงตรง) · ตำแหน่ง home (Az 180°, แผงราบ) · มอเตอร์ 2 W ต่อแกน · Reference cell มีสัญญาณรบกวน 0.5 % · ค่าเหล่านี้ปรับได้ใน `esp32_sim/plant.py` และ `wokwi/*/config.h` และควรสอบเทียบกับชุดทดลองจริง
