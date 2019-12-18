#!/usr/bin/env python3

"""
slixmppd main entry point
"""

# slixmppd
from nuqql_slixmppd.server import BackendServer


def main() -> None:
    """
    Main function, initialize everything and start server
    """

    server = BackendServer()
    server.start()


if __name__ == '__main__':
    main()
