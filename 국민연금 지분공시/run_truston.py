from __future__ import annotations

import sys

from ps_disclosure_report import main


if __name__ == "__main__":
    sys.argv = [
        sys.argv[0],
        "--submitter",
        "트러스톤",
        "--label",
        "트러스톤",
    ]
    raise SystemExit(main())
