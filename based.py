#!/usr/bin/env python3

"""
Basic nuqql backend
"""

import socketserver
import argparse
import pathlib
import logging
import pickle
import select
import stat
import sys
import os

import daemon

ACCOUNTS = {}
LOGGERS = {}
CALLBACKS = {}
ARGS = None


class Buddy:
    """
    Storage for buddy specific information
    """

    def __init__(self, name="none", alias="none", status="Available"):
        self.name = name
        self.alias = alias
        self.status = status


class Account:
    """
    Storage for account specific information
    """

    def __init__(self, aid=0, name="", atype="dummy", user="dummy@dummy.com",
                 password="dummy_password", status="online"):
        self.aid = aid
        self.name = name
        self.type = atype
        self.user = user
        self.password = password
        self.status = status
        self.buddies = []

    def send_msg(self, user, msg):
        """
        Send message to user. Currently, this only logs the message
        """

        # try to send message
        if "send_message" in CALLBACKS:
            CALLBACKS["send_message"](self, user, msg)

        # log message
        log_msg = "message: to {0}: {1}".format(user, msg)
        LOGGERS[self.aid].info(log_msg)


class NuqqlBaseHandler(socketserver.BaseRequestHandler):
    """
    Request Handler for the server, instantiated once per client connection.

    This is limited to one client connection at a time. It should be fine for
    our basic use case.
    """

    buffer = b""

    def handle_incoming(self):
        """
        Handle messages coming from the backend connections
        """

        # if there is no callback for messages, simply stop here
        if "get_messages" not in CALLBACKS:
            return

        for account in ACCOUNTS.values():
            messages = CALLBACKS["get_messages"](account)
            for msg in messages:
                msg = msg + "\r\n"
                msg = msg.encode()
                self.request.sendall(msg)

    def handle_messages(self):
        """
        Try to find complete messages in buffer and handle each
        """

        # try to find first complete message
        eom = self.buffer.find(b"\r\n")
        while eom != -1:
            # extract message from buffer
            msg = self.buffer[:eom]
            self.buffer = self.buffer[eom + 2:]

            # check if there is another complete message, for
            # next loop iteration
            eom = self.buffer.find(b"\r\n")

            # start message handling
            try:
                msg = msg.decode()
            except UnicodeDecodeError as error:
                # invalid message format, drop client
                return error
            reply = handle_msg(msg)

            # if there's nothing to send back, just continue
            if reply == "":
                continue

            # construct reply and send it back
            reply = reply + "\r\n"
            reply = reply.encode()
            self.request.sendall(reply)

    def handle(self):
        # self.request is the client socket
        while True:
            # handle incoming xmpp messages
            self.handle_incoming()

            # handle messages from nuqql client
            # wait 0.1 seconds for data to become available
            reads, unused_writes, errs = select.select([self.request, ], [],
                                                       [self.request, ], 0.1)
            if self.request in errs:
                # something is wrong, drop client
                return

            if self.request in reads:
                # read data from socket and add it to buffer
                self.data = self.request.recv(1024)

                # self.buffer += self.data.decode()
                self.buffer += self.data

            # handle each complete message
            error = self.handle_messages()
            if error:
                # some error occured handling the messages, drop client
                return


def handle_account_list():
    """
    List all accounts
    """

    replies = []
    for account in ACCOUNTS.values():
        reply = "account: {0} ({1}) {2} {3} [{4}]".format(
            account.aid, account.name, account.type, account.user,
            account.status)
        replies.append(reply)

    # log event
    log_msg = "account list: {0}".format(replies)
    LOGGERS["main"].info(log_msg)

    # return a single string containing "\r\n" as line separator.
    # BaseHandler.handle will add the final "\r\n"
    return "\r\n".join(replies)


def _get_account_id():
    """
    Get next free account id
    """

    if not ACCOUNTS:
        return 0

    last_acc_id = -1
    for acc_id in sorted(ACCOUNTS.keys()):
        if acc_id - last_acc_id >= 2:
            return last_acc_id + 1
        if acc_id - last_acc_id == 1:
            last_acc_id = acc_id

    return last_acc_id + 1


def handle_account_add(params):
    """
    Add a new account.

    Expected format:
        account add xmpp robot@my_jabber_server.com my_password

    params does not include "account add"
    """

    # check if there are enough parameters
    if len(params) < 3:
        return ""

    # get account information
    acc_id = _get_account_id()
    acc_type = params[0]
    acc_user = params[1]
    acc_pass = params[2]
    new_acc = Account(aid=acc_id, atype=acc_type, user=acc_user,
                      password=acc_pass)

    # make sure the account does not exist
    for acc in ACCOUNTS.values():
        if acc.type == new_acc.type and acc.user == new_acc.user:
            return "info: account already exists."

    # new account; add it
    ACCOUNTS[new_acc.aid] = new_acc

    # store updated accounts in file
    store_accounts()

    # create mew logger
    account_dir = ARGS.dir + "/logs/account/{0}".format(acc_id)
    pathlib.Path(account_dir).mkdir(parents=True, exist_ok=True)
    os.chmod(account_dir, stat.S_IRWXU)
    account_log = account_dir + "/account.log"
    # logger name must be string
    LOGGERS[acc_id] = get_logger(str(acc_id), account_log)
    os.chmod(account_log, stat.S_IRUSR | stat.S_IWUSR)

    # log event
    log_msg = "account new: id {0} type {1} user {2}".format(new_acc.aid,
                                                             new_acc.type,
                                                             new_acc.user)
    LOGGERS["main"].info(log_msg)

    # notify callback (if present) about new account
    if "add_account" in CALLBACKS:
        CALLBACKS["add_account"](new_acc)

    # inform caller about success
    return "info: new account added."


def handle_account_buddies(acc_id, params):
    """
    Get buddies for a specific account. If params contains "online", filter
    online buddies.

    Expected format:
        account <ID> buddies [online]

    params does not include "account <ID> buddies"

    Returned messages should look like:
        buddy: <acc_id> status: <Offline/Available> name: <name> alias: <alias>
    """

    # update buddy list
    # if "update_buddies" in ACCOUNTS[acc_id].callbacks:
    #     ACCOUNTS[acc_id].callbacks["update_buddies"](ACCOUNTS[acc_id])
    if "update_buddies" in CALLBACKS:
        CALLBACKS["update_buddies"](ACCOUNTS[acc_id])

    # filter online buddies?
    online = False
    if len(params) >= 1 and params[0].lower() == "online":
        online = True

    # get buddies for account
    replies = []
    for buddy in ACCOUNTS[acc_id].buddies:
        # filter online buddies if wanted by client
        if online and buddy.status != "Available":
            continue

        # construct replies
        reply = "buddy: {0} status: {1} name: {2} alias: {3}".format(
            acc_id, buddy.status, buddy.name, buddy.alias)
        replies.append(reply)

    # log event
    log_msg = "account {0} buddies: {1}".format(acc_id, replies)
    LOGGERS[acc_id].info(log_msg)

    # return replies as single string with "\r\n" as line separator.
    # BaseHandler.handle will add the final "\r\n"
    return "\r\n".join(replies)


def handle_account_collect(acc_id, params):
    """
    Collect messages for a specific account.

    Expected format:
        account <ID> collect [time]

    params does not include "account <ID> collect"
    """

    # collect all messages since <time>?
    time = 0   # TODO: change it to time of last collect?
    if len(params) >= 1:
        time = params[0]

    # log event
    log_msg = "account {0} collect {1}".format(acc_id, time)
    LOGGERS[acc_id].info(log_msg)

    # collect messages
    if "collect_messages" in CALLBACKS:
        return "\r\n".join(CALLBACKS["collect_messages"](ACCOUNTS[acc_id]))

    # nothing there
    return ""


def handle_account_send(acc_id, params):
    """
    Send a message to a someone over a specific account.

    Expected format:
        account <ID> send <username> <msg>

    params does not include "account <ID> send"
    """

    user = params[0]
    msg = " ".join(params[1:])      # TODO: do this better?

    # send message to user
    ACCOUNTS[acc_id].send_msg(user, msg)

    # check if it is an existing buddy
    for buddy in ACCOUNTS[acc_id].buddies:
        if buddy.name == user:
            return ""

    # new buddy; add it to account
    new_buddy = Buddy(name=user, alias="")
    ACCOUNTS[acc_id].buddies.append(new_buddy)

    # store updated accounts in file
    store_accounts()

    # log event
    log_msg = "account {0}: new buddy: {1}".format(acc_id, user)
    LOGGERS[acc_id].info(log_msg)

    return ""


def handle_account_status(acc_id, params):
    """
    Get or set current status of account

    Expected format:
        account <ID> status get
        account <ID> status set <STATUS>

    params does not include "account <ID> status"

    Returned messages for "status get" should look like:
        status: account <ID> status: <STATUS>
    """

    if not params:
        return ""

    # get current status
    if params[0] == "get":
        if "get_status" in CALLBACKS:
            status = CALLBACKS["get_status"](ACCOUNTS[acc_id])
        else:
            status = "online"   # TODO: do it better?
            return "status: account {} status: {}".format(acc_id, status)

    # set current status
    if params[0] == "set":
        if len(params) < 2:
            return ""

        status = params[1]
        if "set_status" in CALLBACKS:
            CALLBACKS["set_status"](ACCOUNTS[acc_id], status)
    return ""


def handle_account_chat(acc_id, params):
    """
    Join, part, and list chats and send messages to chats

    Expected format:
        account <ID> chat list
        account <ID> chat join <CHAT>
        account <ID> chat part <CHAT>
        account <ID> chat send <CHAT> <MESSAGE>
        account <ID> chat users <CHAT>
        account <ID> chat invite <CHAT> <USER>
    """

    if not params:
        return ""

    # list active chats
    if params[0] == "list":
        if "chat_list" in CALLBACKS:
            return "\r\n".join(CALLBACKS["chat_list"](ACCOUNTS[acc_id]))

    if len(params) < 2:
        return ""

    chat = params[1]
    # join a chat
    if params[0] == "join":
        if "chat_join" in CALLBACKS:
            return CALLBACKS["chat_join"](ACCOUNTS[acc_id], chat)

    # leave a chat
    if params[0] == "part":
        if "chat_part" in CALLBACKS:
            return CALLBACKS["chat_part"](ACCOUNTS[acc_id], chat)

    # get users in chat
    if params[0] == "users":
        if "chat_users" in CALLBACKS:
            return "\r\n".join(CALLBACKS["chat_users"](ACCOUNTS[acc_id], chat))

    if len(params) < 3:
        return ""

    # invite a user to a chat
    if params[0] == "invite":
        user = params[2]
        if "chat_invite" in CALLBACKS:
            return CALLBACKS["chat_invite"](ACCOUNTS[acc_id], chat, user)

    # send a message to a chat
    if params[0] == "send":
        msg = " ".join(params[2:])
        if "chat_send" in CALLBACKS:
            return CALLBACKS["chat_send"](ACCOUNTS[acc_id], chat, msg)

    return ""


# def handleAccount(parts, account, command, params):
def handle_account(parts):
    """
    Handle account specific commands received from client
    """

    if parts[1] == "list":
        # special case for "list" command
        command = parts[1]
    elif parts[1] == "add":
        # special case for "add" command
        command = parts[1]
        params = parts[2:]
    elif len(parts) >= 3:
        # account specific commands
        try:
            acc_id = int(parts[1])
        except ValueError:
            return "error: invalid account ID"
        command = parts[2]
        params = parts[3:]
        # valid account?
        if acc_id not in ACCOUNTS.keys():
            return "error: invalid account"
    else:
        # invalid command, ignore
        return "error: invalid command"

    if command == "list":
        return handle_account_list()

    if command == "add":
        # TODO: currently this supports
        # "account <ID> add" and "account add <ID>", OK?
        return handle_account_add(params)

    if command == "buddies":
        return handle_account_buddies(acc_id, params)

    if command == "collect":
        return handle_account_collect(acc_id, params)

    if command == "send":
        return handle_account_send(acc_id, params)

    if command == "status":
        return handle_account_status(acc_id, params)

    if command == "chat":
        return handle_account_chat(acc_id, params)

    return "error: unknown command"


def handle_msg(msg):
    """
    Handle messages received from client
    """

    # get parts of message
    parts = msg.split(" ")

    # account specific commands
    if len(parts) >= 2 and parts[0] == "account":
        return handle_account(parts)

    # others
    # TODO: ver? who?
    # ignore rest for now...
    return ""


def run_inet_server(args):
    """
    Run an AF_INET server
    """

    listen = (args.address, args.port)
    with socketserver.TCPServer(listen, NuqqlBaseHandler) as server:
        server.serve_forever()


def run_unix_server(args):
    """
    Run an AF_UNIX server
    """

    # make sure paths exist
    pathlib.Path(args.dir).mkdir(parents=True, exist_ok=True)
    sockfile = args.dir + "/" + args.sockfile
    try:
        # unlink sockfile of previous execution of the server
        os.unlink(sockfile)
    except FileNotFoundError:
        # ignore if the file did not exist
        pass
    with socketserver.UnixStreamServer(sockfile, NuqqlBaseHandler) as server:
        os.chmod(sockfile, stat.S_IRUSR | stat.S_IWUSR)
        server.serve_forever()


def run_server(args):
    """
    Run the server; can be AF_INET or AF_UNIX.
    """

    # AF_INET
    if args.af == "inet":
        if args.daemonize:
            # daemonize the server
            with daemon.DaemonContext():
                run_inet_server(args)
        else:
            # run in foreground
            run_inet_server(args)

    # AF_UNIX
    elif args.af == "unix":
        if args.daemonize:
            # daemonize the server
            with daemon.DaemonContext():
                run_unix_server(args)
        else:
            # run in foreground
            run_unix_server(args)


def get_logger(name, file_name):
    """
    Create a logger with <name>, that logs to <file_name>
    """

    # create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # create handler
    fileh = logging.FileHandler(file_name)
    fileh.setLevel(logging.DEBUG)

    # create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
        datefmt="%s")

    # add formatter to handler
    fileh.setFormatter(formatter)

    # add handler to logger
    logger.addHandler(fileh)

    # return logger to caller
    return logger


def init_loggers():
    """
    Initialize loggers for main log and account specific logs
    """

    # make sure logs directory exists
    logs_dir = ARGS.dir + "/logs"
    pathlib.Path(logs_dir).mkdir(parents=True, exist_ok=True)
    os.chmod(logs_dir, stat.S_IRWXU)

    # main log
    main_log = logs_dir + "/main.log"
    LOGGERS["main"] = get_logger("main", main_log)
    os.chmod(main_log, stat.S_IRUSR | stat.S_IWUSR)

    # account logs
    account_dir = logs_dir + "/account"
    pathlib.Path(account_dir).mkdir(parents=True, exist_ok=True)
    os.chmod(account_dir, stat.S_IRWXU)
    for acc in ACCOUNTS.keys():
        acc_dir = account_dir + "/{0}".format(acc)
        pathlib.Path(acc_dir).mkdir(parents=True, exist_ok=True)
        os.chmod(acc_dir, stat.S_IRWXU)
        acc_log = acc_dir + "/account.log"
        # logger name must be string
        LOGGERS[acc] = get_logger(str(acc), acc_log)
        os.chmod(acc_log, stat.S_IRUSR | stat.S_IWUSR)


def store_accounts():
    """
    Store accounts in a file.
    """

    accounts_file = pathlib.Path(ARGS.dir + "/accounts.pickle")
    with open(accounts_file, "wb") as acc_file:
        # make sure only user can read/write file before storing anything
        os.chmod(accounts_file, stat.S_IRUSR | stat.S_IWUSR)

        # Pickle accounts using the highest protocol available.
        pickle.dump(ACCOUNTS, acc_file, pickle.HIGHEST_PROTOCOL)


def load_accounts():
    """
    Load accounts from a file.
    """

    # make sure path and file exist
    pathlib.Path(ARGS.dir).mkdir(parents=True, exist_ok=True)
    os.chmod(ARGS.dir, stat.S_IRWXU)
    accounts_file = pathlib.Path(ARGS.dir + "/accounts.pickle")
    if not accounts_file.exists():
        return

    # make sure only user can read/write file before using it
    os.chmod(accounts_file, stat.S_IRUSR | stat.S_IWUSR)

    with open(accounts_file, "rb") as acc_file:
        # The protocol version used is detected automatically, so we do not
        # have to specify it.
        global ACCOUNTS
        ACCOUNTS = pickle.load(acc_file)


def get_command_line_args():
    """
    Parse the command line and return command line arguments:
        af:         address family
        address:    AF_INET listen address
        port:       AF_INET listen port
        sockfile:   AF_UNIX listen socket file within working directory
        dir:        working directory
        daemonize:  daemonize process?
    """

    # parse command line parameters
    parser = argparse.ArgumentParser(description="Run a basic nuqql daemon.")
    parser.add_argument("--af", choices=["inet", "unix"], default="inet",
                        help="socket address family: \"inet\" for AF_INET, \
                        \"unix\" for AF_UNIX")
    parser.add_argument("--address", default="localhost",
                        help="AF_INET listen address")
    parser.add_argument("--port", default=32000, help="AF_INET listen port")
    parser.add_argument("--sockfile", default="based.sock",
                        help="AF_UNIX socket file in DIR")
    parser.add_argument("--dir", default=os.getcwd() + "/nuqql-based",
                        help="working directory")
    parser.add_argument("-d", "--daemonize", action="store_true",
                        help="daemonize process (default: true)")
    # use global args variable for storage. TODO: change this?
    global ARGS
    ARGS = parser.parse_args()
    # return args


if __name__ == "__main__":
    # parse command line arguments
    get_command_line_args()

    # load accounts
    load_accounts()

    # initialize loggers
    init_loggers()

    # start server
    try:
        run_server(ARGS)
    except KeyboardInterrupt:
        sys.exit()
