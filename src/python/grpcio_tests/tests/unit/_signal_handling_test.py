# Copyright 2019 the gRPC authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Test of responsiveness to signals."""

from __future__ import print_function

import logging
import os
import signal
import subprocess
import tempfile
import threading
import unittest
import sys

import grpc

from tests.unit import test_common
from tests.unit import _signal_client

_CLIENT_PATH = os.path.abspath(os.path.realpath(_signal_client.__file__))
_HOST = 'localhost'


class _GenericHandler(grpc.GenericRpcHandler):

    def __init__(self):
        self._connected_clients_lock = threading.RLock()
        self._connected_clients_event = threading.Event()
        self._connected_clients = 0

        self._unary_unary_handler = grpc.unary_unary_rpc_method_handler(
            self._handle_unary_unary)
        self._unary_stream_handler = grpc.unary_stream_rpc_method_handler(
            self._handle_unary_stream)

    def _on_client_connect(self):
        with self._connected_clients_lock:
            self._connected_clients += 1
            self._connected_clients_event.set()

    def _on_client_disconnect(self):
        with self._connected_clients_lock:
            self._connected_clients -= 1
            if self._connected_clients == 0:
                self._connected_clients_event.clear()

    def await_connected_client(self):
        """Blocks until a client connects to the server."""
        self._connected_clients_event.wait()

    def _handle_unary_unary(self, request, servicer_context):
        """Handles a unary RPC.

        Blocks until the client disconnects and then echoes.
        """
        stop_event = threading.Event()

        def on_rpc_end():
            self._on_client_disconnect()
            stop_event.set()

        servicer_context.add_callback(on_rpc_end)
        self._on_client_connect()
        stop_event.wait()
        return request

    def _handle_unary_stream(self, request, servicer_context):
        """Handles a server streaming RPC.

        Blocks until the client disconnects and then echoes.
        """
        stop_event = threading.Event()

        def on_rpc_end():
            self._on_client_disconnect()
            stop_event.set()

        servicer_context.add_callback(on_rpc_end)
        self._on_client_connect()
        stop_event.wait()
        yield request

    def service(self, handler_call_details):
        if handler_call_details.method == _signal_client.UNARY_UNARY:
            return self._unary_unary_handler
        elif handler_call_details.method == _signal_client.UNARY_STREAM:
            return self._unary_stream_handler
        else:
            return None


def _read_stream(stream):
    stream.seek(0)
    return stream.read()


class SignalHandlingTest(unittest.TestCase):

    def setUp(self):
        self._server = test_common.test_server()
        self._port = self._server.add_insecure_port('{}:0'.format(_HOST))
        self._handler = _GenericHandler()
        self._server.add_generic_rpc_handlers((self._handler,))
        self._server.start()

    def tearDown(self):
        self._server.stop(None)

    @unittest.skipIf(os.name == 'nt', 'SIGINT not supported on windows')
    def testUnary(self):
        """Tests that the server unary code path does not stall signal handlers."""
        server_target = '{}:{}'.format(_HOST, self._port)
        with tempfile.TemporaryFile(mode='r') as client_stdout:
            with tempfile.TemporaryFile(mode='r') as client_stderr:
                client = subprocess.Popen(
                    (sys.executable, _CLIENT_PATH, server_target, 'unary'),
                    stdout=client_stdout,
                    stderr=client_stderr)
                self._handler.await_connected_client()
                client.send_signal(signal.SIGINT)
                self.assertFalse(client.wait(), msg=_read_stream(client_stderr))
                client_stdout.seek(0)
                self.assertIn(_signal_client.SIGTERM_MESSAGE,
                              client_stdout.read())

    @unittest.skipIf(os.name == 'nt', 'SIGINT not supported on windows')
    def testStreaming(self):
        """Tests that the server streaming code path does not stall signal handlers."""
        server_target = '{}:{}'.format(_HOST, self._port)
        with tempfile.TemporaryFile(mode='r') as client_stdout:
            with tempfile.TemporaryFile(mode='r') as client_stderr:
                client = subprocess.Popen(
                    (sys.executable, _CLIENT_PATH, server_target, 'streaming'),
                    stdout=client_stdout,
                    stderr=client_stderr)
                self._handler.await_connected_client()
                client.send_signal(signal.SIGINT)
                self.assertFalse(client.wait(), msg=_read_stream(client_stderr))
                client_stdout.seek(0)
                self.assertIn(_signal_client.SIGTERM_MESSAGE,
                              client_stdout.read())


if __name__ == '__main__':
    logging.basicConfig()
    unittest.main(verbosity=2)
