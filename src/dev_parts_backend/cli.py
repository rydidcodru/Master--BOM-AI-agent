import argparse
import os
import shutil
from pathlib import Path
import json
import subprocess
import sys

from .bom_table import write_bom_dump_csv
from .config import get_settings
from .db import connect, init_db
from .embedding_csv import embed_csv
from .loader import load_csvs
from .repository import search_details, stats


def cmd_init_db(args) -> None:
    settings = get_settings()
    init_db(args.db or settings.db_path)
    print(f"initialized db: {args.db or settings.db_path}")


def cmd_load_csv(args) -> None:
    settings = get_settings()
    db_path = args.db or settings.db_path
    master_csv = args.master or settings.master_csv
    detail_csv = args.detail or settings.detail_csv
    init_db(db_path)
    with connect(db_path) as conn:
        result = load_csvs(conn, master_csv, detail_csv, reset=args.reset)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_stats(args) -> None:
    settings = get_settings()
    with connect(args.db or settings.db_path) as conn:
        print(json.dumps(stats(conn), ensure_ascii=False, indent=2))


def cmd_search(args) -> None:
    settings = get_settings()
    with connect(args.db or settings.db_path) as conn:
        rows = search_details(conn, args.q, limit=args.limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_embed_csv(args) -> None:
    result = embed_csv(
        args.input,
        args.output,
        model=args.model,
        batch_size=args.batch_size,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_extract_bom(args) -> None:
    with open(args.pptx, "rb") as f:
        pptx_bytes = f.read()
    result = write_bom_dump_csv(pptx_bytes, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_serve(args) -> None:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "dev_parts_backend.api.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.reload:
        command.append("--reload")
    raise SystemExit(subprocess.call(command))


def cmd_web(args) -> None:
    """React(Vite) 개발 서버를 실행한다. 백엔드(`devparts serve`)는 따로 띄워야 한다.

    프론트엔드는 저장소 루트의 frontend/ 에 있고, Vite 프록시(/api)로 FastAPI를 호출한다.
    """
    frontend_dir = Path(args.dir or "frontend").resolve()
    if not (frontend_dir / "package.json").exists():
        raise SystemExit(f"frontend 디렉터리를 찾지 못했습니다: {frontend_dir} (저장소 루트에서 실행하세요)")

    node = shutil.which("node")
    if not node:
        raise SystemExit("node를 찾지 못했습니다. Node.js를 설치하세요.")

    node_modules = frontend_dir / "node_modules"
    vite_bin = node_modules / "vite" / "bin" / "vite.js"
    if not vite_bin.exists():
        # 최초 1회 의존성 설치. npm은 pnpm v10의 build-script 게이트가 없어 더 안전하다.
        installer = shutil.which("npm") or shutil.which("pnpm")
        if not installer:
            raise SystemExit("node_modules가 없고 npm/pnpm도 없습니다. frontend에서 `npm install`을 먼저 실행하세요.")
        print(f"[web] 의존성 설치: {Path(installer).name} install")
        if subprocess.call([installer, "install"], cwd=frontend_dir) != 0 or not vite_bin.exists():
            raise SystemExit("의존성 설치에 실패했습니다. frontend에서 직접 `npm install`을 실행해보세요.")

    api_url = f"http://{args.api_host}:{args.api_port}"
    env = dict(**os.environ, DEV_PARTS_API_URL=api_url)
    # pnpm 래퍼(실행 전 의존성 검사 + esbuild build-script 게이트)를 우회하려고
    # node로 vite를 직접 실행한다.
    command = [node, str(vite_bin), "--host", args.host, "--port", str(args.port)]
    print(f"[web] {api_url} 백엔드로 프록시. http://{args.host}:{args.port} 접속")
    raise SystemExit(subprocess.call(command, cwd=frontend_dir, env=env))


def main() -> None:
    parser = argparse.ArgumentParser(prog="devparts")
    parser.add_argument("--db", help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("load-csv")
    p.add_argument("--master", help="master.csv path")
    p.add_argument("--detail", help="detail.csv path")
    p.add_argument("--reset", action="store_true", help="delete existing rows before loading")
    p.set_defaults(func=cmd_load_csv)

    p = sub.add_parser("stats")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("search")
    p.add_argument("--q", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("embed-csv")
    p.add_argument("--input", required=True, help="source detail CSV path")
    p.add_argument("--output", required=True, help="output detail CSV path with embedding columns")
    p.add_argument("--model", default="text-embedding-3-small", help="OpenAI embedding model")
    p.add_argument("--batch-size", type=int, default=96)
    p.add_argument("--force", action="store_true", help="regenerate existing embedding columns")
    p.set_defaults(func=cmd_embed_csv)

    p = sub.add_parser("extract-bom", help="PPTX의 Design Points BOM 표를 충실하게 CSV로 추출")
    p.add_argument("--pptx", required=True, help="입력 PPTX 경로")
    p.add_argument("--output", required=True, help="출력 CSV 경로")
    p.set_defaults(func=cmd_extract_bom)

    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("web", help="React(Vite) 개발 서버 실행 (백엔드는 devparts serve로 별도 실행)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5173)
    p.add_argument("--api-host", default="127.0.0.1")
    p.add_argument("--api-port", type=int, default=8000)
    p.add_argument("--dir", help="frontend 디렉터리 경로 (기본: ./frontend)")
    p.set_defaults(func=cmd_web)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
