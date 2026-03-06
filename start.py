"""
start.py — PersonalBrain 一键启动脚本

用法：
  python start.py                     # 启动 MCP 服务（Streamable HTTP，端口 8765）
  python start.py --transport stdio   # 启动 MCP 服务（stdio，供 Claude Desktop 等连接）
  python start.py --transport sse     # 启动 MCP 服务（SSE，端口 8765）
  python start.py --admin           # 同时启动管理后台（Streamlit，端口 8501）
  python start.py --admin-only      # 仅启动管理后台
  python start.py --port 9000       # 指定端口（SSE/HTTP 模式）
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _banner():
    print("=" * 50)
    print("  PersonalBrain — 个人知识库记忆后端")
    print("=" * 50)


def _init_db():
    """确保数据库已初始化。"""
    print("▶ 初始化数据库...")
    result = subprocess.run(
        [PYTHON, "-m", "personal_brain.cli", "init"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [错误] 数据库初始化失败:\n{result.stderr}")
        sys.exit(1)
    print(f"  {result.stdout.strip()}")


def _start_admin(port: int = 8501) -> subprocess.Popen:
    """启动 Streamlit 管理后台（后台进程）。"""
    print(f"▶ 启动管理后台 → http://localhost:{port}")
    proc = subprocess.Popen(
        [
            PYTHON, "-m", "streamlit", "run",
            str(ROOT / "personal_brain" / "admin.py"),
            "--server.port", str(port),
            "--server.headless", "true",
        ],
        cwd=ROOT,
    )
    return proc


def _start_mcp(transport: str, host: str, port: int):
    """启动 MCP 服务（前台，阻塞）。"""
    if transport == "stdio":
        print("▶ 启动 MCP 服务（stdio 模式）")
        print("  客户端配置示例（Claude Desktop）：")
        print(f'  "command": "{PYTHON}"')
        print(f'  "args": ["-m", "personal_brain.cli", "serve"]')
    else:
        print(f"▶ 启动 MCP 服务（{transport} 模式） → http://{host}:{port}")

    print("-" * 50)

    subprocess.run(
        [PYTHON, "-m", "personal_brain.cli", "serve",
         "--transport", transport,
         "--host", host,
         "--port", str(port)],
        cwd=ROOT,
    )


def main():
    parser = argparse.ArgumentParser(description="PersonalBrain 一键启动")
    parser.add_argument(
        "--transport", "-t",
        default="http",
        choices=["stdio", "sse", "http"],
        help="MCP 传输方式（默认 http）",
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（SSE/HTTP 模式）")
    parser.add_argument("--port", "-p", type=int, default=8765, help="MCP 服务端口（SSE/HTTP 模式）")
    parser.add_argument("--admin", "-a", action="store_true", help="同时启动管理后台")
    parser.add_argument("--admin-only", action="store_true", help="仅启动管理后台")
    parser.add_argument("--admin-port", type=int, default=8501, help="管理后台端口（默认 8501）")
    args = parser.parse_args()

    _banner()
    _init_db()

    admin_proc = None

    if args.admin_only:
        print()
        admin_proc = _start_admin(args.admin_port)
        try:
            admin_proc.wait()
        except KeyboardInterrupt:
            print("\n⏹ 已停止")
        return

    if args.admin:
        print()
        admin_proc = _start_admin(args.admin_port)

    print()

    try:
        _start_mcp(args.transport, args.host, args.port)
    except KeyboardInterrupt:
        print("\n⏹ MCP 服务已停止")
    finally:
        if admin_proc:
            admin_proc.terminate()
            print("⏹ 管理后台已停止")


if __name__ == "__main__":
    main()
