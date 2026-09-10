"""Build-time download of the exact Chrome archive used in the server trial."""
import hashlib
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

URL = "https://storage.googleapis.com/chrome-for-testing-public/152.0.7977.82/linux64/chrome-linux64.zip"
SHA256 = "0704631fb3e4f741092e08f55272f90abc3e307f991f05f332924364415b02e0"

if __name__ == "__main__":
    archive = Path("/tmp/chemsource-chrome.zip")
    with urllib.request.urlopen(URL, timeout=120) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != SHA256:
        raise RuntimeError("Chrome archive checksum mismatch")
    root = Path("/opt/chrome")
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if not (root / item.filename).resolve().is_relative_to(root):
                raise ValueError("Unexpected archive path")
        package.extractall(root)
        for item in package.infolist():
            mode = (item.external_attr >> 16) & 0o777
            if mode:
                os.chmod(root / item.filename, mode)
    archive.unlink()
