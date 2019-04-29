#!/usr/bin/env python3

"""
slixmppd
"""

import sys
import time
import asyncio
import html
import re

from threading import Thread, Lock, Event

from slixmpp import ClientXMPP
# from slixmpp.exceptions import IqError, IqTimeout


import based

# dictionary for all xmpp client connections
CONNECTIONS = {}
THREADS = []


class NuqqlClient(ClientXMPP):
    """
    Nuqql Client Class, derived from Slixmpp Client
    """

    def __init__(self, jid, password, lock):
        ClientXMPP.__init__(self, jid, password)

        # event handlers
        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        self.add_event_handler("groupchat_message", self.muc_message)

        self.lock = lock
        self.buddies = []
        self.queue = []
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
            # if message contains a timestamp, use it
            tstamp = msg['delay']['stamp']
            if tstamp:
                # convert to timestamp in seconds
                tstamp = tstamp.timestamp()
            else:
                # if there is no timestamp in message, use current time
                tstamp = time.time()

            # save timestamp and message in messages list and history
            tstamp = int(tstamp)
            self.lock.acquire()
            self.messages.append((tstamp, msg))
            self.history.append((tstamp, msg))
            self.lock.release()

    def muc_message(self, msg):
        """
        Groupchat message handler.
        """
        # TODO: if we do nothing extra here, move it into normal message
        # handler above?

        if msg['type'] == 'groupchat':
            # if message contains a timestamp, use it
            tstamp = msg['delay']['stamp']
            if tstamp:
                # convert to timestamp in seconds
                tstamp = tstamp.timestamp()
            else:
                # if there is no timestamp in message, use current time
                tstamp = time.time()

            # save timestamp and message in messages list and history
            tstamp = int(tstamp)
            self.lock.acquire()
            self.messages.append((tstamp, msg))
            self.history.append((tstamp, msg))
            self.lock.release()

    def collect(self):
        """
        Collect all messages from message log
        """

        self.lock.acquire()
        # create a copy of the history
        history = self.history[:]
        self.lock.release()

        # return the copy of the history
        return history

    def get_messages(self):
        """
        Read incoming messages
        """

        self.lock.acquire()
        # create a copy of the message list, and flush the message list
        messages = self.messages[:]
        self.messages = []
        self.lock.release()

        # return the copy of the message list
        return messages

    def enqueue_message(self, message_tuple):
        """
        Enqueue a message tuple in the message queue
        Tuple consists of:
            jid, msg, html_msg, msg_type
        """

        self.lock.acquire()
        # just add message tuple to queue
        self.queue.append(message_tuple)
        self.lock.release()

    def send_queue(self):
        """
        Send all queued messages
        """

        self.lock.acquire()
        for message_tuple in self.queue:
            # create message from message tuple and send it
            jid, msg, html_msg, mtype = message_tuple
            self.send_message(mto=jid, mbody=msg, mhtml=html_msg, mtype=mtype)
        # flush queue
        self.queue = []
        self.lock.release()

    def update_buddies(self):
        """
        Create a "safe" copy of roster
        """

        self.lock.acquire()
        # flush buddy list
        self.buddies = []

        # get buddies from roster
        for jid in self.client_roster.keys():
            alias = self.client_roster[jid]["name"]
            connections = self.client_roster.presence(jid)
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

            # check if it is a muc
            if jid in self.plugin['xep_0045'].get_joined_rooms():
                # use special status for group chats
                status = "GROUP_CHAT"

            # add buddies to buddy list
            buddy = based.Buddy(name=jid, alias=alias, status=status)
            self.buddies.append(buddy)
        self.lock.release()


def update_buddies(account):
    """
    Read buddies from client connection
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return

    # clear buddy list
    account.buddies = []

    # parse buddy list and insert buddies into buddy list
    xmpp.lock.acquire()
    for buddy in xmpp.buddies:
        account.buddies.append(buddy)
    xmpp.lock.release()


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
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return []

    # get messages
    messages = xmpp.get_messages()

    # format and return them
    return format_messages(account, messages)


def collect_messages(account):
    """
    Collect all messages from client connection
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return []

    # collect messages
    messages = xmpp.collect()

    # format and return them
    return format_messages(account, messages)


def send_message(account, jid, msg, msg_type="chat"):
    """
    send a message to a jabber id on an account
    """

    try:
        xmpp = CONNECTIONS[account.aid]
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
    xmpp.enqueue_message((jid, msg, html_msg, msg_type))


def set_status(account, status):
    """
    Set the current status of the account
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return

    xmpp.send_presence(pshow=status)


def get_status(account):
    """
    Get the current status of the account
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return ""

    connections = xmpp.client_roster.presence(xmpp.boundjid)
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

    return status


def chat_list(account):
    """
    List active chats of account
    """

    ret = []
    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return ret

    for chat in xmpp.plugin['xep_0045'].get_joined_rooms():
        nick = xmpp.plugin['xep_0045'].our_nicks[chat]
        ret.append("chat: list: {} {} {}".format(account.aid, chat, nick))

    return ret


def chat_join(account, chat, nick=""):
    """
    Join chat on account
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return ""

    if nick == "":
        nick = xmpp.jid
    xmpp.plugin['xep_0045'].join_muc(chat,
                                     nick,
                                     # If a room password is needed, use:
                                     # password=the_room_password,
                                     wait=True)
    return ""


def chat_part(account, chat):
    """
    Leave chat on account
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return ""

    if chat in xmpp.plugin['xep_0045'].get_joined_rooms():
        nick = xmpp.plugin['xep_0045'].our_nicks[chat]
        xmpp.plugin['xep_0045'].leave_muc(chat, nick)
    return ""


def chat_send(account, chat, msg):
    """
    Send message to chat on account
    """

    send_message(account, chat, msg, msg_type="groupchat")
    return ""


def chat_users(account, chat):
    """
    Get list of users in chat on account
    """

    ret = []
    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return ret

    roster = xmpp.plugin['xep_0045'].get_roster(chat)
    if not roster:
        return ret

    for user in roster:
        if user == "":
            continue
        ret.append("chat: user: {} {}".format(account.aid, user))

    return ret


def run_client(account, ready, running):
    """
    Run client connection in a new thread,
    as long as running Event is set to true.
    """

    # get event loop for thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # create a new lock for the thread
    lock = Lock()

    # start client connection
    xmpp = NuqqlClient(account.user, account.password, lock)
    xmpp.register_plugin('xep_0071')    # XHTML-IM
    xmpp.register_plugin('xep_0082')    # XMPP Date and Time Profiles
    xmpp.register_plugin('xep_0203')    # Delayed Delivery, time stamps
    xmpp.register_plugin('xep_0030')    # Service Discovery
    xmpp.register_plugin('xep_0045')    # Multi-User Chat
    xmpp.register_plugin('xep_0199')    # XMPP Ping
    xmpp.connect()

    # save client connection in active connections dictionary
    CONNECTIONS[account.aid] = xmpp

    # thread is ready to enter main loop, inform caller
    ready.set()

    # enter main loop, and keep running until "running" is set to false
    # by the KeyboardInterrupt
    while running.is_set():
        # process xmpp client for 0.1 seconds, then send pending outgoing
        # messages and update the (safe copy of the) buddy list
        xmpp.process(timeout=0.1)
        xmpp.send_queue()
        xmpp.update_buddies()


def add_account(account):
    """
    Add a new account (from based) and run a new slixmpp client thread for it
    """

    # event to signal thread is ready
    ready = Event()

    # event to signal if thread should stop
    running = Event()
    running.set()

    # create and start thread
    new_thread = Thread(target=run_client, args=(account, ready, running))
    new_thread.start()

    # save thread in active threads dictionary
    THREADS.append((new_thread, running))

    # wait until thread initialized everything
    ready.wait()


def main():
    """
    Main function, initialize everything and start server
    """

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
    based.CALLBACKS["set_status"] = set_status
    based.CALLBACKS["get_status"] = get_status
    based.CALLBACKS["chat_list"] = chat_list
    based.CALLBACKS["chat_join"] = chat_join
    based.CALLBACKS["chat_part"] = chat_part
    based.CALLBACKS["chat_send"] = chat_send
    based.CALLBACKS["chat_users"] = chat_users

    # run the server for the nuqql connection
    try:
        based.run_server(based.ARGS)
    except KeyboardInterrupt:
        # try to terminate all threads
        for thread, running in THREADS:
            running.clear()
            thread.join()
        sys.exit()


if __name__ == '__main__':
    main()
