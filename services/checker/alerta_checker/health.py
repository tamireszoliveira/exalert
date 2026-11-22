"""Probe de liveness do worker: sai com 0 se o heartbeat foi gravado há pouco.

O laço grava o arquivo a cada ciclo, com ou sem erro: a probe pega laço travado,
não API ou fonte fora do ar (reiniciar o pod não conserta essas).

    python -m alerta_checker.health --max-age 600
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confere se o worker gravou o heartbeat recentemente.")
    parser.add_argument("--file", default=os.environ.get("HEARTBEAT_FILE") or "/tmp/alerta-checker.heartbeat")
    parser.add_argument("--max-age", type=float, default=600, help="idade máxima em segundos (padrão: 600)")
    args = parser.parse_args(argv)
    try:
        age = time.time() - Path(args.file).stat().st_mtime
    except OSError:
        print(f"sem heartbeat em {args.file}")
        return 1
    if age > args.max_age:
        print(f"heartbeat parado há {age:.0f}s (máximo {args.max_age:g}s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
