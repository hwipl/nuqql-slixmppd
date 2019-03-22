#!/usr/bin/env python3

import socketserver
import argparse
import pathlib
import logging
import pickle
import daemon
import sys
import os

accounts = {}
loggers = {}
args = None


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

    def __init__(self, id=0, name="", type="dummy", user="dummy@dummy.com",
                 password="dummy_password", status="online", buddies=[]):
        self.id = id
        self.name = name
        self.type = type
        self.user = user
        self.password = password
        self.status = status
        self.buddies = buddies

    def sendMsg(self, user, msg):
        """
        Send message to user. Currently, this only logs the message
        """

        # log message
        log_msg = "message: to {0}: {1}".format(user, msg)
        loggers[self.id].info(log_msg)


class NuqqlBaseHandler(socketserver.BaseRequestHandler):
    """
    Request Handler for the server, instantiated once per client connection.

    This is limited to one client connection at a time. It should be fine for
    our basic use case.
    """

    buffer = b""

    def handle(self):
        # self.request is the client socket
        while True:
            self.data = self.request.recv(1024)
            # self.buffer += self.data.decode()
            self.buffer += self.data

            # try to find a complete message
            eom = self.buffer.find(b"\r\n")
            if eom == -1:
                continue

            # extract message from buffer
            msg = self.buffer[:eom]
            self.buffer = self.buffer[eom + 2:]

            # start message handling
            try:
                msg = msg.decode()
            except UnicodeDecodeError:
                # invalid message format, drop client
                return
            reply = handleMsg(msg)

            # if there's nothing to send back, just continue
            if len(reply) == 0:
                continue

            # construct reply and send it back
            reply = reply + "\r\n"
            reply = reply.encode()
            self.request.sendall(reply)


def handleAccountList():
    """
    List all accounts
    """

    replies = []
    for account in accounts.values():
        reply = "{0} ({1}) {2} {3} [{4}]".format(account.id, account.name,
                                                 account.type, account.user,
                                                 account.status)
        replies.append(reply)

    # log event
    log_msg = "account list: {0}".format(replies)
    loggers["main"].info(log_msg)

    # return a single string containing "\r\n" as line separator.
    # BaseHandler.handle will add the final "\r\n"
    return "\r\n".join(replies)


def handleAccountAdd(params):
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
    acc_id = len(accounts)
    acc_type = params[0]
    acc_user = params[1]
    acc_pass = params[2]
    new_acc = Account(id=acc_id, type=acc_type, user=acc_user,
                      password=acc_pass)

    # make sure the account does not exist
    for a in accounts.values():
        if a.type == new_acc.type and a.user == new_acc.user:
            return "info: account already exists."

    # new account; add it
    accounts[new_acc.id] = new_acc

    # store updated accounts in file
    storeAccounts()

    # create mew logger
    account_dir = args.dir + "/logs/account/{0}".format(a)
    pathlib.Path(account_dir).mkdir(parents=True, exist_ok=True)
    account_log = account_dir + "/account.log"
    loggers[a] = getLogger(a, account_log)

    # log event
    log_msg = "account new: id {0} type {1} user {2}".format(new_acc.id,
                                                             new_acc.type,
                                                             new_acc.user)
    loggers["main"].info(log_msg)

    return "info: new account added."


def handleAccountBuddies(acc_id, params):
    """
    Get buddies for a specific account. If params contains "online", filter
    online buddies.

    Expected format:
        account <ID> buddies [online]

    params does not include "account <ID> buddies"

    Returned messages should look like:
        buddy: <acc_id> status: <Offline/Available> name: <name> alias: <alias>
    """

    # filter online buddies?
    online = False
    if len(params) >= 1 and params[0].lower() == "online":
        online = True

    # get buddies for account
    replies = []
    for buddy in accounts[acc_id].buddies:
        # filter online buddies if wanted by client
        if online and buddy.status != "Available":
            continue

        # construct replies
        reply = "buddy: {0} status: {1} name: {2} alias: {3}".format(
            acc_id, buddy.status, buddy.name, buddy.alias)
        replies.append(reply)

    # log event
    log_msg = "account {0} buddies: {1}".format(acc_id, replies)
    loggers[acc_id].info(log_msg)

    # return replies as single string with "\r\n" as line separator.
    # BaseHandler.handle will add the final "\r\n"
    return "\r\n".join(replies)


def handleAccountSend(acc_id, params):
    """
    Send a message to a someone over a specific account.

    Expected format:
        account <ID> send <username> <msg>

    params does not include "account <ID> send"
    """

    user = params[0]
    msg = params[1:]

    # send message to user
    accounts[acc_id].sendMsg(user, msg)

    # check if it is an existing buddy
    for buddy in accounts[acc_id].buddies:
        if buddy.name == user:
            return ""

    # new buddy; add it to account
    new_buddy = Buddy(name=user, alias="")
    accounts[acc_id].buddies.append(new_buddy)

    # store updated accounts in file
    storeAccounts()

    # log event
    log_msg = "account {0}: new buddy: {1}".format(acc_id, user)
    loggers[acc_id].info(log_msg)

    return ""


# def handleAccount(parts, account, command, params):
def handleAccount(parts):
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
        acc_id = int(parts[1])          # TODO: check for conversion errors?
        command = parts[2]
        params = parts[3:]
        # valid account?
        if acc_id not in accounts.keys():
            return "error: invalid account"
    else:
        # invalid command, ignore
        return "error: invalid command"

    if command == "list":
        return handleAccountList()

    if command == "add":
        # TODO: currently this supports
        # "account <ID> add" and "account add <ID>", OK?
        return handleAccountAdd(params)

    if command == "buddies":
        return handleAccountBuddies(acc_id, params)

    if command == "send":
        return handleAccountSend(acc_id, params)


def handleMsg(msg):
    """
    Handle messages received from client
    """

    # get parts of message
    parts = msg.split()

    # account specific commands
    if len(parts) >= 2 and parts[0] == "account":
        return handleAccount(parts)

    # others
    # TODO: ver? who?
    # ignore rest for now...
    return ""


def runInetServer(args):
    """
    Run an AF_INET server
    """

    listen = (args.address, args.port)
    with socketserver.TCPServer(listen, NuqqlBaseHandler) as server:
        server.serve_forever()
    return


def runUnixServer(args):
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
        server.serve_forever()


def runServer(args):
    """
    Run the server; can be AF_INET or AF_UNIX.
    """

    # AF_INET
    if args.af == "inet":
        if args.daemonize:
            # daemonize the server
            with daemon.DaemonContext():
                runInetServer(args)
        else:
            # run in foreground
            runInetServer(args)

    # AF_UNIX
    elif args.af == "unix":
        if args.daemonize:
            # daemonize the server
            with daemon.DaemonContext():
                runUnixServer(args)
        else:
            # run in foreground
            runUnixServer(args)
    return


def getLogger(name, file_name):
    """
    Create a logger with <name>, that logs to <file_name>
    """

    # create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # create handler
    fh = logging.FileHandler(file_name)
    fh.setLevel(logging.DEBUG)

    # create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
        datefmt="%s")

    # add formatter to handler
    fh.setFormatter(formatter)

    # add handler to logger
    logger.addHandler(fh)

    # return logger to caller
    return logger


def initLoggers():
    """
    Initialize loggers for main log and account specific logs
    """

    # make sure logs directory exists
    logs_dir = args.dir + "/logs"
    pathlib.Path(logs_dir).mkdir(parents=True, exist_ok=True)

    # main log
    main_log = logs_dir + "/main.log"
    loggers["main"] = getLogger("main", main_log)

    # account logs
    account_dir = logs_dir + "/account"
    for a in accounts.keys():
        a_dir = account_dir + "/{0}".format(a)
        pathlib.Path(a_dir).mkdir(parents=True, exist_ok=True)
        a_log = a_dir + "/account.log"
        loggers[a] = getLogger(str(a), a_log)   # logger name must be string


def storeAccounts():
    """
    Store accounts in a file.
    """

    accounts_file = pathlib.Path(args.dir + "/accounts.pickle")
    with open(accounts_file, "wb") as f:
        # Pickle accounts using the highest protocol available.
        pickle.dump(accounts, f, pickle.HIGHEST_PROTOCOL)


def loadAccounts():
    """
    Load accounts from a file.
    """

    # make sure path and file exist
    pathlib.Path(args.dir).mkdir(parents=True, exist_ok=True)
    accounts_file = pathlib.Path(args.dir + "/accounts.pickle")
    if not accounts_file.exists():
        return

    with open(accounts_file, "rb") as f:
        # The protocol version used is detected automatically, so we do not
        # have to specify it.
        global accounts
        accounts = pickle.load(f)


def getCommandLineArgs():
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
    global args
    args = parser.parse_args()
    # return args


if __name__ == "__main__":
    # parse command line arguments
    getCommandLineArgs()

    # load accounts
    loadAccounts()

    # initialize loggers
    initLoggers()

    # start server
    try:
        runServer(args)
    except KeyboardInterrupt:
        sys.exit()
