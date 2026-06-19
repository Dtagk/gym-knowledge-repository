import subprocess
import sys

print("MCP server: gym-knowledge-repository | transport: stdio", flush=True)
subprocess.run([sys.executable, "-m", "yt_kg.mcp_server"])
