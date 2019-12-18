#!/usr/bin/env python3

"""
Helper script for starting slixmppd
"""

import sys

import nuqql_slixmppd.main
import nuqql_slixmppd

# start slixmppd
sys.exit(nuqql_slixmppd.main.main())
