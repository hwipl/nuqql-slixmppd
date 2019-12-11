#!/usr/bin/env python3

"""
slixmppd
"""

import time
import asyncio
import html
import re
import unicodedata
import logging
import stat
import os

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple
from threading import Thread, Lock, Event

# slixmpp
from slixmpp import ClientXMPP  # type: ignore
# from slixmpp.exceptions import IqError, IqTimeout

# nuqql-based
from nuqql_based.based import Based
from nuqql_based.callback import Callback
from nuqql_based.message import Message

if TYPE_CHECKING:   # imports for typing
    from nuqql_based.account import Account
    from nuqql_based.config import Config

# slixmppd version
VERSION = "0.4"


class BackendClient(ClientXMPP):
    """
    Backend Client Class, derived from Slixmpp Client,
    for a connection to the IM network
    """

    def __init__(self, account: "Account", lock: Lock) -> None:
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

        self._status: Optional[str] = None     # status configured by user
        self.lock = lock
        self.queue: List[Tuple[Callback, Tuple]] = []

        self.muc_invites: Dict[str, Tuple[str, str]] = {}
        self.muc_cache: List[str] = []
        self.muc_filter_own = True

    def _session_start(self, _event) -> None:
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

    def _disconnected(self, _event) -> None:
        """
        Got disconnected, set status to offline
        """

        self.account.status = "offline"     # flag account as "offline"
        self.account.flush_buddies()        # flush buddy list

    def message(self, msg) -> None:
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
            formatted_msg = Message.message(
                self.account, tstamp, msg["from"], msg["to"], msg["body"])
            self.account.receive_msg(formatted_msg)

    def muc_message(self, msg) -> None:
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
            formatted_msg = Message.chat_msg(
                self.account, tstamp, msg["mucnick"], chat, msg["body"])
            self.account.receive_msg(formatted_msg)

    def _muc_presence(self, presence, status) -> None:
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
            msg = Message.chat_user(self.account, chat, user, user_alias,
                                    status)
            self.account.receive_msg(msg)

    def _muc_invite(self, inv) -> None:
        """
        Group chat invite handler
        """

        user = inv["to"]
        chat = inv["from"]
        self.muc_invites[chat] = (user, chat)

    def muc_online(self, presence) -> None:
        """
        Group chat online presence handler
        """

        self._muc_presence(presence, "online")

    def muc_offline(self, presence) -> None:
        """
        Group chat offline presence handler
        """

        self._muc_presence(presence, "offline")

    def _send_message(self, message_tuple: Tuple) -> None:
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

    def enqueue_command(self, cmd: Callback, params: Tuple) -> None:
        """
        Enqueue a command to the queue consisting of:
            command and its parameters
        """

        self.lock.acquire()
        # just add message tuple to queue
        self.queue.append((cmd, params))
        self.lock.release()

    def handle_queue(self) -> None:
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

    def update_buddies(self) -> None:
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
            buddy = (jid, alias, status)
            buddies.append(buddy)

            # cleanup invites
            if jid in self.muc_invites:
                del self.muc_invites[jid]

        # handle pending invites as buddies
        for invite in self.muc_invites.values():
            _user, chat = invite
            buddy = (chat, chat, "GROUP_CHAT_INVITE")
            buddies.append(buddy)

        # update buddy list
        self.account.update_buddies(buddies)

    def _set_status(self, status: str) -> None:
        """
        Set the current status of the account
        """

        # save status and send presence to server
        self._status = status
        self.send_presence(pshow=status)

    def _get_status(self) -> None:
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

        self.account.receive_msg(Message.status(self.account, status))

    def _chat_list(self) -> None:
        """
        List active chats of account
        """

        for chat in self.plugin['xep_0045'].get_joined_rooms():
            chat_alias = chat   # TODO: use something else as alias?
            nick = self.plugin['xep_0045'].our_nicks[chat]
            self.account.receive_msg(Message.chat_list(
                self.account, chat, chat_alias, nick))

    def _chat_join(self, chat: str) -> None:
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

    def _chat_part(self, chat: str) -> None:
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

    def _chat_users(self, chat: str) -> None:
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
            self.account.receive_msg(Message.chat_user(
                self.account, chat, user, user_alias, status))

    def _chat_invite(self, chat: str, user: str) -> None:
        """
        Invite user to chat on account
        """

        self.plugin['xep_0045'].invite(chat, user)


class BackendServer:
    """
    Backend server class, manages the BackendClients for connections to
    IM networks
    """

    def __init__(self) -> None:
        self.connections: Dict[int, BackendClient] = {}
        self.threads: Dict[int, Tuple[Thread, Event]] = {}

        # register callbacks
        callbacks = [
            # based events
            (Callback.BASED_CONFIG, self._based_config),
            (Callback.BASED_INTERRUPT, self._based_interrupt),
            (Callback.BASED_QUIT, self._based_quit),

            # nuqql messages
            (Callback.QUIT, self.stop_thread),
            (Callback.ADD_ACCOUNT, self.add_account),
            (Callback.DEL_ACCOUNT, self.del_account),
            (Callback.SEND_MESSAGE, self.send_message),
            (Callback.SET_STATUS, self.enqueue),
            (Callback.GET_STATUS, self.enqueue),
            (Callback.CHAT_LIST, self.enqueue),
            (Callback.CHAT_JOIN, self.enqueue),
            (Callback.CHAT_PART, self.enqueue),
            (Callback.CHAT_SEND, self.chat_send),
            (Callback.CHAT_USERS, self.enqueue),
            (Callback.CHAT_INVITE, self.enqueue),
        ]

        # start based
        self.based = Based("slixmppd", VERSION, callbacks)

    def start(self) -> None:
        """
        Start server
        """

        self.based.start()

    def enqueue(self, account_id: int, cmd: Callback, params: Tuple) -> str:
        """
        Helper for adding commands to the command queue of the account/client
        """

        try:
            xmpp = self.connections[account_id]
        except KeyError:
            # no active connection
            return ""

        xmpp.enqueue_command(cmd, params)

        return ""

    def send_message(self, account_id: int, cmd: Callback,
                     params: Tuple) -> str:
        """
        send a message to a jabber id on an account
        """

        # parse parameters
        if len(params) > 2:
            dest, msg, msg_type = params
        else:
            dest, msg = params
            msg_type = "chat"

        # nuqql sends a html-escaped message; construct "plain-text" version
        # and xhtml version using nuqql's message and use them as message body
        # later
        html_msg = \
            '<body xmlns="http://www.w3.org/1999/xhtml">{}</body>'.format(msg)
        msg = html.unescape(msg)
        msg = "\n".join(re.split("<br/>", msg, flags=re.IGNORECASE))

        # send message
        self.enqueue(account_id, cmd, (dest, msg, html_msg, msg_type))

        return ""

    def chat_send(self, account_id: int, _cmd: Callback, params: Tuple) -> str:
        """
        Send message to chat on account
        """

        chat, msg = params
        return self.send_message(account_id, Callback.SEND_MESSAGE,
                                 (chat, msg, "groupchat"))

    @staticmethod
    def _reconnect(xmpp, last_connect: float) -> float:
        """
        Try to reconnect to the server if last connect is older than 10
        seconds.
        """

        cur_time = time.time()
        if cur_time - last_connect < 10:
            return last_connect

        print("Reconnecting:", xmpp.account.user)
        xmpp.connect()
        return cur_time

    def run_client(self, account: "Account", ready: Event,
                   running: Event) -> None:
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
        xmpp = BackendClient(account, lock)
        xmpp.register_plugin('xep_0071')    # XHTML-IM
        xmpp.register_plugin('xep_0082')    # XMPP Date and Time Profiles
        xmpp.register_plugin('xep_0203')    # Delayed Delivery, time stamps
        xmpp.register_plugin('xep_0030')    # Service Discovery
        xmpp.register_plugin('xep_0045')    # Multi-User Chat
        xmpp.register_plugin('xep_0199')    # XMPP Ping
        xmpp.connect()
        last_connect = time.time()

        # save client connection in active connections dictionary
        self.connections[account.aid] = xmpp

        # thread is ready to enter main loop, inform caller
        ready.set()

        # enter main loop, and keep running until "running" is set to false
        # by the KeyboardInterrupt
        while running.is_set():
            # process xmpp client for 0.1 seconds, then send pending outgoing
            # messages and update the (safe copy of the) buddy list
            xmpp.process(timeout=0.1)
            # if account is offline, skip other steps to avoid issues with
            # sending commands/messages over the (uninitialized) xmpp
            # connection
            if xmpp.account.status == "offline":
                last_connect = self._reconnect(xmpp, last_connect)
                continue
            xmpp.handle_queue()
            xmpp.update_buddies()

    def add_account(self, account_id: int, _cmd: Callback,
                    params: Tuple) -> str:
        """
        Add a new account (from based) and run a new slixmpp client thread for
        it
        """

        # only handle xmpp accounts
        account = params[0]
        if account.type != "xmpp":
            return ""

        # make sure other loggers do not also write to root logger
        account.logger.propagate = False

        # event to signal thread is ready
        ready = Event()

        # event to signal if thread should stop
        running = Event()
        running.set()

        # create and start thread
        new_thread = Thread(target=self.run_client, args=(account, ready,
                                                          running))
        new_thread.start()

        # save thread in active threads dictionary
        self.threads[account_id] = (new_thread, running)

        # wait until thread initialized everything
        ready.wait()

        return ""

    def del_account(self, account_id: int, _cmd: Callback,
                    _params: Tuple) -> str:
        """
        Delete an existing account (in based) and
        stop slixmpp client thread for it
        """

        # stop thread
        thread, running = self.threads[account_id]
        running.clear()
        thread.join()

        # cleanup
        del self.connections[account_id]
        del self.threads[account_id]

        return ""

    @staticmethod
    def init_logging(config: "Config") -> None:
        """
        Configure logging module, so slixmpp logs are written to a file
        """

        # determine logging path from command line parameters and
        # make sure it exists
        logs_dir = config.get_dir() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "slixmpp.log"

        # configure logging module to write to file
        log_format = "%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s"
        loglevel = config.get_loglevel()
        logging.basicConfig(filename=log_file, level=loglevel,
                            format=log_format, datefmt="%s")
        os.chmod(log_file, stat.S_IRWXU)

    def stop_thread(self, account_id: int, _cmd: Callback,
                    _params: Tuple) -> str:
        """
        Quit backend/stop client thread
        """

        # stop thread
        print("Signalling account thread to stop.")
        _thread, running = self.threads[account_id]
        running.clear()
        return ""

    def _based_config(self, _account_id: int, _cmd: Callback,
                      params: Tuple) -> str:
        """
        Config event in based
        """

        config = params[0]
        self.init_logging(config)
        return ""

    def _based_interrupt(self, _account_id: int, _cmd: Callback,
                         _params: Tuple) -> str:
        """
        KeyboardInterrupt event in based
        """

        for _thread, running in self.threads.values():
            print("Signalling account thread to stop.")
            running.clear()
        return ""

    def _based_quit(self, _account_id: int, _cmd: Callback,
                    _params: Tuple) -> str:
        """
        Based shut down event
        """

        print("Waiting for all threads to finish. This might take a while.")
        for thread, _running in self.threads.values():
            thread.join()
        return ""


def main() -> None:
    """
    Main function, initialize everything and start server
    """

    server = BackendServer()
    server.start()


if __name__ == '__main__':
    main()
