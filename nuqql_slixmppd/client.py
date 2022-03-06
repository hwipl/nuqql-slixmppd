"""
slixmppd backend client
"""

import logging
import time
import unicodedata

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

# slixmpp
from slixmpp import ClientXMPP  # type: ignore
# from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.exceptions import PresenceError  # type: ignore

# nuqql-based
from nuqql_based.callback import Callback
from nuqql_based.message import Message

if TYPE_CHECKING:   # imports for typing
    # TODO: move Lock here?
    from nuqql_based.account import Account

# slixmppd version
VERSION = "0.4"


class BackendClient(ClientXMPP):
    """
    Backend Client Class, derived from Slixmpp Client,
    for a connection to the IM network
    """

    def __init__(self, account: "Account") -> None:
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
        self.shutdown = False

        self.muc_invites: Dict[str, Tuple[str, str]] = {}
        self.muc_cache: List[str] = []

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
        if not self.shutdown:
            # not shutting down -> reconnect
            self.connect()

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
            sender = msg['mucnick']
            nick = self.plugin['xep_0045'].our_nicks[chat]
            if self.account.config.get_filter_own() and sender == nick:
                return

            # rewrite sender of own messages to "<self>"
            if sender == nick:
                sender = "<self>"

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
                self.account, tstamp, sender, chat, msg["body"])
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

    async def handle_command(self, cmd: Callback, params: Tuple) -> None:
        """
        Handle a command
        Params tuple consisting of:
            command and its parameters
        """

        if cmd == Callback.GET_BUDDIES:
            self.get_buddies(params[0])
        if cmd == Callback.SEND_MESSAGE:
            self._send_message(params)
        if cmd == Callback.SET_STATUS:
            self._set_status(params[0])
        if cmd == Callback.GET_STATUS:
            self._get_status()
        if cmd == Callback.CHAT_LIST:
            self._chat_list()
        if cmd == Callback.CHAT_JOIN:
            await self._chat_join(params[0])
        if cmd == Callback.CHAT_PART:
            self._chat_part(params[0])
        if cmd == Callback.CHAT_USERS:
            self._chat_users(params[0])
        if cmd == Callback.CHAT_INVITE:
            self._chat_invite(params[0], params[1])

    def get_buddies(self, online: bool) -> None:
        """
        Get roster/buddy list
        """

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

            # send buddy message
            if online and status != "available":
                continue
            msg = Message.buddy(self.account, jid, alias, status)
            self.account.receive_msg(msg)

            # cleanup invites
            if jid in self.muc_invites:
                del self.muc_invites[jid]

        # handle pending invites as buddies
        if online:
            # invites are not "online"
            return
        for invite in self.muc_invites.values():
            _user, chat = invite
            msg = Message.buddy(self.account, chat, chat, "GROUP_CHAT_INVITE")
            self.account.receive_msg(msg)

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

    async def _chat_join(self, chat: str) -> None:
        """
        Join chat on account
        """

        nick = self.boundjid.bare
        try:
            # if a room password is needed, use: password=the_room_password
            await self.plugin['xep_0045'].join_muc_wait(chat, nick)
        except PresenceError as ex:
            logging.error(ex)
            msg = Message.chat_msg(self.account,
                                   int(time.time()),
                                   "<self>",
                                   chat,
                                   "Error joining chat, part chat to clean up")
            self.account.receive_msg(msg)
        self.add_event_handler(f"muc::{chat}::got_online", self.muc_online)
        self.add_event_handler(f"muc::{chat}::got_offline", self.muc_offline)
        self.muc_cache.append(chat)

    def _chat_part(self, chat: str) -> None:
        """
        Leave chat on account
        """

        # chat already joined
        if chat in self.plugin['xep_0045'].get_joined_rooms():
            nick = self.plugin['xep_0045'].our_nicks[chat]
            self.plugin['xep_0045'].leave_muc(chat, nick)
            self.del_event_handler(f"muc::{chat}::got_online",
                                   self.muc_online)
            self.del_event_handler(f"muc::{chat}::got_offline",
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
