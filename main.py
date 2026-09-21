"""Entry point for the packaged app and `python main.py`."""
import multiprocessing
import sys

from landscan.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
