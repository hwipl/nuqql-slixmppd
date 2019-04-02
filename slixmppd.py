"""
slixmppd
"""

import sys
import getpass

from slixmpp import ClientXMPP
# from slixmpp.exceptions import IqError, IqTimeout


class NuqqlClient(ClientXMPP):
    def __init__(self, jid, password):
        ClientXMPP.__init__(self, jid, password)

        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)

    def session_start(self, event):
        print("session start")
        self.send_presence()
        self.get_roster()

    def message(self, msg):
        print("message received: type {0}".format(msg["type"]))
        if msg['type'] in ('chat', 'normal'):
            print(msg["body"])
        print("roster:")
        print(self.client_roster)


if __name__ == '__main__':
    # Ideally use optparse or argparse to get JID,
    # password, and log level.
    print(sys.argv[1])
    xmpp = NuqqlClient(sys.argv[1], getpass.getpass())
    # xmpp.connect(use_ssl=True)
    xmpp.connect()
    xmpp.process(forever=True)
