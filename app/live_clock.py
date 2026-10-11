#!/usr/bin/env python3
"""Planet Time launcher."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from liveclock.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
