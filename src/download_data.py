"""Idempotent download of the LendingClub accepted-loans extract via kagglehub."""

import gzip
import shutil
import sys
from pathlib import Path

from src.config import get_settings

DATASET_HANDLE = "wordsforthewise/lending-club"
DATASET_FILE = "accepted_2007_to_2018Q4.csv.gz"
MIN_READY_BYTES = 100 * 1024 * 1024


def _gb(size_bytes: int) -> float:
    return size_bytes / (1024 ** 3)


def _require_token(token: str | None) -> None:
    if token and token.strip():
        return
    raise RuntimeError(
        "KAGGLE_API_TOKEN is not set. Add it to .env "
        "(see KAGGLE_API_TOKEN in .env.example; create the token at "
        "https://www.kaggle.com/settings/api) and rerun: python -m src.download_data"
    )


def _resolve_artifact(downloaded: Path) -> Path:
    if downloaded.is_file():
        return downloaded
    matches = sorted(downloaded.rglob("accepted_2007_to_2018Q4.csv*"))
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(path.name for path in matches) or "none"
    raise FileNotFoundError(
        f"kagglehub returned {downloaded}, but the accepted-loans file was not found ({names})."
    )


def _publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    if source.name.lower().endswith(".gz"):
        with gzip.open(source, "rb") as compressed, temporary.open("wb") as target:
            shutil.copyfileobj(compressed, target)
    elif source.suffix.lower() == ".csv":
        shutil.copyfile(source, temporary)
    else:
        raise RuntimeError(
            f"Unexpected Kaggle artifact '{source.name}'. Expected a .csv or .csv.gz file."
        )
    temporary.replace(destination)


def main() -> None:
    settings = get_settings()
    destination = settings.raw_data_path
    if destination.is_file() and destination.stat().st_size > MIN_READY_BYTES:
        size_gb = _gb(destination.stat().st_size)
        print(f"Dataset already ready at {destination} ({size_gb:.2f} GB). Skipping download.")
        return

    _require_token(settings.kaggle_api_token)

    import kagglehub

    downloaded = Path(
        kagglehub.dataset_download(DATASET_HANDLE, path=DATASET_FILE)
    )
    artifact = _resolve_artifact(downloaded)
    print(f"Kaggle artifact: {artifact}")
    _publish(artifact, destination)
    size_gb = _gb(destination.stat().st_size)
    print(f"Dataset written to {destination.resolve()} ({size_gb:.2f} GB).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
