import sys

if sys.version_info < (3, 9):
    sys.exit("scalpscan требует Python 3.9+ (на macOS: python.org или `brew install python`), "
             f"сейчас {sys.version.split()[0]}")

from .cli import main  # noqa: E402

sys.exit(main())
