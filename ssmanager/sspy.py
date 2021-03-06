import os
import time
import json
import logging
from subprocess import Popen, DEVNULL
from threading import Thread, Event
from socket import socket, AF_UNIX, SOCK_DGRAM

from . import Server, _Manager


class Manager(_Manager):

    def __init__(self, print_ss_log=True, manager_addr='/tmp/manager.sock',
                 client_addr='/tmp/manager-client.sock', ss_bin='/usr/bin/ssserver'):
        super().__init__()
        self._sock = None
        self._ss_bin = ss_bin
        self._ss_proc = None
        self._print_ss_log = print_ss_log
        self._manager_addr = manager_addr
        self._client_addr = client_addr
        self._ok = Event()

        self._recv_thread = Thread(target=self._receiving, daemon=True)

    def start(self):
        super().start()
        if self._print_ss_log:
            output = None  # inherited from self
        else:
            output = DEVNULL
        args = [self._ss_bin, '--manager-address', self._manager_addr,
                '-s', '127.0.1.2', '-p', '0']
        self._ss_proc = Popen(args, stdout=output, stderr=output)
        self._sock = socket(AF_UNIX, SOCK_DGRAM)
        self._sock.bind(self._client_addr)
        # Waiting for ssserver started.
        connected = False
        for t in 0.01, 0.1, 0.2, 0.4, 0.8, 1, 2, 4:
            time.sleep(t)
            try:
                self._sock.connect(self._manager_addr)
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            else:
                connected = True
                break
        if not connected:
            logging.critical('Cannot connect to ssserver process on %s.',
                             self._manager_addr)
            raise SSServerConnectionError()
        self._recv_thread.start()

        self._sock.send(b'remove: {"server": "127.0.1.2"}')
        self._ok.wait()
        logging.info('Manager started.')

    def stop(self):
        super().stop()
        if self._sock is not None:
            self._sock.close()
        if self._ss_proc is not None:
            self._ss_proc.terminate()
        if os.path.exists(self._manager_addr):
            os.remove(self._manager_addr)
        if os.path.exists(self._client_addr):
            os.remove(self._client_addr)

    def _start_instance(self, server):
        server.is_running = True
        config = server._config.copy()
        config['one_time_auth'] = config.pop('auth')
        self._sock.send(b'add: ' + json.dumps(config).encode())
        self._ok.wait()
        logging.debug('ss-server at %s:%d started.' % (server.host, server.port))

    def _stop_instance(self, server):
        server.is_running = False
        self._sock.send(b'remove: {"server_port":' + str(server.port).encode() + b'}')
        self._ok.wait()
        logging.debug('ss-server at %s:%d stopped.' % (server.host, server.port))

    def _receiving(self):
        while self._is_running:
            data, _ = self._sock.recvfrom(2048)
            if data == b'ok':
                self._ok.set()
                continue

            cmd, data = data.decode().split(':', 1)
            if cmd != 'stat':
                logging.info('Unknown cmd received from ss-server: ' + cmd)
                continue

            stat = json.loads(data.strip())
            for port, traffic in stat.items():
                port = int(port)
                if port not in self._servers:
                    logging.warning('Stat from unknown port (%s) received.' % port)
                    continue
                self._servers[port].traffic += traffic

class ServerAlreadyExistError(Exception):
    pass

class SSServerConnectionError(ConnectionError):
    pass
