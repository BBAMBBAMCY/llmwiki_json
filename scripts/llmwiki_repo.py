"""데이터 repo 관리 — lock.

데이터 repo(`llmwiki.lock` 이 있는 root)는 코드를 갖지 않는다. 이 모듈은 코드 repo 쪽에서 데이터 repo 를
맞춘다: 어느 코드(판·커밋·경로)로 맞췄는지 lock 에 적는다. 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

try:  # package import
    from . import llmwiki_paths as P
except ImportError:  # run as a script or loaded from its file path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import llmwiki_paths as P  # type: ignore[no-redef]


# --------------------------------------------------------------------------- git
def git(cwd: Path, *args: str, timeout: float = 120.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def is_own_repo(path: Path) -> bool:
    """path 가 그 자신의 git 저장소 최상위인가 (다른 저장소 안의 디렉터리가 아니라)."""
    code, out = git(path, "rev-parse", "--show-toplevel", timeout=15.0)
    if code != 0 or not out:
        return False
    return Path(out.splitlines()[-1]).resolve() == Path(path).resolve()


def code_commit(code: Path | None = None) -> str:
    """코드 repo 의 HEAD. git 저장소가 아니면 빈 문자열."""
    code = (code or P.CODE_ROOT).resolve()
    if not is_own_repo(code):
        return ""
    status, out = git(code, "rev-parse", "HEAD", timeout=15.0)
    return out.splitlines()[-1].strip() if status == 0 and out else ""


# --------------------------------------------------------------------------- lock
def refresh_lock(data: Path, *, schema_version: str | None = None, code: Path | None = None) -> dict[str, Any]:
    """lock 을 지금 코드에 맞춰 다시 쓴다. schema_version 을 주지 않으면 lock 에 있던 값(없으면 코드의 판).

    내용이 같으면 쓰지 않는다 — 날짜(updated)만 바뀌는 쓰기로 데이터 repo 를 더럽히지 않는다.
    """
    current = P.read_lock(data) or {}
    schema = schema_version or str(current.get("schema_version") or P.SCHEMA_VERSION)
    payload = P.lock_payload(schema_version=schema, code_commit=code_commit(code), code=code)
    same = {k: v for k, v in payload.items() if k != "updated"} == {k: v for k, v in current.items() if k != "updated"}
    if same and current.get("updated"):
        payload["updated"] = current["updated"]
    payload["changed"] = P.write_lock(data, {k: v for k, v in payload.items() if k != "changed"})
    return payload
