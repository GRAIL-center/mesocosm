"""A byte relay between a TCP port and a Unix socket, for contained environments.

A contained trial runs in its own network namespace, where only loopback exists. The recording gateway
listens on the host. Two relays join them through one Unix socket that is bound into the sandbox: on the
host, the socket forwards to the gateway's port; inside the sandbox, a loopback port forwards to the
socket. Nothing else leaves the sandbox.

Run as a script inside the sandbox: ``python relay.py PORT SOCKET -- COMMAND...`` serves the port, runs
the command and exits with its status. Standard library only.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading

Address = tuple[str, int] | str
CHUNK = 65536


def _open(address: Address) -> socket.socket:
    family = socket.AF_UNIX if isinstance(address, str) else socket.AF_INET
    return socket.socket(family, socket.SOCK_STREAM)


def _pump(source: socket.socket, sink: socket.socket) -> None:
    try:
        while data := source.recv(CHUNK):
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for end, how in ((sink, socket.SHUT_WR), (source, socket.SHUT_RD)):
            try:
                end.shutdown(how)
            except OSError:
                pass


def _serve(server: socket.socket, target: Address) -> None:
    while True:
        try:
            client, _ = server.accept()
        except OSError:
            return  # the listener was closed
        try:
            upstream = _open(target)
            upstream.connect(target)
        except OSError:
            client.close()
            continue
        threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
        threading.Thread(target=_pump, args=(upstream, client), daemon=True).start()


def start_relay(listen: Address, target: Address) -> socket.socket:
    """Forward every connection made to ``listen`` to ``target``. Close the returned listener to stop."""
    server = _open(listen)
    if not isinstance(listen, str):
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(listen)
    server.listen(64)
    threading.Thread(target=_serve, args=(server, target), daemon=True).start()
    return server


if __name__ == "__main__":
    port, path, separator, *command = sys.argv[1:]
    if separator != "--" or not command:
        sys.exit("usage: relay.py PORT SOCKET -- COMMAND...")
    start_relay(("127.0.0.1", int(port)), path)
    sys.exit(subprocess.run(command, check=False).returncode)
