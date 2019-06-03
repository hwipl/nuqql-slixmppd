#!/usr/bin/env python3

"""
slixmppd
"""

import sys
import time
import asyncio
import html
import re
import unicodedata
import logging
import stat
import os


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

    def __init__(self, account, lock):
        # jid: account.user
        # password: account.password
        ClientXMPP.__init__(self, account.user, account.password)
        self.account = account

        # event handlers
        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        self.add_event_handler("groupchat_message", self.muc_message)
        self.add_event_handler("groupchat_invite", self._muc_invite)

        self.lock = lock
        self.buddies = []
        self.queue = []
        self.history = []
        self.messages = []

        self.muc_invites = {}
        self.muc_cache = []
        self.muc_filter_own = True

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
            formated_msg = format_message(self.account, tstamp, msg)
            self.lock.acquire()
            self.messages.append(formated_msg)
            self.history.append(formated_msg)
            self.lock.release()

    def muc_message(self, msg):
        """
        Groupchat message handler.
        """
        # TODO: if we do nothing extra here, move it into normal message
        # handler above?

        if msg['type'] == 'groupchat':
            # filter own messages
            chat = msg['from'].bare
            nick = self.plugin['xep_0045'].our_nicks[chat]
            if self.muc_filter_own and msg['mucnick'] == nick:
                return
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
            formated_msg = format_message(self.account, tstamp, msg)
            self.lock.acquire()
            self.messages.append(formated_msg)
            self.history.append(formated_msg)
            self.lock.release()

    def _muc_presence(self, presence, status):
        """
        Group chat presence handler
        """

        # get chat and our nick in the chat
        chat = presence["from"].bare
        nick = self.plugin['xep_0045'].our_nicks[chat]
        if presence['muc']['nick'] == "" and presence["muc"]["role"] == "":
            return

        if presence['muc']['nick'] != nick:
            user = presence["muc"]["nick"]
            user_alias = user   # try to get a real alias?
            msg = "chat: user: {} {} {} {} {}".format(
                self.account.aid, chat, user, user_alias, status)
            self.lock.acquire()
            # append to messages only
            self.messages.append(msg)
            self.lock.release()

    def _muc_invite(self, inv):
        """
        Group chat invite handler
        """

        user = inv["to"]
        chat = inv["from"]
        self.lock.acquire()
        self.muc_invites[chat] = (user, chat)
        self.lock.release()

    def muc_online(self, presence):
        """
        Group chat online presence handler
        """

        self._muc_presence(presence, "online")

    def muc_offline(self, presence):
        """
        Group chat offline presence handler
        """

        self._muc_presence(presence, "offline")

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

    def _send_message(self, message_tuple):
        """
        Send a queued message
        """

        # create message from message tuple and send it
        jid, msg, html_msg, mtype = message_tuple
        # remove control characters from message
        # TODO: do it in based/for all backends?
        msg = "".join(ch for ch in msg if ch == "\n" or
                      unicodedata.category(ch)[0] != "C")
        html_msg = "".join(ch for ch in html_msg if
                           unicodedata.category(ch)[0] != "C")
        self.send_message(mto=jid, mbody=msg, mhtml=html_msg, mtype=mtype)

    def enqueue_command(self, cmd, params):
        """
        Enqueue a command to the queue consisting of:
            command and its parameters
        """

        self.lock.acquire()
        # just add message tuple to queue
        self.queue.append((cmd, params))
        self.lock.release()

    def handle_queue(self):
        """
        Send all queued messages
        """

        self.lock.acquire()
        for cmd, params in self.queue:
            if cmd == "message":
                self._send_message(params)
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
            elif jid in self.muc_cache:
                # this is a muc and we are not in it any more, filter it
                continue

            # add buddies to buddy list
            buddy = based.Buddy(name=jid, alias=alias, status=status)
            self.buddies.append(buddy)

            # cleanup invites
            if jid in self.muc_invites:
                del self.muc_invites[jid]

        # handle pending invites as buddies
        for invite in self.muc_invites.values():
            _user, chat = invite
            buddy = based.Buddy(name=chat, alias=chat,
                                status="GROUP_CHAT_INVITE")
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


def format_message(account, tstamp, msg):
    """
    format messages for get_messages() and collect_messages()
    """

    # nuqql expects html-escaped messages; construct them
    msg_body = msg["body"]
    msg_body = html.escape(msg_body)
    msg_body = "<br/>".join(msg_body.split("\n"))
    ret_str = "message: {} {} {} {} {}".format(account.aid, msg["to"], tstamp,
                                               msg["from"], msg_body)
    return ret_str


def get_messages(account):
    """
    Read messages from client connection
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return []

    # get and return messages
    return xmpp.get_messages()


def collect_messages(account):
    """
    Collect all messages from client connection
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return []

    # collect and return messages
    return xmpp.collect()


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
    xmpp.enqueue_command("message", (jid, msg, html_msg, msg_type))


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
        chat_alias = chat   # TODO: use something else as alias?
        nick = xmpp.plugin['xep_0045'].our_nicks[chat]
        ret.append("chat: list: {} {} {} {}".format(account.aid, chat,
                                                    chat_alias, nick))

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
    xmpp.add_event_handler("muc::%s::got_online" % chat, xmpp.muc_online)
    xmpp.add_event_handler("muc::%s::got_offline" % chat, xmpp.muc_offline)
    xmpp.muc_cache.append(chat)
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

    # chat already joined
    if chat in xmpp.plugin['xep_0045'].get_joined_rooms():
        nick = xmpp.plugin['xep_0045'].our_nicks[chat]
        xmpp.plugin['xep_0045'].leave_muc(chat, nick)
        xmpp.del_event_handler("muc::%s::got_online" % chat, xmpp.muc_online)
        xmpp.del_event_handler("muc::%s::got_offline" % chat, xmpp.muc_offline)
        # keep muc in muc_cache to filter it from buddy list

    # chat not joined yet, remove pending invite
    if chat in xmpp.muc_invites:
        # simply ignore the pending invite and remove it
        del xmpp.muc_invites[chat]

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
        # TODO: try to retrieve proper alias
        user_alias = user
        # TODO: try to retrieve user's presence as status?
        status = "join"
        ret.append("chat: user: {} {} {} {} {}".format(
            account.aid, chat, user, user_alias, status))

    return ret


def chat_invite(account, chat, user):
    """
    Invite user to chat on account
    """

    try:
        xmpp = CONNECTIONS[account.aid]
    except KeyError:
        # no active connection
        return ""

    xmpp.plugin['xep_0045'].invite(chat, user)
    return ""


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
    xmpp = NuqqlClient(account, lock)
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
        xmpp.handle_queue()
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


def init_logging():
    """
    Configure logging module, so slixmpp logs are written to a file
    """

    # determine logging path from command line parameters
    log_file = based.ARGS.dir + "/logs/slixmpp.log"

    # configure logging module to write to file
    log_format = "%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s"
    logging.basicConfig(filename=log_file, level=logging.DEBUG,
                        format=log_format, datefmt="%s")
    os.chmod(log_file, stat.S_IRWXU)


def main():
    """
    Main function, initialize everything and start server
    """

    # parse command line arguments
    based.get_command_line_args()

    # load accounts
    based.load_accounts()

    # initialize logging and loggers
    init_logging()
    based.init_loggers()
    for logger in based.LOGGERS.values():
        # make sure other loggers do not also write to root logger
        logger.propagate = False

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
    based.CALLBACKS["chat_invite"] = chat_invite

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
