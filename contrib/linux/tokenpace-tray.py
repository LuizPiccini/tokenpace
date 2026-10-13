#!/usr/bin/python3
"""Run with distro Python so GTK introspection bindings are available."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tokenpace.__main__ import main
sys.exit(main(["tray", *sys.argv[1:]]))
