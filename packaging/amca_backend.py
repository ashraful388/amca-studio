"""PyInstaller entry point for the loopback AMCA desktop service."""

from amca.service import main


if __name__ == "__main__":
    raise SystemExit(main())
