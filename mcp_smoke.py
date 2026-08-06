#!/usr/bin/env python3
"""End-to-end MCP stdio smoke test for the memo-bank federation.

Launches the server via the SAME command .mcp.json uses, performs a real MCP
handshake over stdio, and exercises the live tools — proving the wiring works
from a client's perspective, not just via in-process calls.

    .venv/bin/python mcp_smoke.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
UMBRELLA = HERE.parents[1]
REGISTRY = UMBRELLA / ".island-slices.json"


async def main() -> int:
    params = StdioServerParameters(
        command=str(HERE / ".venv" / "bin" / "python"),
        args=[str(HERE / "memo_bank.py"), "--federation", str(REGISTRY)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"tools/list → {len(names)} tools: {names}")

            # rung 0: resolve_path
            r = await session.call_tool(
                "docs.resolve_path",
                {"path": "src/main/java/example/server/Sessions.java"})
            payload = json.loads(r.content[0].text)
            top = payload["results"][0]["id"] if payload.get("results") else None
            print(f"resolve_path(Sessions.java) → {top}")

            # rung 4: compose_context under a budget
            r = await session.call_tool(
                "docs.compose_context",
                {"path": "src/screens/PlaybackScreen.tsx", "budget_tokens": 1200})
            payload = json.loads(r.content[0].text)
            secs = [s["id"] for s in payload.get("sections", [])]
            print(f"compose_context(PlaybackScreen, 1200) → mode={payload['mode']} "
                  f"used={payload.get('used_tokens')} sections={secs}")

            ok = (len(names) == 8 and top is not None
                  and payload["mode"] == "assembled")
            print("SMOKE:", "PASS" if ok else "FAIL")
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
