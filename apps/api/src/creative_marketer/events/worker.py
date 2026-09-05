"""Publisher entrypoint placeholder that fails closed until a transport is configured."""


def main() -> None:
    raise SystemExit(
        "No event transport is configured. Outbox events remain pending; "
        "configure an explicit transport adapter before enabling this worker."
    )


if __name__ == "__main__":
    main()
