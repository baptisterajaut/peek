import os
import sys

# Match daemon.py: force xwayland so window position is honored.
# Set BEFORE Qt is imported anywhere down the chain.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from peek.daemon import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
