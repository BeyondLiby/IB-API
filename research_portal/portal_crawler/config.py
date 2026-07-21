from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PORTAL_URLS = {
    "jpm": "https://markets.jpmorgan.com/jpmm/research.my_subscriptions",
    "gs": "https://marquee.gs.com/s/home",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _first_existing(paths: list[Path]) -> Path | None:
    return next((path for path in paths if path.is_file()), None)


def detect_chrome() -> Path | None:
    configured = os.environ.get("RESEARCH_PORTAL_CHROME")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())

    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")

    return _first_existing(candidates)


@dataclass(frozen=True)
class Settings:
    project_root: Path
    local_dir: Path
    profile_dir: Path
    artifacts_dir: Path
    data_dir: Path
    chrome_path: Path | None
    cdp_url: str

    @classmethod
    def load(cls) -> "Settings":
        project_root = _project_root()
        local_dir = Path(
            os.environ.get("RESEARCH_PORTAL_LOCAL_DIR", project_root / ".local")
        ).expanduser().resolve()
        return cls(
            project_root=project_root,
            local_dir=local_dir,
            profile_dir=local_dir / "chrome-profile",
            artifacts_dir=local_dir / "artifacts",
            data_dir=Path(
                os.environ.get("RESEARCH_PORTAL_DATA_DIR", project_root / "data")
            ).expanduser().resolve(),
            chrome_path=detect_chrome(),
            cdp_url=os.environ.get("RESEARCH_PORTAL_CDP_URL", "http://127.0.0.1:9222"),
        )

    def ensure_local_dirs(self) -> None:
        for path in (self.local_dir, self.profile_dir, self.artifacts_dir, self.data_dir):
            path.mkdir(parents=True, exist_ok=True)

