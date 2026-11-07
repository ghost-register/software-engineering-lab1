"""Command-line build/run helpers for a local desktop-browser application."""

import argparse
import getpass
import json
from pathlib import Path
import sys

from waitress import serve

from .domain import DomainError
from .maintenance import MaintenanceService
from .seed import seed_demo
from .web import create_app


def parser():
    """Expose operational tasks without adding privileged public HTTP routes."""
    result = argparse.ArgumentParser(description="校园二手交易系统")
    result.add_argument("--data-dir", default="instance", help="数据库与图片存储目录")
    tasks = result.add_subparsers(dest="command", required=True)
    run = tasks.add_parser("run", help="启动本地网页应用")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=5053)
    tasks.add_parser("init", help="初始化空数据库")
    tasks.add_parser("demo", help="显式创建演示账号和商品")
    admin = tasks.add_parser("admin", help="在本机预置管理员")
    admin.add_argument("username")
    backup = tasks.add_parser("backup", help="在线备份数据库")
    backup.add_argument("destination")
    tasks.add_parser("check-db", help="只读数据库完整性检查")
    tasks.add_parser("check-state", help="检查商品预约状态、外键和请求归属")
    cleanup = tasks.add_parser("retention", help="预览过期记录清理，默认不写入")
    cleanup.add_argument("--keep-days", type=int, default=180)
    cleanup.add_argument("--apply", action="store_true")
    return result


def main(argv=None):
    """Return an exit code so both command-line and packaging tests can verify it."""
    options = parser().parse_args(argv)
    app = create_app(options.data_dir)
    services = app.extensions["market"]
    database = app.extensions["database"]
    try:
        if options.command == "run":
            print(f"本地访问地址：http://{options.host}:{options.port}", flush=True)
            serve(app, host=options.host, port=options.port, threads=24)
        elif options.command == "init":
            print(f"数据库已初始化：{database.path}")
        elif options.command == "demo":
            print(json.dumps(seed_demo(services), ensure_ascii=False, indent=2))
        elif options.command == "admin":
            secret = getpass.getpass("管理员密码：")
            confirmation = getpass.getpass("再次输入密码：")
            if secret != confirmation:
                raise ValueError("两次密码不一致")
            print(
                json.dumps(
                    services["auth"].create_admin(options.username, secret), ensure_ascii=False
                )
            )
        elif options.command == "backup":
            print(database.backup(Path(options.destination)))
        elif options.command == "check-db":
            results = database.integrity()
            print(json.dumps(results))
            return 0 if results == ["ok"] else 1
        elif options.command == "check-state":
            result = MaintenanceService(database).consistency()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        elif options.command == "retention":
            result = MaintenanceService(database).retention(options.keep_days, apply=options.apply)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (DomainError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
