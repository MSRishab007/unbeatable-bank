"""Generate Python gRPC code from proto/ into every service that uses it.

Run from the repo root after changing a .proto file:
    pip install grpcio-tools==1.68.1
    python scripts/gen_protos.py

The generated files are committed, so Docker builds don't need protoc.
"""

import shutil
import sys
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[1]
PROTO_DIR = ROOT / "proto"

# proto file -> services that need its generated code
TARGETS = {
    "ledger/v1/ledger.proto": ["ledger", "intake"],
}

INIT = '''"""Generated gRPC code. Do not edit; run scripts/gen_protos.py.

The generated modules import each other by top-level package name
(e.g. `from ledger.v1 import ledger_pb2`), so this folder is put on sys.path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
'''


def main() -> int:
    for proto, services in TARGETS.items():
        for service in services:
            out = ROOT / "services" / service / "app" / "gen"
            out.mkdir(parents=True, exist_ok=True)
            (out / "__init__.py").write_text(INIT)
            rc = protoc.main([
                "grpc_tools.protoc",
                f"-I{PROTO_DIR}",
                f"--python_out={out}",
                f"--pyi_out={out}",
                f"--grpc_python_out={out}",
                str(PROTO_DIR / proto),
            ])
            if rc != 0:
                print(f"protoc failed for {proto} -> {service}", file=sys.stderr)
                return rc
            # make every generated folder an importable package
            for d in out.rglob("*"):
                if d.is_dir() and d.name != "__pycache__":
                    (d / "__init__.py").touch()
            shutil.rmtree(out / "__pycache__", ignore_errors=True)
            print(f"generated {proto} -> services/{service}/app/gen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
