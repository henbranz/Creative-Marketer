import argparse
import json
import sys

from creative_marketer.infrastructure.obsidian import ObsidianBridge, ObsidianBridgeConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize Creative Marketer into Obsidian")
    parser.add_argument("--full", action="store_true", help="perform a full authoritative rebuild")
    arguments = parser.parse_args()
    result = ObsidianBridge(ObsidianBridgeConfig.from_environment()).sync(full=arguments.full)
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
