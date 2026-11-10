#!/usr/bin/env bash
# Launches the peon-ping MCP server. Exits silently until the engine
# dependencies are installed — run /peon-ping-setup once after cloning.
MCP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../engine/mcp" && pwd)"
if [ ! -f "$MCP_DIR/node_modules/@modelcontextprotocol/sdk/package.json" ]; then
  exit 0
fi
exec node "$MCP_DIR/peon-mcp.js"
