"""Live test: Smart OCR with screen understanding"""
from desktop_mcp.tools.ocr import ocr_region
from desktop_mcp.tools.smart_ocr import _extract_elements, _match_prompt

# Full screen OCR
r = ocr_region(0, 0, 1920, 1080)
elements = _extract_elements(r)
print(f"Found {len(elements)} text elements")

# Smart search
for query in ["Manage MCPs", "test en live", "desktop-mcp", "Antigravity"]:
    matches = _match_prompt(query, elements)
    top3 = [(m["text"], round(m["match_score"], 2)) for m in matches[:3]]
    print(f"  '{query}' -> {top3}")
