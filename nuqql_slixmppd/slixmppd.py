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


from nuqql_based import based
from nuqql_based.based import Format, Callback

# dictionary for all xmpp client connections
CONNECTIONS = {}
THREADS = {}


class NuqqlClient(ClientXMPP):
    """
    Nuqql Client Class, derived from Slixmpp Client
    """

    def __init__(self, account, lock):
        # jid: account.user
        # password: account.password
        ClientXMPP.__init__(self, account.user, account.password)
        self.account = account
        self.account.status = "offline"     # set "online" in session_start()

        # event handlers
        self.add_event_handler("session_start", self._session_start)
        self.add_event_handler("disconnected", self._disconnected)
        self.add_event_handler("message", self.message)
        self.add_event_handler("groupchat_message", self.muc_message)
        self.add_event_handler("groupchat_invite", self._muc_invite)

        self._status = None     # status configured by user
        self.lock = lock
        self.buddies = []
        self.queue = []
        self.history = []
        self.messages = []

        self.muc_invites = {}
        self.muc_cache = []
        self.muc_filter_own = True

    def _session_start(self, _event):
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
        self.account.status = "online"      # flag account as "online" now
        if self._status:
            # set a previously configured status
            self._set_status(self._status)

    def _disconnected(self, _event):
        """
        Got disconnected, set status to offline
        """

        self.account.status = "offline"     # flag account as "offline"
        self.buddies = []                   # flush buddy list

    def message(self, msg):
        """
        Message handler
        """

        if msg['type'] in ('chat', 'normal'):
            if msg.xml.find("{http://jabber.org/protocol/muc#user}x"):
                # this seems to be a muc (invite) message, ignore it for now
                # TODO: add special handling?
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
            formated_msg = based.format_message(
                self.account, tstamp, msg["from"], msg["to"], msg["body"])
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
            formated_msg = based.format_chat_msg(
                self.account, tstamp, msg["mucnick"], chat, msg["body"])
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
            msg = Format.CHAT_USER.format(self.account.aid, chat, user,
                                          user_alias, status)
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
        self.muc_invites[chat] = (user, chat)

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

        # create temporary copy and flush queue
        self.lock.acquire()
        queue = self.queue[:]
        self.queue = []
        self.lock.release()

        # handle commands in queue
        for cmd, params in queue:
            if cmd == Callback.SEND_MESSAGE:
                self._send_message(params)
            if cmd == Callback.SET_STATUS:
                self._set_status(params[0])
            if cmd == Callback.GET_STATUS:
                self._get_status()
            if cmd == Callback.CHAT_LIST:
                self._chat_list()
            if cmd == Callback.CHAT_JOIN:
                self._chat_join(params[0])
            if cmd == Callback.CHAT_PART:
                self._chat_part(params[0])
            if cmd == Callback.CHAT_USERS:
                self._chat_users(params[0])
            if cmd == Callback.CHAT_INVITE:
                self._chat_invite(params[0], params[1])

    def update_buddies(self):
        """
        Create a "safe" copy of roster
        """

        # get buddies from roster
        buddies = []
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
            buddies.append(buddy)

            # cleanup invites
            if jid in self.muc_invites:
                del self.muc_invites[jid]

        # handle pending invites as buddies
        for invite in self.muc_invites.values():
            _user, chat = invite
            buddy = based.Buddy(name=chat, alias=chat,
                                status="GROUP_CHAT_INVITE")
            buddies.append(buddy)

        # update buddy list
        self.lock.acquire()
        self.buddies = buddies
        self.lock.release()

    def _set_status(self, status):
        """
        Set the current status of the account
        """

        # save status and send presence to server
        self._status = status
        self.send_presence(pshow=status)

    def _get_status(self):
        """
        Get the current status of the account
        """

        connections = self.client_roster.presence(self.boundjid)
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

        self.lock.acquire()
        self.messages.append(Format.STATUS.format(self.account.aid, status))
        self.lock.release()

    def _chat_list(self):
        """
        List active chats of account
        """

        for chat in self.plugin['xep_0045'].get_joined_rooms():
            chat_alias = chat   # TODO: use something else as alias?
            nick = self.plugin['xep_0045'].our_nicks[chat]
            self.lock.acquire()
            self.messages.append(Format.CHAT_LIST.format(
                self.account.aid, chat, chat_alias, nick))
            self.lock.release()

    def _chat_join(self, chat):
        """
        Join chat on account
        """

        nick = self.jid
        self.plugin['xep_0045'].join_muc(chat,
                                         nick,
                                         # If a room password is needed, use:
                                         # password=the_room_password,
                                         wait=True)
        self.add_event_handler("muc::%s::got_online" % chat, self.muc_online)
        self.add_event_handler("muc::%s::got_offline" % chat, self.muc_offline)
        self.muc_cache.append(chat)
        return ""

    def _chat_part(self, chat):
        """
        Leave chat on account
        """

        # chat already joined
        if chat in self.plugin['xep_0045'].get_joined_rooms():
            nick = self.plugin['xep_0045'].our_nicks[chat]
            self.plugin['xep_0045'].leave_muc(chat, nick)
            self.del_event_handler("muc::%s::got_online" % chat,
                                   self.muc_online)
            self.del_event_handler("muc::%s::got_offline" % chat,
                                   self.muc_offline)
            # keep muc in muc_cache to filter it from buddy list

        # chat not joined yet, remove pending invite
        if chat in self.muc_invites:
            # simply ignore the pending invite and remove it
            del self.muc_invites[chat]

    def _chat_users(self, chat):
        """
        Get list of users in chat on account
        """

        roster = self.plugin['xep_0045'].get_roster(chat)
        if not roster:
            return

        for user in roster:
            if user == "":
                continue
            # TODO: try to retrieve proper alias
            user_alias = user
            # TODO: try to retrieve user's presence as status?
            status = "join"
            self.lock.acquire()
            self.messages.append(Format.CHAT_USER.format(
                self.account.aid, chat, user, user_alias, status))
            self.lock.release()

    def _chat_invite(self, chat, user):
        """
        Invite user to chat on account
        """

        self.plugin['xep_0045'].invite(chat, user)


def update_buddies(account_id, _cmd, _params):
    """
    Read buddies from client connection
    """

    try:
        xmpp = CONNECTIONS[account_id]
    except KeyError:
        # no active connection
        return ""

    # clear buddy list
    xmpp.account.buddies = []

    # parse buddy list and insert buddies into buddy list
    xmpp.lock.acquire()
    for buddy in xmpp.buddies:
        xmpp.account.buddies.append(buddy)
    xmpp.lock.release()

    return ""


def get_messages(account_id, _cmd, _params):
    """
    Read messages from client connection
    """

    try:
        xmpp = CONNECTIONS[account_id]
    except KeyError:
        # no active connection
        return []

    # get and return messages
    return "".join(xmpp.get_messages())


def collect_messages(account_id, _cmd, _params):
    """
    Collect all messages from client connection
    """

    try:
        xmpp = CONNECTIONS[account_id]
    except KeyError:
        # no active connection
        return ""

    # collect and return messages
    return "".join(xmpp.collect())


def enqueue(account_id, cmd, params):
    """
    Helper for adding commands to the command queue of the account/client
    """

    try:
        xmpp = CONNECTIONS[account_id]
    except KeyError:
        # no active connection
        return ""

    xmpp.enqueue_command(cmd, params)

    return ""


def send_message(account_id, cmd, params):
    """
    send a message to a jabber id on an account
    """

    # parse parameters
    if len(params) > 2:
        dest, msg, msg_type = params
    else:
        dest, msg = params
        msg_type = "chat"

    # nuqql sends a html-escaped message; construct "plain-text" version and
    # xhtml version using nuqql's message and use them as message body later
    html_msg = \
        '<body xmlns="http://www.w3.org/1999/xhtml">{}</body>'.format(msg)
    msg = html.unescape(msg)
    msg = "\n".join(re.split("<br/>", msg, flags=re.IGNORECASE))

    # send message
    enqueue(account_id, cmd, (dest, msg, html_msg, msg_type))

    return ""


def chat_send(account_id, _cmd, params):
    """
    Send message to chat on account
    """

    chat, msg = params
    return send_message(account_id, Callback.SEND_MESSAGE,
                        (chat, msg, "groupchat"))


def _reconnect(xmpp, last_connect):
    """
    Try to reconnect to the server if last connect is older than 10 seconds.
    """

    cur_time = time.time()
    if cur_time - last_connect < 10:
        return last_connect

    print("Reconnecting:", xmpp.account.user)
    xmpp.connect()
    return cur_time


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
    last_connect = time.time()

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
        # if account is offline, skip other steps to avoid issues with sending
        # commands/messages over the (uninitialized) xmpp connection
        if xmpp.account.status == "offline":
            last_connect = _reconnect(xmpp, last_connect)
            continue
        xmpp.handle_queue()
        xmpp.update_buddies()


def add_account(account_id, _cmd, params):
    """
    Add a new account (from based) and run a new slixmpp client thread for it
    """

    # event to signal thread is ready
    ready = Event()

    # event to signal if thread should stop
    running = Event()
    running.set()

    # create and start thread
    account = params[0]
    new_thread = Thread(target=run_client, args=(account, ready, running))
    new_thread.start()

    # save thread in active threads dictionary
    THREADS[account_id] = (new_thread, running)

    # wait until thread initialized everything
    ready.wait()

    return ""


def del_account(account_id, _cmd, _params):
    """
    Delete an existing account (in based) and
    stop slixmpp client thread for it
    """

    # stop thread
    thread, running = THREADS[account_id]
    running.clear()
    thread.join()

    # cleanup
    del CONNECTIONS[account_id]
    del THREADS[account_id]

    return ""


def init_logging(config):
    """
    Configure logging module, so slixmpp logs are written to a file
    """

    # determine logging path from command line parameters and
    # make sure it exists
    logs_dir = config["dir"] / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "slixmpp.log"

    # configure logging module to write to file
    log_format = "%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s"
    loglevel = based.LOGLEVELS[config["loglevel"]]
    logging.basicConfig(filename=log_file, level=loglevel,
                        format=log_format, datefmt="%s")
    os.chmod(log_file, stat.S_IRWXU)


def stop_thread(account_id, _cmd, _params):
    """
    Quit backend/stop client thread
    """

    # stop thread
    print("Signalling account thread to stop.")
    _thread, running = THREADS[account_id]
    running.clear()


def main():
    """
    Main function, initialize everything and start server
    """

    # initialize configuration from command line and config file
    config = based.init_config()

    # initialize logging and main logger
    init_logging(config)
    based.init_main_logger()

    # load accounts
    based.load_accounts()

    # initialize account loggers
    based.init_account_loggers()
    for acc in based.get_accounts().values():
        # make sure other loggers do not also write to root logger
        acc.logger.propagate = False

    # start a client connection for every xmpp account in it's own thread
    for acc in based.get_accounts().values():
        if acc.type == "xmpp":
            add_account(acc.aid, Callback.ADD_ACCOUNT, (acc, ))

    # register callbacks
    based.register_callback(Callback.QUIT, stop_thread)
    based.register_callback(Callback.ADD_ACCOUNT, add_account)
    based.register_callback(Callback.DEL_ACCOUNT, del_account)
    based.register_callback(Callback.UPDATE_BUDDIES, update_buddies)
    based.register_callback(Callback.GET_MESSAGES, get_messages)
    based.register_callback(Callback.SEND_MESSAGE, send_message)
    based.register_callback(Callback.COLLECT_MESSAGES, collect_messages)
    based.register_callback(Callback.SET_STATUS, enqueue)
    based.register_callback(Callback.GET_STATUS, enqueue)
    based.register_callback(Callback.CHAT_LIST, enqueue)
    based.register_callback(Callback.CHAT_JOIN, enqueue)
    based.register_callback(Callback.CHAT_PART, enqueue)
    based.register_callback(Callback.CHAT_SEND, chat_send)
    based.register_callback(Callback.CHAT_USERS, enqueue)
    based.register_callback(Callback.CHAT_INVITE, enqueue)

    # run the server for the nuqql connection
    try:
        based.run_server(config)
    except KeyboardInterrupt:
        # try to terminate all threads
        for _thread, running in THREADS.values():
            print("Signalling account thread to stop.")
            running.clear()
    finally:
        # wait for threads to finish
        print("Waiting for all threads to finish. This might take a while.")
        for thread, _running in THREADS.values():
            thread.join()
        sys.exit()


if __name__ == '__main__':
    main()
