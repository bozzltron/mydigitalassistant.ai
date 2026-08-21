#!/bin/bash
# Run the evaluation harness.
# Requires: real Ollama at 127.0.0.1:11434 and SearXNG at 127.0.0.1:8080.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR" && pwd)"

# Build the eval image if needed
IMAGE="mydigitalassistantai-assistant"

if ! docker image inspect "$IMAGE" > /dev/null 2>&1; then
    echo "Building eval image..."
    docker build -t "$IMAGE" "$PROJECT_ROOT" --quiet
fi

echo "Starting evaluation..."
docker run --rm \
    --network host \
    -v "$PROJECT_ROOT:/app" \
    -w /app \
    "$IMAGE" \
    python -m assistant.eval.runner
