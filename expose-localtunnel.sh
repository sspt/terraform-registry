#!/bin/bash
# Install localtunnel if not present (requires Node.js)
if ! command -v lt &> /dev/null; then
    echo "Installing localtunnel..."
    npm install -g localtunnel
fi

echo "Starting localtunnel on port 8000..."
lt --port 8000
