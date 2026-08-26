from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    """DEPRECATED: thin shell over `bilibili_get grab-targets` (M2 unification).

    Kept temporarily for CLI compatibility; will be removed in a later release.
    Note: pipeline defaults now follow `bilibili_get run` conventions
    (download=audio,subtitles,cover; snapshots=smart); pruning is no longer
    forced — pass --prune-output explicitly.
    """
    repo_dir = Path(__file__).resolve().parents[1]
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    from bilibili_get.cli import main as cli_main

    argv = list(sys.argv[1:] if argv is None else argv)
    sys.stdout.buffer.write(
        "[DEPRECATED] collect_targets.py -> `python -m bilibili_get grab-targets`\n".encode("utf-8")
    )
    cli_main(["grab-targets"] + argv)


if __name__ == "__main__":
    main()
