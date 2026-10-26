# Virtual ESP32 boards: runs pvsim physics + MQTT telemetry (section 2.7)
# pvsim source is mounted at runtime from the sibling repo via docker-compose volume.
FROM python:3.12-slim

WORKDIR /app

# Install pvsim's dependencies (pvsim itself is volume-mounted at /app/pvsim)
RUN pip install --no-cache-dir \
    "numpy>=1.24" "scipy>=1.10" "pandas>=2.0" \
    "paho-mqtt==2.1.0"

COPY esp32_sim ./esp32_sim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "esp32_sim"]
