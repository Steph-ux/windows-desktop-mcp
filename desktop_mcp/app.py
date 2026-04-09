from mcp.server.fastmcp import FastMCP


mcp = FastMCP(
    "windows-desktop-control",
    instructions=(
        "Windows desktop control bridge. Prefer capture and annotate tools when "
        "the host model can inspect returned images directly. Use screenshot tools "
        "before clicking when state is uncertain. Prefer list_windows, "
        "find_ui_elements, and inspect_ui_tree before raw screen clicks. Treat "
        "describe_screen as an optional fallback, not the primary visual path. "
        "The user can abort pyautogui actions by moving the mouse to the top-left "
        "corner because pyautogui failsafe is enabled."
    ),
)
