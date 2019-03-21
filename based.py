#!/usr/bin/env python3

import socketserver
import argparse
import pathlib
import daemon
import sys
import os

accounts = {}


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
    return "info: new account added."


def handleAccountBuddies(account, params):
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

    # valid account?
    if account not in accounts.keys():
        return "error: invalid account"

    # get buddies for account
    replies = []
    for buddy in accounts[account].buddies:
        # filter online buddies if wanted by client
        if online and buddy.status != "Available":
            continue

        # construct replies
        reply = "buddy: {0} status: {1} name: {2} alias: {3}".format(
            account, buddy.status, buddy.name, buddy.alias)
        replies.append(reply)

    # return replies as single string with "\r\n" as line separator.
    # BaseHandler.handle will add the final "\r\n"
    return "\r\n".join(replies)


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
        account = int(parts[1])     # TODO: check for conversion errors?
        command = parts[2]
        params = parts[3:]
    else:
        # invalid command, ignore
        return ""

    if command == "list":
        return handleAccountList()

    if command == "add":
        # TODO: currently this supports
        # "account <ID> add" and "account add <ID>", OK?
        return handleAccountAdd(params)

    if command == "buddies":
        return handleAccountBuddies(account, params)


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
    args = parser.parse_args()

    return args


if __name__ == "__main__":
    # start server
    try:
        runServer(getCommandLineArgs())
    except KeyboardInterrupt:
        sys.exit()
