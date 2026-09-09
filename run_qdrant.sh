#!/usr/bin/env bash
#
# run_qdrant.sh — start (default) / stop / status the local Qdrant vector
# database, with persistent storage in data/qdrant/ and a collection named
# "words" ready to receive embeddings.
#
# Qdrant has no notion of a "database" file to point at on launch — storage
# lives in a directory it owns, and a "database" is a *collection* created
# through its API. So "launch with a words database" here means:
#   1. run the qdrant/qdrant container with data/qdrant/ bind-mounted as
#      its storage dir (so nothing is lost when the container is recreated);
#   2. wait for the HTTP API to come up;
#   3. create the "words" collection if it does not already exist.
#
# Re-running just relaunches cleanly: any previous container of the same
# name is stopped and removed first (same "stop what's already there"
# behaviour as run_llm.sh), then a fresh one is started against the same
# on-disk storage — so the "words" collection and its points survive.
#
# Config (env vars, each with the fallback below; env.sh is sourced if
# present so overrides can live there alongside the other project settings):
#   QDRANT_IMAGE          qdrant/qdrant:latest   (pin a version for a real deploy)
#   QDRANT_CONTAINER      crosswordfalcon-qdrant
#   QDRANT_HOST           127.0.0.1              (bind address — localhost only, like the back end)
#   QDRANT_PORT           6333                   (REST / dashboard)
#   QDRANT_GRPC_PORT      6334                   (gRPC)
#   QDRANT_COLLECTION     words
#   QDRANT_VECTOR_SIZE    (probed from the embed model, BAAI/bge-m3 -> 1024)
#   QDRANT_DISTANCE       Cosine                 (Cosine | Dot | Euclid | Manhattan)
#
# Usage:
#   ./run_qdrant.sh            # start (default)
#   ./run_qdrant.sh status
#   ./run_qdrant.sh stop
#
set -euo pipefail

cd "$(dirname "$0")"

if [ -f env.sh ]; then
    source env.sh
elif [ -f env_default.sh ]; then
    source env_default.sh
fi

QDRANT_IMAGE="${QDRANT_IMAGE:-qdrant/qdrant:latest}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-crosswordfalcon-qdrant}"
QDRANT_HOST="${QDRANT_HOST:-127.0.0.1}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
QDRANT_GRPC_PORT="${QDRANT_GRPC_PORT:-6334}"
QDRANT_COLLECTION="${QDRANT_COLLECTION:-words}"
QDRANT_VECTOR_SIZE="${QDRANT_VECTOR_SIZE:-1024}"
QDRANT_DISTANCE="${QDRANT_DISTANCE:-Cosine}"

STORAGE_DIR="$PWD/data/qdrant"
BASE_URL="http://${QDRANT_HOST}:${QDRANT_PORT}"

ACTION="${1:-start}"

# --help never needs Docker.
case "$ACTION" in
    -h|--help|help) sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

# --- docker invocation (direct, or via sudo if the user can't reach the daemon) ---

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker not found — run ./Install_qdrant.sh first." >&2
    exit 1
fi
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
    if sudo docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
    elif [ "$ACTION" = "status" ]; then
        echo "Qdrant is not running (the Docker daemon is not reachable)."
        exit 0
    else
        echo "Error: cannot reach the Docker daemon (not in the 'docker' group, and sudo failed)." >&2
        echo "  sudo usermod -aG docker \"$USER\"   (then log out and back in), or start Docker." >&2
        exit 1
    fi
fi

container_running() { [ -n "$($DOCKER ps -q -f "name=^${QDRANT_CONTAINER}$" 2>/dev/null || true)" ]; }
container_exists()  { [ -n "$($DOCKER ps -aq -f "name=^${QDRANT_CONTAINER}$" 2>/dev/null || true)" ]; }

# --- actions ----------------------------------------------------------

do_status() {
    if container_running; then
        echo "Qdrant is running:"
        $DOCKER ps -f "name=^${QDRANT_CONTAINER}$" --format '  {{.Names}}  {{.Status}}  {{.Ports}}'
        echo "  storage:   $STORAGE_DIR"
        echo "  REST:      $BASE_URL"
        echo "  dashboard: $BASE_URL/dashboard"
        if curl -fsS "$BASE_URL/collections/${QDRANT_COLLECTION}" >/dev/null 2>&1; then
            echo "  collection \"$QDRANT_COLLECTION\": present"
        else
            echo "  collection \"$QDRANT_COLLECTION\": MISSING (run ./run_qdrant.sh to create it)"
        fi
    elif container_exists; then
        echo "Qdrant container \"$QDRANT_CONTAINER\" exists but is stopped. Start it with ./run_qdrant.sh"
    else
        echo "Qdrant is not running."
    fi
}

do_stop() {
    if container_exists; then
        echo "Stopping and removing container \"$QDRANT_CONTAINER\"..."
        $DOCKER rm -f "$QDRANT_CONTAINER" >/dev/null
        echo "Stopped. (Storage in $STORAGE_DIR is kept.)"
    else
        echo "No container \"$QDRANT_CONTAINER\" — nothing to stop."
    fi
}

do_start() {
    if ! $DOCKER image inspect "$QDRANT_IMAGE" >/dev/null 2>&1; then
        echo "Image $QDRANT_IMAGE not present locally — pulling..."
        $DOCKER pull "$QDRANT_IMAGE"
    fi

    # Recreate from scratch every launch, against the same persistent
    # storage dir — matches run_llm.sh's "stop what's already on the port".
    if container_exists; then
        echo "Removing previous container \"$QDRANT_CONTAINER\"..."
        $DOCKER rm -f "$QDRANT_CONTAINER" >/dev/null
    fi

    mkdir -p "$STORAGE_DIR"

    # SELinux (Fedora/RHEL/CentOS) needs the volume relabelled or the
    # container cannot read/write the bind mount; harmless no-op elsewhere.
    local vol_opt=""
    if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null || echo Disabled)" != "Disabled" ]; then
        vol_opt=":z"
    fi

    echo "Starting Qdrant ($QDRANT_IMAGE)..."
    $DOCKER run -d \
        --name "$QDRANT_CONTAINER" \
        --restart unless-stopped \
        -p "${QDRANT_HOST}:${QDRANT_PORT}:6333" \
        -p "${QDRANT_HOST}:${QDRANT_GRPC_PORT}:6334" \
        -v "${STORAGE_DIR}:/qdrant/storage${vol_opt}" \
        "$QDRANT_IMAGE" >/dev/null

    # Wait for the HTTP API. The root endpoint returns version JSON as soon
    # as Qdrant is up.
    echo -n "Waiting for Qdrant to be ready"
    local ready="" i
    for i in $(seq 1 60); do
        if curl -fsS "$BASE_URL/" >/dev/null 2>&1; then ready=1; break; fi
        echo -n "."
        sleep 1
    done
    echo
    if [ -z "$ready" ]; then
        echo "Error: Qdrant did not become ready within 60s. Logs:" >&2
        $DOCKER logs --tail 40 "$QDRANT_CONTAINER" >&2 || true
        exit 1
    fi

    # Ensure the "words" collection exists (idempotent — the storage dir
    # persists it across container recreations). The authoritative setup
    # lives in backend/qdrant_store.py's QdrantStore.ensure_collection()
    # — it also registers the per-language tenant payload index and
    # on-disk vectors — so delegate to it when the venv is available;
    # fall back to a bare curl create otherwise.
    export QDRANT_HOST QDRANT_PORT QDRANT_COLLECTION QDRANT_DISTANCE QDRANT_ON_DISK
    export QDRANT_VECTOR_SIZE   # so the child skips the embed-server dimension probe
    # Match backend/qdrant_store.py's own default: vectors in RAM unless
    # QDRANT_ON_DISK is explicitly 1/true/yes/on (on-disk HNSW is unusably
    # slow on a spinning HDD).
    case "$(printf '%s' "${QDRANT_ON_DISK:-0}" | tr 'A-Z' 'a-z')" in
        1|true|yes|on) _on_disk=true ;;
        *) _on_disk=false ;;
    esac
    if [ -x .venv/bin/python ] && [ -f backend/qdrant_store.py ]; then
        .venv/bin/python -m backend.qdrant_store --init
    elif curl -fsS "$BASE_URL/collections/${QDRANT_COLLECTION}" >/dev/null 2>&1; then
        echo "Collection \"$QDRANT_COLLECTION\" already exists — kept as is."
    else
        echo "Creating collection \"$QDRANT_COLLECTION\" (size=$QDRANT_VECTOR_SIZE, distance=$QDRANT_DISTANCE, on_disk=$_on_disk)..."
        curl -fsS -X PUT "$BASE_URL/collections/${QDRANT_COLLECTION}" \
            -H 'Content-Type: application/json' \
            -d "{\"vectors\": {\"size\": ${QDRANT_VECTOR_SIZE}, \"distance\": \"${QDRANT_DISTANCE}\", \"on_disk\": ${_on_disk}}}" \
            >/dev/null
        echo "Collection \"$QDRANT_COLLECTION\" created."
    fi

    echo
    echo "Qdrant ready."
    echo "  storage:   $STORAGE_DIR"
    echo "  REST:      $BASE_URL"
    echo "  dashboard: $BASE_URL/dashboard"
    echo "  gRPC:      ${QDRANT_HOST}:${QDRANT_GRPC_PORT}"
    echo "  stop with: ./run_qdrant.sh stop"
}

# --- dispatch -------------------------------------------------------------

case "$ACTION" in
    start)  do_start ;;
    stop)   do_stop ;;
    status) do_status ;;
    *)
        echo "Unknown action: $ACTION" >&2
        echo "Usage: $0 {start|stop|status}" >&2
        exit 2
        ;;
esac
