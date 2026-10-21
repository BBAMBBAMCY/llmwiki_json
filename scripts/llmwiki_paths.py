"""어느 데이터 root 를 쓰고, 그 안의 무엇이 어디에 있는가 — 모든 진입점이 이 모듈 하나로 정한다.

두 가지 배치를 지원한다.

단일 repo 모드 (하위 호환)
    코드와 데이터가 한 저장소다. 설정은 `tools/config/`, 파생물은 `index/` 와
    `viewer/public/data/` 에 둔다.

분리 모드
    데이터 repo 는 root 에 `llmwiki.lock` 을 갖는다. 그 안에 `wiki/ raw/ config/ LOCAL.md`
    를 두고, 파생물은 git 에서 빠지는 `.llmwiki/index/`·`.llmwiki/viewer-data/` 에 만든다.
    코드(스크립트·스키마·viewer)는 이 모듈이 들어 있는 코드 repo 에서 온다.

데이터 root 우선순위
    1. `--root` 인자
    2. 환경변수 `LLMWIKI_ROOT`
    3. 현재 디렉터리에서 위로 올라가며 처음 만난 `llmwiki.lock` 의 디렉터리
    4. 환경변수 `LLMWIKI_DEFAULT_ROOT` — `llmwiki` shim 이 설치 때 받은 `--data` 를 여기에 둔다
    5. 코드 repo 자신 (단일 repo 모드)

표준 라이브러리만 쓴다. viewer 는 `llmwiki.py paths` 로 같은 답을 받아 간다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

CODE_ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = "LLMWIKI_ROOT"
ENV_DEFAULT_ROOT = "LLMWIKI_DEFAULT_ROOT"
LOCK_NAME = "llmwiki.lock"
LOCK_VERSION = 1
DERIVED_DIR = ".llmwiki"
VERSION_FILE = "VERSION"

# 정본 page 의 스키마 판. 이 값은 여기 한 곳에만 둔다 — validate·ingest·migrate·lock 이 모두 이것을 본다.
# 바꿀 때는 llmwiki_migrate.MIGRATIONS 에 변환을, RELEASES 에 코드 판을 함께 넣는다.
SCHEMA_VERSION = "1.1"

# 데이터마다 다른 설정. 분리 모드에서는 `<data>/config/` 를 읽고, 없으면 `<code>/tools/config/` 의 기본값을 쓴다.
# 어휘(tags)·대역 사전(glossary)은 위키마다 다른 내용이라 기본값이 없다 — 남의 위키 어휘로 검사하면 안 된다.
CONFIG_DEFAULTS = {"groups.json": True, "context.json": True, "tags.json": False, "glossary.json": False}
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def find_lock(start: str | os.PathLike[str] | None = None) -> Path | None:
    """start(기본: 현재 디렉터리)에서 위로 올라가며 처음 만난 `llmwiki.lock`. 없으면 None."""
    try:
        here = Path(start or os.getcwd()).resolve()
    except OSError:
        return None
    for folder in (here, *here.parents):
        candidate = folder / LOCK_NAME
        if candidate.is_file():
            return candidate
    return None


def resolve_root(explicit: str | os.PathLike[str] | None = None, *,
                 cwd: str | os.PathLike[str] | None = None) -> Path:
    """데이터 root. 우선순위는 모듈 설명 그대로다."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser().resolve()
    lock = find_lock(cwd)
    if lock is not None:
        return lock.parent
    default = os.environ.get(ENV_DEFAULT_ROOT)
    if default:
        return Path(default).expanduser().resolve()
    return CODE_ROOT


def root_source(explicit: str | os.PathLike[str] | None = None, *,
                cwd: str | os.PathLike[str] | None = None) -> str:
    """resolve_root 가 어느 단계에서 답을 냈는가 (doctor·verify 가 보여 준다)."""
    if explicit:
        return "--root"
    if os.environ.get(ENV_ROOT):
        return ENV_ROOT
    if find_lock(cwd) is not None:
        return LOCK_NAME
    if os.environ.get(ENV_DEFAULT_ROOT):
        return ENV_DEFAULT_ROOT
    return "code"


def is_data_repo(root: str | os.PathLike[str]) -> bool:
    return (Path(root) / LOCK_NAME).is_file()


class Layout:
    """한 데이터 root 의 경로표. 분리 여부는 root 에 `llmwiki.lock` 이 있는가로만 정한다."""

    def __init__(self, root: str | os.PathLike[str] | None = None, *, code: Path | None = None) -> None:
        self.root = Path(root).resolve() if root else resolve_root()
        self.code = (code or CODE_ROOT).resolve()
        self.split = is_data_repo(self.root)

    # ----------------------------------------------------------- 정본
    @property
    def wiki(self) -> Path:
        return self.root / "wiki"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def fixtures(self) -> Path:
        """시험용 page 묶음. 코드에 딸린 것이라 분리 모드에서는 코드 repo 에서 읽는다."""
        return (self.code if self.split else self.root) / "tests" / "fixtures" / "pages"

    @property
    def schema(self) -> Path:
        base = self.code if self.split or not (self.root / "tools" / "schema").is_dir() else self.root
        return base / "tools" / "schema" / "page.schema.json"

    # ----------------------------------------------------------- 설정
    @property
    def config_dir(self) -> Path:
        """이 데이터의 설정 디렉터리 — 쓸 때는 언제나 여기다."""
        return self.root / "config" if self.split else self.root / "tools" / "config"

    @property
    def default_config_dir(self) -> Path:
        return self.code / "tools" / "config"

    def config(self, name: str) -> Path:
        """읽을 설정 파일. 데이터 쪽에 있으면 그것, 없으면 기본값이 있는 설정만 코드 쪽 기본값.

        돌려준 경로가 없을 수도 있다 — 그때는 그 설정이 없는 것이다.
        """
        own = self.config_dir / name
        if own.is_file() or not CONFIG_DEFAULTS.get(name, False):
            return own
        default = self.default_config_dir / name
        return default if default.is_file() else own

    # ----------------------------------------------------------- 파생물
    @property
    def derived(self) -> Path:
        return self.root / DERIVED_DIR

    @property
    def index(self) -> Path:
        return self.derived / "index" if self.split else self.root / "index"

    @property
    def viewer_data(self) -> Path:
        return self.derived / "viewer-data" if self.split else self.root / "viewer" / "public" / "data"

    @property
    def lock(self) -> Path:
        return self.root / LOCK_NAME

    def describe(self) -> dict[str, Any]:
        """viewer·doctor 가 받아 가는 경로표. 모두 절대경로 문자열이다."""
        return {
            "root": str(self.root), "code": str(self.code), "mode": "split" if self.split else "single",
            "wiki": str(self.wiki), "raw": str(self.raw), "config": str(self.config_dir),
            "index": str(self.index), "viewer_data": str(self.viewer_data),
            "schema_version": SCHEMA_VERSION, "code_version": code_version(self.code),
        }


# --------------------------------------------------------------------------- 판(version)
def code_version(code: Path | None = None) -> str:
    """코드 repo 루트의 VERSION (semver). 없으면 0.0.0."""
    try:
        text = ((code or CODE_ROOT) / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return text if SEMVER.match(text) else "0.0.0"


def semver(text: str) -> tuple[int, int, int]:
    match = SEMVER.match(text.strip())
    if not match:
        raise ValueError(f"semver 가 아니다: {text!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def schema_key(text: str) -> tuple[int, ...]:
    """schema_version("1.0") 비교용 열쇠. 숫자가 아니면 맨 앞으로 간다."""
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return (-1,)


# --------------------------------------------------------------------------- lock
def read_lock(root: str | os.PathLike[str]) -> dict[str, Any] | None:
    path = Path(root) / LOCK_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def lock_payload(*, schema_version: str, code_commit: str, code: Path | None = None,
                 updated: str | None = None) -> dict[str, Any]:
    code = (code or CODE_ROOT).resolve()
    return {"lock_version": LOCK_VERSION, "schema_version": schema_version,
            "code_version": code_version(code), "code_commit": code_commit,
            "code_path": str(code), "updated": updated or today()}


def write_lock(root: str | os.PathLike[str], payload: dict[str, Any]) -> bool:
    """lock 을 쓴다. 내용이 같으면 쓰지 않고 False."""
    path = Path(root) / LOCK_NAME
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def today() -> str:
    override = os.environ.get("LLMWIKI_NOW")
    return override[:10] if override else dt.date.today().isoformat()
