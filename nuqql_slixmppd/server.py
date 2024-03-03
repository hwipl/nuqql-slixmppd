"""
slixmppd backend server
"""

import html
import re
import logging
import stat
import os

from typing import TYPE_CHECKING, Dict, Optional, Tuple

# nuqql-based
from nuqql_based.based import Based
from nuqql_based.callback import Callback
from nuqql_based.message import Message

# slixmppd
from nuqql_slixmppd.client import BackendClient

if TYPE_CHECKING:   # imports for typing
    # pylint: disable=ungrouped-imports
    from nuqql_based.account import Account     # noqa
    from nuqql_based.based import CallbackList
    from nuqql_based.config import Config

# slixmppd version
VERSION = "0.8.3"


class BackendServer:
    """
    Backend server class, manages the BackendClients for connections to
    IM networks
    """

    def __init__(self) -> None:
        self.connections: Dict[int, BackendClient] = {}
        self.based = Based("slixmppd", VERSION)

    async def start(self) -> None:
        """
        Start server
        """

        # register callbacks
        callbacks: "CallbackList" = [
            # based events
            (Callback.BASED_CONFIG, self._based_config),
            (Callback.BASED_QUIT, self._based_quit),

            # nuqql messages
            (Callback.HELP_WELCOME, self._help_welcome),
            (Callback.HELP_ACCOUNT_ADD, self._help_account_add),
            (Callback.ADD_ACCOUNT, self.add_account),
            (Callback.DEL_ACCOUNT, self.del_account),
            (Callback.GET_BUDDIES, self.handle_command),
            (Callback.SEND_MESSAGE, self.send_message),
            (Callback.SET_STATUS, self.handle_command),
            (Callback.GET_STATUS, self.handle_command),
            (Callback.CHAT_LIST, self.handle_command),
            (Callback.CHAT_JOIN, self.handle_command),
            (Callback.CHAT_PART, self.handle_command),
            (Callback.CHAT_SEND, self.chat_send),
            (Callback.CHAT_USERS, self.handle_command),
            (Callback.CHAT_INVITE, self.handle_command),
        ]
        self.based.set_callbacks(callbacks)

        # start based
        await self.based.start()

    async def handle_command(self, account: Optional["Account"], cmd: Callback,
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

        await xmpp.handle_command(cmd, params)

        return ""

    async def send_message(self, account: Optional["Account"], cmd: Callback,
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
        html_msg = f'<body xmlns="http://www.w3.org/1999/xhtml">{msg}</body>'
        msg = html.unescape(msg)
        msg = "\n".join(re.split("<br/>", msg, flags=re.IGNORECASE))

        # send message
        await self.handle_command(account, cmd, (dest, msg, html_msg,
                                                 msg_type))

        return ""

    async def chat_send(self, account: Optional["Account"], _cmd: Callback,
                        params: Tuple) -> str:
        """
        Send message to chat on account
        """

        chat, msg = params
        return await self.send_message(account, Callback.SEND_MESSAGE,
                                       (chat, msg, "groupchat"))

    async def run_client(self, account: Optional["Account"]) -> None:
        """
        Run client connection
        """

        # start client connection
        assert account
        xmpp = BackendClient(account)
        xmpp.register_plugin('xep_0071')    # XHTML-IM
        xmpp.register_plugin('xep_0082')    # XMPP Date and Time Profiles
        xmpp.register_plugin('xep_0203')    # Delayed Delivery, time stamps
        xmpp.register_plugin('xep_0030')    # Service Discovery
        xmpp.register_plugin('xep_0045')    # Multi-User Chat
        xmpp.register_plugin('xep_0199')    # XMPP Ping
        xmpp.connect()

        # save client connection in active connections dictionary
        self.connections[account.aid] = xmpp

    async def add_account(self, account: Optional["Account"], _cmd: Callback,
                          _params: Tuple) -> str:
        """
        Add a new account (from based) and run a new slixmpp client thread for
        it
        """

        # only handle xmpp accounts
        assert account
        if account.type != "xmpp":
            return ""

        # create and start thread
        await self.run_client(account)

        return ""

    async def del_account(self, account: Optional["Account"], _cmd: Callback,
                          _params: Tuple) -> str:
        """
        Delete an existing account (in based) and
        stop slixmpp client thread for it
        """

        # stop client
        assert account
        xmpp = self.connections[account.aid]
        xmpp.shutdown = True
        xmpp.disconnect()
        await xmpp.disconnected

        # cleanup
        del self.connections[account.aid]

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

    @staticmethod
    async def _help_account_add(_account: Optional["Account"],
                                _cmd: Callback, _params: Tuple) -> str:
        """
        Handle account add help event
        """

        add_help = Message.info("You do not have any accounts configured.")
        add_help += Message.info("You can add a new xmpp account with the "
                                 "following command: "
                                 "account add xmpp <jabber ID> <password>")
        add_help += Message.info("Example: account add xmpp "
                                 "dummy@jabber.org MyPassword")
        return add_help

    async def _help_welcome(self, _account: Optional["Account"],
                            _cmd: Callback, _params: Tuple) -> str:
        """
        Handle welcome help message event
        """

        welcome = Message.info(f"Welcome to nuqql-slixmppd v{VERSION}!")
        welcome += Message.info("Enter \"help\" for a list of available "
                                "commands and their help texts")
        if self.based.config.get_push_accounts():
            welcome += Message.info("Listing your accounts:")
        return welcome

    async def _based_config(self, _account: Optional["Account"],
                            _cmd: Callback, params: Tuple) -> str:
        """
        Config event in based
        """

        config = params[0]
        self.init_logging(config)
        return ""

    async def _based_quit(self, _account: Optional["Account"], _cmd: Callback,
                          _params: Tuple) -> str:
        """
        Based shut down event
        """

        for xmpp in self.connections.values():
            xmpp.shutdown = True
            xmpp.disconnect()
            await xmpp.disconnected
        return ""
