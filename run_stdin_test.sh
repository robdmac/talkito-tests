#!/bin/bash

# Docker test runner for stdin input e2e test
# Usage: ./run_stdin_test.sh

set -e

echo "=================================================="
echo "Talkito Stdin E2E Test - Docker Runner"
echo "=================================================="
echo ""

# Check if ANTHROPIC_API_KEY is set
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "Error: ANTHROPIC_API_KEY environment variable is not set"
    echo ""
    echo "Please set it before running:"
    echo "  export ANTHROPIC_API_KEY='your-api-key-here'"
    echo "  ./run_stdin_test.sh"
    echo ""
    exit 1
fi

echo "Building Docker image..."
docker-compose -f tests-repo/docker-compose.yml build

echo ""
echo "Running stdin e2e test in Docker..."
echo ""

# Run the test, passing through the API key
docker-compose -f tests-repo/docker-compose.yml run \
    -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    --rm \
    stdin-test

# Capture exit code
exit_code=$?

echo ""
if [ $exit_code -eq 0 ]; then
    echo "✓ Test passed!"
else
    echo "✗ Test failed with exit code $exit_code"
fi

exit $exit_code