"""One-click MCP server setup for Claude Desktop, Claude Code, Cursor, and Windsurf."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Terminal formatting
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _green(t: str) -> str: return f"\033[32m{t}\033[0m" if _USE_COLOR else t
def _red(t: str) -> str: return f"\033[31m{t}\033[0m" if _USE_COLOR else t
def _yellow(t: str) -> str: return f"\033[33m{t}\033[0m" if _USE_COLOR else t
def _bold(t: str) -> str: return f"\033[1m{t}\033[0m" if _USE_COLOR else t


OK = _green("OK")
FAIL = _red("FAIL")
WARN = _yellow("WARN")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def find_scout_mcp() -> Path | None:
    """Find the absolute path to the scout-mcp binary."""
    found = shutil.which("scout-mcp")
    if found:
        return Path(found).resolve()
    # Fallback: check alongside the current Python interpreter (venv without PATH)
    python_dir = Path(sys.executable).parent
    for name in ("scout-mcp", "scout-mcp.exe"):
        candidate = python_dir / name
        if candidate.is_file():
            return candidate.resolve()
    return None


def find_claude_cli() -> Path | None:
    """Find the Claude Code CLI binary."""
    found = shutil.which("claude")
    return Path(found).resolve() if found else None


def get_env_vars() -> dict[str, str]:
    """Collect env vars to pass through to the MCP server config."""
    env: dict[str, str] = {}
    for key in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            env[key] = val
    return env


# ---------------------------------------------------------------------------
# Config building & merging
# ---------------------------------------------------------------------------


def build_server_entry(scout_mcp_path: Path, env: dict[str, str]) -> dict:
    """Build the JSON object for one MCP server entry."""
    entry: dict = {"command": str(scout_mcp_path)}
    if env:
        entry["env"] = env
    return entry


def _read_json_file(path: Path) -> dict:
    """Read a JSON file, returning {} if missing or empty."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _write_json_file(path: Path, data: dict, backup: bool = True) -> Path | None:
    """Write JSON to a file, optionally backing up the original."""
    backup_path = None
    if backup and path.is_file():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_suffix(f".{ts}.bak")
        shutil.copy2(path, backup_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return backup_path


def merge_server_config(
    config_path: Path, server_name: str, server_entry: dict,
) -> tuple[bool, Path | None]:
    """Merge a server entry into an MCP config file. Returns (changed, backup_path)."""
    data = _read_json_file(config_path)
    servers = data.setdefault("mcpServers", {})
    if servers.get(server_name) == server_entry:
        return False, None
    servers[server_name] = server_entry
    backup_path = _write_json_file(config_path, data, backup=True)
    return True, backup_path


# ---------------------------------------------------------------------------
# Client config paths
# ---------------------------------------------------------------------------


def _config_path_claude_desktop() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if platform.system() == "Windows":
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "Claude" / "claude_desktop_config.json"


def _config_path_cursor() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _config_path_windsurf() -> Path:
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


def _find_repo_root() -> Path | None:
    """Walk up from this file to find the nearest .git directory."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".git").is_dir():
            return current
        current = current.parent
    return None


# ---------------------------------------------------------------------------
# Per-client setup
# ---------------------------------------------------------------------------


def setup_claude_desktop(scout_mcp_path: Path, env: dict[str, str]) -> bool:
    """Configure Scout in Claude Desktop's config file."""
    config_path = _config_path_claude_desktop()
    entry = build_server_entry(scout_mcp_path, env)
    print(f"  Config: {config_path}")
    changed, backup = merge_server_config(config_path, "scout", entry)
    if not changed:
        print(f"  {OK} Already configured correctly")
    else:
        print(f"  {OK} Scout server added to config")
        if backup:
            print(f"  Backup: {backup}")
    print("  Note: Restart Claude Desktop to pick up changes")
    return True


def setup_claude_code(scout_mcp_path: Path, env: dict[str, str]) -> bool:
    """Configure Scout in Claude Code (CLI preferred, .mcp.json fallback)."""
    claude_cli = find_claude_cli()
    if claude_cli:
        print(f"  Using Claude CLI: {claude_cli}")
        cmd: list[str] = [str(claude_cli), "mcp", "add"]
        for key, val in env.items():
            cmd.extend(["-e", f"{key}={val}"])
        cmd.extend(["-s", "user", "scout", "--", str(scout_mcp_path)])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                print(f"  {OK} Scout server registered via Claude CLI (user scope)")
                return True
            stderr = result.stderr.strip()
            print(f"  {WARN} Claude CLI returned error: {stderr}")
            print("  Falling back to .mcp.json...")
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            print(f"  {WARN} Claude CLI failed: {exc}")
            print("  Falling back to .mcp.json...")

    repo_root = _find_repo_root()
    if not repo_root:
        print(f"  {FAIL} Could not find project root (no .git directory above)")
        return False
    mcp_json = repo_root / ".mcp.json"
    entry = build_server_entry(scout_mcp_path, env)
    changed, backup = merge_server_config(mcp_json, "scout", entry)
    if not changed:
        print(f"  {OK} .mcp.json already configured correctly")
    else:
        print(f"  {OK} Scout server added to {mcp_json}")
        if backup:
            print(f"  Backup: {backup}")
    return True


def setup_cursor(scout_mcp_path: Path, env: dict[str, str]) -> bool:
    """Configure Scout in Cursor's MCP config."""
    config_path = _config_path_cursor()
    entry = build_server_entry(scout_mcp_path, env)
    print(f"  Config: {config_path}")
    changed, backup = merge_server_config(config_path, "scout", entry)
    if not changed:
        print(f"  {OK} Already configured correctly")
    else:
        print(f"  {OK} Scout server added to config")
        if backup:
            print(f"  Backup: {backup}")
    print("  Note: Restart Cursor to pick up changes")
    return True


def setup_windsurf(scout_mcp_path: Path, env: dict[str, str]) -> bool:
    """Configure Scout in Windsurf's MCP config."""
    config_path = _config_path_windsurf()
    entry = build_server_entry(scout_mcp_path, env)
    print(f"  Config: {config_path}")
    changed, backup = merge_server_config(config_path, "scout", entry)
    if not changed:
        print(f"  {OK} Already configured correctly")
    else:
        print(f"  {OK} Scout server added to config")
        if backup:
            print(f"  Backup: {backup}")
    print("  Note: Restart Windsurf to pick up changes")
    return True


CLIENTS = {
    "claude-desktop": setup_claude_desktop,
    "claude-code": setup_claude_code,
    "cursor": setup_cursor,
    "windsurf": setup_windsurf,
}


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def verify() -> bool:
    """Run all verification checks. Returns True if all pass."""
    all_ok = True
    print(_bold("Scout MCP Setup Verification"))
    print()

    # 1. scout-mcp binary
    scout_mcp = find_scout_mcp()
    if scout_mcp:
        print(f"  {OK} scout-mcp found: {scout_mcp}")
    else:
        print(f"  {FAIL} scout-mcp not found on PATH")
        print("       Run: pip install -e .  (from the scout directory)")
        all_ok = False

    # 2. ANTHROPIC_API_KEY
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        masked = api_key[:7] + "..." + api_key[-4:]
        print(f"  {OK} ANTHROPIC_API_KEY set ({masked})")
    else:
        print(f"  {FAIL} ANTHROPIC_API_KEY not set (scoring will not work)")
        all_ok = False

    # 3. GITHUB_TOKEN (optional)
    if os.environ.get("GITHUB_TOKEN", "").strip():
        print(f"  {OK} GITHUB_TOKEN set")
    else:
        print(f"  {WARN} GITHUB_TOKEN not set (optional, improves GitHub enrichment)")

    # 4. Per-client config checks
    print()
    print(_bold("Client configurations:"))

    checks = [
        ("Claude Desktop", _config_path_claude_desktop()),
        ("Cursor", _config_path_cursor()),
        ("Windsurf", _config_path_windsurf()),
    ]
    for label, path in checks:
        if not path.is_file():
            print(f"  {WARN} {label}: config not found at {path}")
            continue
        try:
            data = _read_json_file(path)
            servers = data.get("mcpServers", {})
            if "scout" in servers:
                cmd = servers["scout"].get("command", "")
                if Path(cmd).is_file():
                    print(f"  {OK} {label}: scout configured, binary exists")
                else:
                    print(f"  {FAIL} {label}: scout configured but binary missing: {cmd}")
                    all_ok = False
            else:
                print(f"  {WARN} {label}: config exists but scout not configured")
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"  {FAIL} {label}: config parse error: {exc}")
            all_ok = False

    # 5. Claude Code
    claude_cli = find_claude_cli()
    if claude_cli:
        try:
            result = subprocess.run(
                [str(claude_cli), "mcp", "get", "scout"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                print(f"  {OK} Claude Code: scout server registered")
            else:
                repo_root = _find_repo_root()
                if repo_root and (repo_root / ".mcp.json").is_file():
                    data = _read_json_file(repo_root / ".mcp.json")
                    if "scout" in data.get("mcpServers", {}):
                        print(f"  {OK} Claude Code: scout configured via .mcp.json")
                    else:
                        print(f"  {WARN} Claude Code: .mcp.json exists but scout not configured")
                else:
                    print(f"  {WARN} Claude Code: scout not configured")
        except Exception:
            print(f"  {WARN} Claude Code: could not check (CLI error)")
    else:
        print(f"  {WARN} Claude Code: CLI not found")

    print()
    if all_ok:
        print(_green("All checks passed."))
    else:
        print(_red("Some checks failed. See above for details."))
    return all_ok


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

USAGE = """\
Usage: scout-setup <command>

Commands:
  claude-desktop   Configure Scout MCP in Claude Desktop
  claude-code      Configure Scout MCP in Claude Code
  cursor           Configure Scout MCP in Cursor
  windsurf         Configure Scout MCP in Windsurf
  all              Configure all detected clients
  --verify         Check if everything is configured correctly
  --help           Show this help message

Environment variables (read from current shell):
  ANTHROPIC_API_KEY   Required for LLM scoring (passed to MCP server)
  GITHUB_TOKEN        Optional for GitHub enrichment (passed to MCP server)
"""


def main() -> None:
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(USAGE)
        sys.exit(0)

    if "--verify" in args:
        ok = verify()
        sys.exit(0 if ok else 1)

    client_name = args[0]
    if client_name != "all" and client_name not in CLIENTS:
        print(f"{FAIL} Unknown client: {client_name}")
        print(f"  Available: {', '.join(CLIENTS)} or 'all'")
        sys.exit(1)

    print(_bold("Scout MCP Setup"))
    print()

    # Step 1: Find scout-mcp
    scout_mcp = find_scout_mcp()
    if not scout_mcp:
        print(f"  {FAIL} scout-mcp not found on PATH or in Python environment")
        print("  Run: pip install -e .  (from the scout directory)")
        sys.exit(1)
    print(f"  {OK} scout-mcp: {scout_mcp}")

    # Step 2: Collect env vars
    env = get_env_vars()
    if env.get("ANTHROPIC_API_KEY"):
        print(f"  {OK} ANTHROPIC_API_KEY will be passed through")
    else:
        print(f"  {WARN} ANTHROPIC_API_KEY not set -- scoring will not work")
    if env.get("GITHUB_TOKEN"):
        print(f"  {OK} GITHUB_TOKEN will be passed through")
    print()

    # Step 3: Dispatch
    targets = list(CLIENTS.items()) if client_name == "all" else [(client_name, CLIENTS[client_name])]
    for name, setup_fn in targets:
        print(_bold(f"Configuring {name}..."))
        try:
            setup_fn(scout_mcp, env)
        except Exception as exc:
            print(f"  {FAIL} Error: {exc}")
        print()

    print("Done. Run 'scout-setup --verify' to check everything.")


if __name__ == "__main__":
    main()
