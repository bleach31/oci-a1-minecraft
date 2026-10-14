#!/usr/bin/env python3
import argparse
import socket
import struct
import time


MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


def ping(host: str, port: int, timeout: float) -> str:
    packet = (
        b"\x01"
        + struct.pack(">Q", int(time.time() * 1000))
        + MAGIC
        + struct.pack(">Q", 1)
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(timeout)
        client.sendto(packet, (host, port))
        response, _ = client.recvfrom(4096)
    if response[:1] != b"\x1c" or len(response) < 36:
        raise RuntimeError("unexpected Bedrock ping response")
    return response[35:].decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a Bedrock unconnected ping")
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19132)
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    print(ping(args.host, args.port, args.timeout))


if __name__ == "__main__":
    main()
