#!/bin/bash

# CloudEx Portal - Development Startup Script
# Runs backend in foreground and frontend in background

set -e  # Exit on error

# Color codes for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  CloudEx Portal - Starting Development${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo -e "${RED}Error: .venv directory not found!${NC}"
    echo -e "${YELLOW}Please create it first:${NC}"
    echo -e "  python3 -m venv .venv"
    echo -e "  source .venv/bin/activate"
    echo -e "  pip install -r backend/requirements.txt"
    exit 1
fi

# Check if node_modules exists
if [ ! -d "frontend/node_modules" ]; then
    echo -e "${YELLOW}Warning: frontend/node_modules not found${NC}"
    echo -e "${YELLOW}Installing frontend dependencies...${NC}\n"
    cd frontend
    npm install
    cd ..
fi

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down services...${NC}"
    if [ ! -z "$FRONTEND_PID" ]; then
        echo -e "${YELLOW}Stopping frontend (PID: $FRONTEND_PID)...${NC}"
        kill $FRONTEND_PID 2>/dev/null || true
    fi
    echo -e "${GREEN}All services stopped${NC}"
    exit 0
}

# Trap Ctrl+C and cleanup
trap cleanup INT TERM

# Start frontend in background
echo -e "${BLUE}[1/2] Starting Frontend (background)...${NC}"
cd frontend
npm run dev > ../frontend.log 2>&1 &
FRONTEND_PID=$!
cd ..

# Wait a moment for frontend to start
sleep 2

# Check if frontend is still running
if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo -e "${RED}Error: Frontend failed to start${NC}"
    echo -e "${YELLOW}Check frontend.log for details${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Frontend started (PID: $FRONTEND_PID)${NC}"
echo -e "${GREEN}  URL: http://localhost:3000${NC}"
echo -e "${YELLOW}  Logs: frontend.log${NC}\n"

# Start backend in foreground
echo -e "${BLUE}[2/2] Starting Backend (foreground)...${NC}"
echo -e "${GREEN}  URL: http://localhost:8000${NC}"
echo -e "${GREEN}  Docs: http://localhost:8000/api/docs${NC}"
echo -e "${YELLOW}  Press Ctrl+C to stop all services${NC}\n"
echo -e "${BLUE}========================================${NC}\n"

cd backend
# Use the python from .venv directly to run uvicorn
../.venv/bin/python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# This line will only execute if uvicorn exits normally
cleanup
