# Node-RED on the (simulated) Raspberry Pi, with the nodes listed in Table 2.9-1.
# The same image builds for amd64 (PC / VM) and arm64 (Raspberry Pi 4/5, 64-bit OS).
FROM nodered/node-red:5.0.9

# Extra nodes, pinned so flows.json keeps working
RUN npm install --no-audit --no-fund --omit=dev \
        @flowfuse/node-red-dashboard@1.34.0 \
        node-red-contrib-influxdb@0.7.0

# Writes the InfluxDB token from the environment into flows_cred.json at start-up
COPY seed-credentials.js /usr/src/node-red/seed-credentials.js

ENTRYPOINT ["sh", "-c", "node /usr/src/node-red/seed-credentials.js && exec ./entrypoint.sh"]
