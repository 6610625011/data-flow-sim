#!/bin/bash
# One-shot setup run by the "influx-init" service after InfluxDB is healthy.
# Idempotent: safe on every "docker compose up".
#   - bucket solar_1m (1-minute summaries, section 2.7.5)
#   - task downsample_1m (re-created so edits to downsample_1m.flux take effect)
# The influx CLI reads INFLUX_HOST / INFLUX_TOKEN / INFLUX_ORG from the environment.
set -euo pipefail

for i in $(seq 1 60); do
  influx ping >/dev/null 2>&1 && break
  sleep 1
done

if influx bucket list --name solar_1m >/dev/null 2>&1; then
  echo "bucket solar_1m exists"
else
  influx bucket create --name solar_1m --retention 0 --description "1-minute summaries for analysis and ML"
fi

old_ids=$(influx task list --hide-headers | awk -F'\t' '$2 == "downsample_1m" {print $1}')
for id in $old_ids; do
  influx task delete --id "$id" >/dev/null
done
influx task create --file /init/downsample_1m.flux
echo "InfluxDB ready: buckets solar_raw + solar_1m, task downsample_1m"
