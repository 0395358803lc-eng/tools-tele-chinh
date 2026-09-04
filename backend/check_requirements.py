"""Exit non-zero unless every exact requirement in requirements.lock is installed."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def main() -> int:
    for raw in (Path(__file__).with_name("requirements.lock")).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, expected = line.partition("==")
        if not separator:
            return 1
        try:
            if version(name) != expected:
                return 1
        except PackageNotFoundError:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
