#!/usr/bin/env bash
# Build and run the Fedora Docker leak test for nodriver.
#
# Usage:
#   ./run_docker_test.sh [--iterations N] [--url URL]
#
# Examples:
#   ./run_docker_test.sh
#   ./run_docker_test.sh --iterations 5
#   ./run_docker_test.sh --iterations 3 --url https://www.reuters.com/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE_NAME="nodriver-leak-test"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Building Docker image: ${IMAGE_NAME}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

docker build \
    -f "${SCRIPT_DIR}/Dockerfile" \
    -t "${IMAGE_NAME}" \
    "${PROJECT_ROOT}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Running leak test"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

docker run --rm \
    --shm-size=256m \
    "${IMAGE_NAME}" \
    python3 leak_test.py "$@"
