"""Entry point for the packaged application."""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_esc_tool.gui import main

if __name__ == "__main__":
    sys.exit(main() or 0)
