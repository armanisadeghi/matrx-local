"""Refuse hosted CI before paid runners start without a named requester."""

import os
import sys


def main() -> int:
    if not os.environ.get("REQUESTED_BY", "").strip():
        print(
            "::error::CI dispatch requires a nonblank requested_by (agent session or lane)."
        )
        return 1
    print("CI dispatch attribution: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
