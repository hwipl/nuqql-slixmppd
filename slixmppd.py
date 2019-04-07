#!/usr/bin/env python3

"""
slixmppd
"""

import sys
import time
import asyncio
import html
import re

from threading import Thread, Lock

from slixmpp import ClientXMPP
# from slixmpp.exceptions import IqError, IqTimeout


import based

# dictionary for all xmpp client connections
CONNECTIONS = {}


class NuqqlClient(ClientXMPP):
    """
    Nuqql Client Class, derived from Slixmpp Client
    """

    def __init__(self, jid, password):
        ClientXMPP.__init__(self, jid, password)

        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)

        self.history = []
        self.messages = []

    def session_start(self, _event):
        """
        Session start handler
        """

        # presence types and show types from slixmpp:
        # types = {'available', 'unavailable', 'error', 'probe', 'subscribe',
        #          'subscribed', 'unsubscribe', 'unsubscribed'}
        # showtypes = {'dnd', 'chat', 'xa', 'away'}
        # empty presence means "available"/"online", so that's ok.
        self.send_presence()
        self.get_roster()

    def message(self, msg):
        """
        Message handler
        """

        if msg['type'] in ('chat', 'normal'):
            tstamp = int(time.time())
            self.messages.append((tstamp, msg))
            self.history.append((tstamp, msg))

    def collect(self):
        """
        Collect all messages from message log
        """

        return self.history[:]

    def get_messages(self):
        """
        Read incoming messages
        """

        messages = self.messages[:]
        self.messages = []
        return messages


def update_buddies(account):
    """
    Read buddies from client connection
    """

    try:
        xmpp, lock = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return

    # clear buddy list
    account.buddies = []

    # parse buddy list and insert buddies into buddy list
    lock.acquire()
    for jid in xmpp.client_roster.keys():
        alias = xmpp.client_roster[jid]["name"]
        connections = xmpp.client_roster.presence(jid)
        status = "offline"
        # check all resources for presence information
        if connections:
            # if there is a connection, user is at least online
            status = "available"
        for pres in connections.values():
            # the optional status field shows additional info like
            # "I'm currently away from my computer" which is too long
            # if pres['status']:
            #     status = pres["status"]
            # if there is an optional show value, display it instead
            if pres['show']:
                status = pres['show']
        buddy = based.Buddy(name=jid, alias=alias, status=status)
        account.buddies.append(buddy)
    lock.release()


def format_messages(account, messages):
    """
    format messages for get_messages() and collect_messages()
    """

    ret = []
    for tstamp, msg in messages:
        # nuqql expects html-escaped messages; construct them
        msg_body = msg["body"]
        msg_body = html.escape(msg_body)
        msg_body = "<br/>".join(msg_body.split("\n"))
        ret_str = "message: {} {} {} {} {}".format(account.aid, msg["to"],
                                                   tstamp, msg["from"],
                                                   msg_body)
        ret.append(ret_str)
    return ret


def get_messages(account):
    """
    Read messages from client connection
    """

    try:
        xmpp, lock = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return []

    # get messages
    lock.acquire()
    messages = xmpp.get_messages()
    lock.release()

    # format and return them
    return format_messages(account, messages)


def collect_messages(account):
    """
    Collect all messages from client connection
    """

    try:
        xmpp, lock = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return []

    # collect messages
    lock.acquire()
    messages = xmpp.collect()
    lock.release()

    # format and return them
    return format_messages(account, messages)


def send_message(account, jid, msg):
    """
    send a message to a jabber id on an account
    """

    try:
        xmpp, lock = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return

    # nuqql sends a html-escaped message; construct "plain-text" version and
    # xhtml version using nuqql's message and use them as message body later
    html_msg = \
        '<body xmlns="http://www.w3.org/1999/xhtml">{}</body>'.format(msg)
    msg = html.unescape(msg)
    msg = "\n".join(re.split("<br/>", msg, flags=re.IGNORECASE))

    # send message
    lock.acquire()
    xmpp.send_message(mto=jid, mbody=msg, mhtml=html_msg, mtype='chat')
    lock.release()


def run_client(account):
    """
    Run client connection in a new thread
    """

    # get event loop for thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # start client connection
    xmpp = NuqqlClient(account.user, account.password)
    xmpp.register_plugin('xep_0071')
    xmpp.connect()

    lock = Lock()
    CONNECTIONS[account.aid] = (xmpp, lock)

    # enter main loop
    while True:
        lock.acquire()
        xmpp.process(timeout=0.1)
        lock.release()
        # give other thread some time to get the lock
        time.sleep(0.1)


def add_account(account):
    """
    Add a new account (from based) and run a new slixmpp client thread for it
    """

    new_thread = Thread(target=run_client, args=(account,))
    new_thread.start()

    # hacky: give thread some time initialize everything
    # TODO: wait for some event?
    time.sleep(0.1)


if __name__ == '__main__':
    # parse command line arguments
    based.get_command_line_args()

    # load accounts
    based.load_accounts()

    # initialize loggers
    based.init_loggers()

    # start a client connection for every xmpp account in it's own thread
    for acc in based.ACCOUNTS.values():
        if acc.type == "xmpp":
            add_account(acc)

    # register callbacks
    based.CALLBACKS["add_account"] = add_account
    based.CALLBACKS["update_buddies"] = update_buddies
    based.CALLBACKS["get_messages"] = get_messages
    based.CALLBACKS["send_message"] = send_message
    based.CALLBACKS["collect_messages"] = collect_messages

    # run the server for the nuqql connection
    try:
        based.run_server(based.ARGS)
    except KeyboardInterrupt:
        sys.exit()
