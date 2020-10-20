"""
slixmppd backend server
"""

import time
import asyncio
import html
import re
import logging
import stat
import os

from typing import TYPE_CHECKING, Dict, Optional, Tuple
from threading import Thread, Lock, Event

# slixmppd
from nuqql_slixmppd.client import BackendClient

# nuqql-based
from nuqql_based.based import Based
from nuqql_based.callback import Callback

if TYPE_CHECKING:   # imports for typing
    from nuqql_based.account import Account     # noqa
    from nuqql_based.config import Config

# slixmppd version
VERSION = "0.6.0"


class BackendServer:
    """
    Backend server class, manages the BackendClients for connections to
    IM networks
    """

    def __init__(self) -> None:
        self.connections: Dict[int, BackendClient] = {}
        self.threads: Dict[int, Tuple[Thread, Event]] = {}
        self.based = Based("slixmppd", VERSION)

    def start(self) -> None:
        """
        Start server
        """

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
        self.based.set_callbacks(callbacks)

        # start based
        self.based.start()

    def enqueue(self, account: Optional["Account"], cmd: Callback,
                params: Tuple) -> str:
        """
        Helper for adding commands to the command queue of the account/client
        """

        assert account
        try:
            xmpp = self.connections[account.aid]
        except KeyError:
            # no active connection
            return ""

        xmpp.enqueue_command(cmd, params)

        return ""

    def send_message(self, account: Optional["Account"], cmd: Callback,
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
        self.enqueue(account, cmd, (dest, msg, html_msg, msg_type))

        return ""

    def chat_send(self, account: Optional["Account"], _cmd: Callback,
                  params: Tuple) -> str:
        """
        Send message to chat on account
        """

        chat, msg = params
        return self.send_message(account, Callback.SEND_MESSAGE,
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

    def run_client(self, account: Optional["Account"], ready: Event,
                   running: Event) -> None:
        """
        Run client connection in a new thread,
        as long as running Event is set to true.
        """

        # get event loop for thread
        assert account
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

    def add_account(self, account: Optional["Account"], _cmd: Callback,
                    _params: Tuple) -> str:
        """
        Add a new account (from based) and run a new slixmpp client thread for
        it
        """

        # only handle xmpp accounts
        assert account
        if account.type != "xmpp":
            return ""

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
        self.threads[account.aid] = (new_thread, running)

        # wait until thread initialized everything
        ready.wait()

        return ""

    def del_account(self, account: Optional["Account"], _cmd: Callback,
                    _params: Tuple) -> str:
        """
        Delete an existing account (in based) and
        stop slixmpp client thread for it
        """

        # stop thread
        assert account
        thread, running = self.threads[account.aid]
        running.clear()
        thread.join()

        # cleanup
        del self.connections[account.aid]
        del self.threads[account.aid]

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

    def stop_thread(self, account: Optional["Account"], _cmd: Callback,
                    _params: Tuple) -> str:
        """
        Quit backend/stop client thread
        """

        # stop thread
        assert account
        print("Signalling account thread to stop.")
        _thread, running = self.threads[account.aid]
        running.clear()
        return ""

    def _based_config(self, _account: Optional["Account"], _cmd: Callback,
                      params: Tuple) -> str:
        """
        Config event in based
        """

        config = params[0]
        self.init_logging(config)
        return ""

    def _based_interrupt(self, _account: Optional["Account"], _cmd: Callback,
                         _params: Tuple) -> str:
        """
        KeyboardInterrupt event in based
        """

        for _thread, running in self.threads.values():
            print("Signalling account thread to stop.")
            running.clear()
        return ""

    def _based_quit(self, _account: Optional["Account"], _cmd: Callback,
                    _params: Tuple) -> str:
        """
        Based shut down event
        """

        print("Waiting for all threads to finish. This might take a while.")
        for thread, _running in self.threads.values():
            thread.join()
        return ""
