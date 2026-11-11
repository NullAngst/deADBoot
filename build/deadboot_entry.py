"""PyInstaller entry point for the Windows build."""

import sys

from deadboot.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
