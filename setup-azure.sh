#!/bin/bash
# Quick setup for Azure Student VM (Ubuntu)
# Run this once after cloning both repos.

set -e

echo "=== Solar IoT Simulation — Azure Setup ==="

# 1. Install Docker (rootless — no sudo needed)
if ! command -v docker &>/dev/null; then
    echo "[1/4] Installing rootless Docker..."
    curl -fsSL https://get.docker.com/rootless | sh
    echo 'export PATH=$HOME/bin:$PATH' >> ~/.bashrc
    echo 'export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock' >> ~/.bashrc
    export PATH=$HOME/bin:$PATH
    export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock
    systemctl --user start docker
    systemctl --user enable docker
    echo "   Docker rootless installed ✓"
else
    echo "[1/4] Docker already installed ✓"
fi

# 2. Install docker compose plugin
if ! docker compose version &>/dev/null; then
    echo "[2/4] Installing docker-compose plugin..."
    DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker}
    mkdir -p "$DOCKER_CONFIG/cli-plugins"
    COMPOSE_VER=$(curl -sL https://api.github.com/repos/docker/compose/releases/latest | grep tag_name | cut -d'"' -f4)
    curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VER}/docker-compose-linux-$(uname -m)" \
         -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
    chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
    echo "   docker-compose ${COMPOSE_VER} installed ✓"
else
    echo "[2/4] docker-compose already installed ✓"
fi

# 3. Clone repos if needed
PROJ_DIR=$(dirname "$(readlink -f "$0")")
cd "$PROJ_DIR/.."

if [ ! -d "sim-daniel" ]; then
    echo "[3/4] Cloning pvsim (sim-daniel)..."
    git clone https://github.com/ItsRatcha/solar_panel_simulation.git sim-daniel
else
    echo "[3/4] sim-daniel already exists ✓"
fi

# 4. Create .env from example
cd "$PROJ_DIR"
if [ ! -f ".env" ]; then
    echo "[4/4] Creating .env from .env.example..."
    cp .env.example .env
    echo "   ⚠  Edit .env to change passwords before production use!"
else
    echo "[4/4] .env already exists ✓"
fi

echo ""
echo "=== Ready! ==="
echo "  cd $(basename "$PROJ_DIR")"
echo "  docker compose up -d --build"
echo "  Dashboard: http://localhost:1880/dashboard"
echo ""
echo "  To open port on Azure:"
echo "    az vm open-port --port 1880 --resource-group <rg> --name <vm>"
