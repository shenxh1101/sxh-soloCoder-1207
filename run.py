#!/usr/bin/env python
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from file_renamer.cli import main

if __name__ == "__main__":
    main()