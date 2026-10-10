#!/usr/bin/env python3
"""Download a third-party Lua source for local inspection only; never execute it."""
import hashlib
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/LSSOPS/OpenSource/refs/heads/main/SpeedHubX_Steal_An_Egg.lua"
DEST = Path(__file__).resolve().parent / "downloaded" / "SpeedHubX_Steal_An_Egg.lua"


def main():
    request = urllib.request.Request(URL, headers={"User-Agent": "FunPay-assistant-source-review/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000 or not data:
        raise ValueError("Неожиданный размер исходника")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(data)
    print(f"Сохранено: {DEST}\nSHA256: {hashlib.sha256(data).hexdigest()}")
    print("Сторонний исходник не проверен и не запускается ботом. Проверьте код и лицензию перед использованием.")


if __name__ == "__main__":
    main()
