#!/usr/bin/env python3

"""
slixmppd main entry point
"""

import asyncio

# slixmppd
from nuqql_slixmppd.server import BackendServer


async def _main() -> None:
    """
    Main function, initialize everything and start server
    """

    server = BackendServer()
    await server.start()


def main() -> None:
    """
    Main entry point
    """

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        return


if __name__ == '__main__':
    main()
