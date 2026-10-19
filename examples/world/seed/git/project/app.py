"""Platform service entry point."""
from __future__ import annotations

from rates import convert


def main() -> None:
    print(convert(100.0, "USD", "EUR"))


if __name__ == "__main__":
    main()
