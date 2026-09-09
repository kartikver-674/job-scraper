"""`python -m sweep` — serve the UI on localhost and open a browser.

Binds 127.0.0.1 only. This process reads .env, so it must never be reachable
from the network.
"""

import socket
import threading
import webbrowser

from sweep.app import create_app


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    print(f"Sweep is running at {url}\nPress Ctrl+C to stop.")
    create_app().run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()
