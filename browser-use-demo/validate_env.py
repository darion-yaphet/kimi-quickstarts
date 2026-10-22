#!/usr/bin/env python3
"""Validate environment configuration before startup."""

import os
import sys
from pathlib import Path

# Import constants for display information
try:
    from browser_use_demo.display_constants import BROWSER_WIDTH, BROWSER_HEIGHT, DISPLAY_WIDTH, DISPLAY_HEIGHT
except ImportError:
    # Fallback if running outside the package
    DISPLAY_WIDTH = 1920
    DISPLAY_HEIGHT = 1080
    BROWSER_WIDTH = 1920
    BROWSER_HEIGHT = 1080


def validate_env():
    """Validate required environment variables are set."""
    # Check API key
    api_key = os.environ.get("KIMI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        print("\n" + "=" * 60)
        print("ERROR: Missing required configuration!")
        print("=" * 60)
        print("\nThe Browser Use Demo requires proper configuration to run.")
        print("\n🔧 RECOMMENDED: Use docker-compose with a .env file:")
        print("  1. Copy the example environment file:")
        print("     cp .env.example .env")
        print("  2. Edit .env and add your Kimi Code API key")
        print("  3. Run with docker-compose:")
        print("     docker-compose up --build")
        print("=" * 60)
        sys.exit(1)

    if api_key in {"your_anthropic_api_key_here", "your_kimi_code_api_key_here"} or len(api_key) < 10:
        print("\n" + "=" * 60)
        print("ERROR: Invalid API key!")
        print("=" * 60)
        print("  KIMI_API_KEY: Must be a valid Kimi Code API key")
        print("\nTo fix this, please edit your .env file with a valid API key")
        print("=" * 60)
        sys.exit(1)

    print("\n✓ Environment validation passed")
    print(f"  Display: {DISPLAY_WIDTH}x{DISPLAY_HEIGHT}")
    print(f"  Browser: {BROWSER_WIDTH}x{BROWSER_HEIGHT}")


if __name__ == "__main__":
    validate_env()
