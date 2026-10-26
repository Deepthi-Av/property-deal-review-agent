"""Build a local source ZIP from an explicit allowlist; exclude secrets and runtime files."""
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
files = [ROOT / name for name in ("README.md", "pyproject.toml", "uv.lock", ".env.example", ".gitignore", ".python-version")]
for directory in ("src", "app", "data", "docs", "scripts", "tests"):
    files.extend(p for p in (ROOT / directory).rglob("*") if p.is_file()
                 and "__pycache__" not in p.parts and p.suffix != ".pyc"
                 and p.resolve().is_relative_to(ROOT))
secrets = []
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    key, sep, value = line.partition("=")
    if sep and key.strip().endswith("_KEY"):
        value = value.strip().strip("\"'")
        if len(value) > 8:
            secrets.append(value.encode())
for file in files:
    if any(secret in file.read_bytes() for secret in secrets):
        raise SystemExit("Packaging stopped: a configured secret appears in an allowed file.")
destination = ROOT / "artifacts" / ("home-review-submission-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".zip")
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(files):
        archive.write(file, file.relative_to(ROOT).as_posix())
with zipfile.ZipFile(destination) as archive:
    names = archive.namelist()
    assert ".env" not in names and not any(name.startswith((".venv/", "artifacts/")) for name in names)
    assert archive.testzip() is None
print(f"Verified source files: {len(files)}; configured secret matches: 0")
print(destination)
