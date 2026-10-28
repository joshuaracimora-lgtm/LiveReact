"""PyInstaller entry point: run the package's ``__main__`` as an import (it uses relative imports)."""
from livereact.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
