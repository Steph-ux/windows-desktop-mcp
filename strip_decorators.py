"""Strip @mcp.tool() and @threaded_tool decorators from all tool files."""
import re
from pathlib import Path

root = Path("desktop_mcp/tools")
files = list(root.glob("*.py")) + [Path("desktop_mcp/tools_ai.py")]
total = 0
for f in files:
    content = f.read_text(encoding="utf-8")
    original = content
    content = re.sub(r'^\s*@mcp\.tool\(\)\s*\n', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*@threaded_tool\s*\n', '', content, flags=re.MULTILINE)
    if content != original:
        count = original.count('@mcp.tool()') 
        f.write_text(content, encoding="utf-8")
        total += count
        print(f"  Stripped {count} @mcp.tool() from {f.name}")
print(f"\nTotal: {total} decorators stripped")
