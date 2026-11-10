"""Generate Python gRPC code from proto/ into the shared library (libs/common/ubproto).

Run from the repo root after changing a .proto file:
    pip install grpcio-tools==1.68.1
    python scripts/gen_protos.py

The generated files are committed, so Docker builds don't need protoc.
Every service imports them as e.g. `from ubproto.ledger.v1 import ledger_pb2`.
"""

import shutil
import sys
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[1]
PROTO_DIR = ROOT / "proto"
OUT_DIR = ROOT / "libs" / "common"


def main() -> int:
    shutil.rmtree(OUT_DIR / "ubproto", ignore_errors=True)
    protos = sorted(str(p) for p in (PROTO_DIR / "ubproto").rglob("*.proto"))
    rc = protoc.main([
        "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"--python_out={OUT_DIR}",
        f"--pyi_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        *protos,
    ])
    if rc != 0:
        print("protoc failed", file=sys.stderr)
        return rc
    for d in [OUT_DIR / "ubproto", *(p for p in (OUT_DIR / "ubproto").rglob("*") if p.is_dir())]:
        (d / "__init__.py").touch()
    for p in protos:
        print(f"generated {Path(p).relative_to(PROTO_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
