#!/bin/bash
# AutoGLM WebSocket Mode - Quick Start Script
#
# This script starts the AutoGLM server in WebSocket mode.

set -e

# Configuration
export AUTOGLEM_WEBSOCKET_MODE=true
export AUTOGLEM_WS_HOST="${AUTOGLEM_WS_HOST:-0.0.0.0}"
export AUTOGLEM_WS_PORT="${AUTOGLEM_WS_PORT:-8765}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}AutoGLM WebSocket Mode${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check dependencies
echo -e "${YELLOW}Checking dependencies...${NC}"

if ! python -c "import websockets" 2>/dev/null; then
    echo -e "${RED}✗ websockets module not found${NC}"
    echo "Installing: pip install websockets"
    pip install websockets
else
    echo -e "${GREEN}✓ websockets module installed${NC}"
fi

if ! python -c "from PIL import Image" 2>/dev/null; then
    echo -e "${RED}✗ Pillow module not found${NC}"
    echo "Installing: pip install Pillow"
    pip install Pillow
else
    echo -e "${GREEN}✓ Pillow module installed${NC}"
fi

echo ""
echo -e "${BLUE}Configuration:${NC}"
echo "  AUTOGLEM_WEBSOCKET_MODE: $AUTOGLEM_WEBSOCKET_MODE"
echo "  AUTOGLEM_WS_HOST: $AUTOGLEM_WS_HOST"
echo "  AUTOGLEM_WS_PORT: $AUTOGLEM_WS_PORT"
echo ""

# Get optional command line arguments
BASE_URL="${1:-http://localhost:8000/v1}"
MODEL="${2:-autoglm-phone-9b}"
TASK="${3:-}"

echo -e "${BLUE}Starting AutoGLM Server...${NC}"
echo "  Base URL: $BASE_URL"
echo "  Model: $MODEL"
if [ -n "$TASK" ]; then
    echo "  Task: $TASK"
fi
echo ""

# Check if model API is available
echo -e "${YELLOW}Checking model API...${NC}"
if curl -s "$BASE_URL"/models > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Model API is accessible${NC}"
else
    echo -e "${YELLOW}⚠ Model API may not be accessible at $BASE_URL${NC}"
    echo "  Continuing anyway..."
fi
echo ""

# Start server
if [ -n "$TASK" ]; then
    # Single task mode
    echo -e "${GREEN}Running single task...${NC}"
    python main.py --base-url "$BASE_URL" --model "$MODEL" "$TASK"
else
    # Interactive mode
    echo -e "${GREEN}Starting interactive mode${NC}"
    echo -e "${YELLOW}Press Ctrl+C to exit${NC}"
    echo ""
    python main.py --base-url "$BASE_URL" --model "$MODEL"
fi
