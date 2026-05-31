from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from graph import compile_graph  # noqa: E402 (dotenv 먼저)


def main():
    input_dir = Path(__file__).parent.parent / "input"
    pptx_paths = [str(p) for p in sorted(input_dir.glob("*.pptx")) if not p.name.startswith("~$")]

    if not pptx_paths:
        print(f"[ERROR] input/ 디렉토리에 .pptx 파일이 없습니다: {input_dir}")
        return

    print(f"[START] PPTX 파일 {len(pptx_paths)}개 처리 시작")
    for p in pptx_paths:
        print(f"  - {Path(p).name}")

    graph = compile_graph()
    graph.invoke({
        "pptx_paths": pptx_paths,
        "change_points": [],
        "search_results": [],
        "human_selections": [],
        "bom_updates": [],
    })


if __name__ == "__main__":
    main()
