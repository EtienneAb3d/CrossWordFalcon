#!/usr/bin/env bash
#
# Install_qdrant.sh — one-time setup for the local Qdrant vector database
# used to store word embeddings (collection "words", see run_qdrant.sh).
#
# Qdrant runs as a Docker container rather than a native install: the
# official image is the only supported distribution on Linux, and it keeps
# the vector engine (and its own storage format) fully isolated from this
# project's Python venv — the same reasoning as running the LLM server as
# its own process. This script only makes sure Docker is available and the
# Qdrant image is pulled; run_qdrant.sh does the actual launch, the volume
# mount to data/qdrant/, and the "words" collection creation.
#
# Nothing here needs the project venv or env.sh. The image tag is the sole
# thing worth overriding (pin a specific version for reproducibility):
#   QDRANT_IMAGE=qdrant/qdrant:v1.12.4 ./Install_qdrant.sh
#
set -euo pipefail

cd "$(dirname "$0")"

# Kept in sync by hand with run_qdrant.sh's own fallback of the same name —
# both default here, both overridable via the same env var, so a pinned
# tag set once in the environment (or env.sh) reaches install and launch
# alike. "latest" by default so a fresh machine is not tied to a tag that
# may have been pruned from Docker Hub; pin it for a real deployment.
QDRANT_IMAGE="${QDRANT_IMAGE:-qdrant/qdrant:latest}"

# --- Docker itself ------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker not found — installing..."
    if [ "$(uname -s)" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install --cask docker \
                || echo "Warning: 'brew install --cask docker' failed — install Docker Desktop manually (https://www.docker.com/products/docker-desktop)."
            echo "Docker Desktop installed — start it once from Applications before running run_qdrant.sh."
        else
            echo "Warning: Homebrew not found — install Docker Desktop manually:"
            echo "  https://www.docker.com/products/docker-desktop"
        fi
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y docker.io \
            || { echo "Error: 'apt-get install docker.io' failed — install Docker manually." >&2; exit 1; }
        sudo systemctl enable --now docker || true
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y docker \
            || { echo "Error: 'dnf install docker' failed — install Docker manually." >&2; exit 1; }
        sudo systemctl enable --now docker || true
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm docker \
            || { echo "Error: 'pacman -S docker' failed — install Docker manually." >&2; exit 1; }
        sudo systemctl enable --now docker || true
    else
        echo "Error: no supported package manager found — install Docker manually:" >&2
        echo "  https://docs.docker.com/engine/install/" >&2
        exit 1
    fi
fi

echo "Docker: $(docker --version 2>/dev/null || echo 'installed')"

# Decide how to invoke docker: directly if the current user can, else via
# sudo. Not being in the 'docker' group is the common case on a fresh Linux
# box; adding yourself needs a re-login, so fall back to sudo rather than
# failing outright.
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
    if sudo docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
        echo "Note: current user cannot reach the Docker daemon — using 'sudo docker'."
        echo "      To drop the sudo:  sudo usermod -aG docker \"$USER\"   (then log out and back in)"
    else
        echo "Error: cannot reach the Docker daemon (not in the 'docker' group, and sudo failed)." >&2
        echo "  On Linux, either add yourself to the group:" >&2
        echo "      sudo usermod -aG docker \"$USER\"   (then log out and back in)" >&2
        echo "  or make sure the service is running:  sudo systemctl start docker" >&2
        echo "  On macOS, start Docker Desktop." >&2
        exit 1
    fi
fi

# --- Qdrant image -----------------------------------------------------

echo "Pulling $QDRANT_IMAGE ..."
$DOCKER pull "$QDRANT_IMAGE"

echo
echo "Qdrant is installed. Next:  ./run_qdrant.sh"
echo "  (starts the container, mounts data/qdrant/, and creates the \"words\" collection)"
