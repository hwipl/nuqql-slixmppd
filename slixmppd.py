#!/usr/bin/env python3

"""
Helper script for starting slixmppd
"""

import sys

import nuqql_slixmppd.slixmppd
import nuqql_slixmppd

# start slixmppd
sys.exit(nuqql_slixmppd.slixmppd.main())
