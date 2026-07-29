""".env 파일 in-place 수정 유틸 — 주석/포맷 보존, 지정 키만 upsert/remove."""

from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"


def upsert_key(key: str, value: str, path: Path | None = None) -> None:
    """key=value 줄을 추가하거나(없으면 파일 끝에), 있으면 값만 교체."""
    path = path or ENV_PATH
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix) or line.strip().startswith(f"# {prefix}"):
            lines[i] = f"{key}={value}"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_key(key: str, path: Path | None = None) -> bool:
    """key= 로 시작하는 줄 제거. 실제로 지웠으면 True."""
    path = path or ENV_PATH
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    new_lines = [l for l in lines if not (l.strip().startswith(prefix) or l.strip().startswith(f"# {prefix}"))]
    if len(new_lines) == len(lines):
        return False
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True
