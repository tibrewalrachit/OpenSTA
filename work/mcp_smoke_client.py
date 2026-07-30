#!/usr/bin/env python3
"""Minimal MCP stdio client for the sta-smoke Makefile target.

Speaks just enough JSON-RPC over stdio to initialize an MCP server, list
its tools, and call design_summary + worst_paths against the smoke design.
No SDK dependency -- the point is a self-contained health check that any
MCP stdio server (sta-claude's server/sta_mcp.py in particular) can be
driven with.

Usage: mcp_smoke_client.py <server command...>
e.g.:  mcp_smoke_client.py python3 sta-claude/server/sta_mcp.py --backend opensta
"""

import json
import subprocess
import sys


class McpStdioClient:
    def __init__(self, argv):
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self._id = 0

    def request(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method,
               "params": params or {}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"server exited during {method}")
            reply = json.loads(line)
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply["result"]
            # notifications / other ids: ignore

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    client = McpStdioClient(sys.argv[1:])
    init = client.request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "sta-smoke", "version": "0"},
    })
    client.notify("notifications/initialized")
    print(f"initialized: {init['serverInfo']['name']}")

    tools = {t["name"] for t in client.request("tools/list")["tools"]}
    print(f"tools: {sorted(tools)}")
    for required in ("design_summary", "worst_paths"):
        if required not in tools:
            print(f"FAIL: server does not expose {required}")
            return 1

    summary = client.request("tools/call", {
        "name": "design_summary", "arguments": {}})
    print("design_summary:", json.dumps(summary["content"], indent=2)[:800])

    paths = client.request("tools/call", {
        "name": "worst_paths", "arguments": {"n": 3}})
    print("worst_paths:", json.dumps(paths["content"], indent=2)[:800])

    client.close()
    print("sta-smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
