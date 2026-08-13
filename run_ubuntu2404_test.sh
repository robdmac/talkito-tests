#!/bin/bash

# Test talkito installation on Ubuntu 24.04
# Usage: ./run_ubuntu2404_test.sh

set -e

echo "=================================================="
echo "Talkito Ubuntu 24.04 Installation Test"
echo "=================================================="
echo ""

cd "$(dirname "$0")/.."

echo "Building Ubuntu 24.04 Docker image..."
docker build -f tests-repo/Dockerfile.ubuntu2404 -t talkito-ubuntu2404-test .

echo ""
echo "Testing talkito import..."
docker run --rm talkito-ubuntu2404-test

echo ""
echo "Testing talkito CLI..."
docker run --rm talkito-ubuntu2404-test talkito --help

echo ""
echo "✓ Ubuntu 24.04 installation test passed!"
