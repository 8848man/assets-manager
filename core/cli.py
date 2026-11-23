"""Assets Manager 명령줄 (v3.7). 다른 프로젝트의 AI가 스킬처럼 부르는 입구.

  python cli.py standard "<작업 한 줄>" [--set <id>] [--no-gate]   맞는 표준을 찾아 조립된 지침 출력
  python cli.py standard "<작업 한 줄>" --lookup                   파일을 만들지 않고 표준만 조회
  python cli.py sets                                                세트 목록
  python cli.py assemble <id> [--task "..."]                        특정 노드를 조립
  python cli.py <am 명령> ...                                       노드 보기·바꾸기 (python cli.py help)

라이브러리 위치: --root, 없으면 이 파일이 있는 app/의 상위 폴더. 앱이 꺼져 있어도 파일만 읽어 동작한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import Library  # noqa: E402
import standards  # noqa: E402


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.environ.get("ASSETS_MANAGER_ROOT") or os.path.dirname(os.path.dirname(HERE))
    if argv[:1] == ["--root"] and len(argv) > 1:
        root, argv = argv[1], argv[2:]
    if not argv or argv[0] not in ("standard", "sets", "assemble"):
        # v3.8 `am` 노드 명령 (console.py). 콘솔 탭·AI 탭·명령줄이 같은 명령을 쓴다
        import console
        res = console.run(os.path.abspath(root), argv, os.environ.get("AM_ACTOR", "cli"))
        sys.stdout.write(res["output"])
        return res["code"]
    argv = ["--root", root] + argv
    ap = argparse.ArgumentParser(prog="assets-manager")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(HERE)))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("standard")
    s.add_argument("task")
    s.add_argument("--set")
    s.add_argument("--no-gate", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("--lookup", action="store_true", help="파일을 만들지 않고 선택된 표준과 후보만 출력")
    sub.add_parser("sets")
    a = sub.add_parser("assemble")
    a.add_argument("id")
    a.add_argument("--task", default="")
    args = ap.parse_args(argv)
    lib = Library(os.path.abspath(args.root), read_only=args.cmd == "standard" and args.lookup)
    if args.cmd == "standard":
        res = (standards.lookup(lib, args.task, args.set) if args.lookup else
               standards.standard(lib, args.task, args.set, False if args.no_gate else None))
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else res["text"])
        return 0 if res["found"] else 1
    if args.cmd == "sets":
        for n in sorted(lib.rule_nodes(), key=lambda x: x["id"]):
            if n["kind"] == "set":
                print(f"{n['id']}\t{n['title']}")
        return 0
    pkg = lib.package(args.id, standards.agent_target(lib), write=True, task=args.task)
    print(pkg["clipboard"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
