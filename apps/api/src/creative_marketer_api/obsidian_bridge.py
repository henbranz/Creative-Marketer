import argparse
import json
import sys

from creative_marketer.infrastructure.obsidian import ObsidianBridge, ObsidianBridgeConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize Creative Marketer into Obsidian")
    parser.add_argument("--full", action="store_true", help="perform a full authoritative rebuild")
    parser.add_argument(
        "--watch", action="store_true", help="continuously apply projection changes"
    )
    parser.add_argument("--setup", action="store_true", help="validate local bridge configuration")
    parser.add_argument(
        "--interval", type=float, default=4.0, help="watch poll interval in seconds"
    )
    arguments = parser.parse_args()
    bridge = ObsidianBridge(ObsidianBridgeConfig.from_environment())
    if arguments.watch:
        bridge.watch(interval_seconds=arguments.interval)
        return
    result = bridge.validate_setup() if arguments.setup else bridge.sync(full=arguments.full)
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
