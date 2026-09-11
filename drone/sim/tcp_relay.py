#!/usr/bin/env python3
"""Minimal bidirectional TCP relay.

Bridges hosts that cannot route to each other directly. In the distributed
bring-up (see BRINGUP_DISTRIBUTED.md) the drone box reaches the Mac's Godot
IPC port through the GCS box, which is the only host with a route to both.

Usage: python3 tcp_relay.py <listen_host> <listen_port> <target_host> <target_port>
"""
import asyncio
import sys


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


async def handle(client_reader, client_writer, target_host, target_port):
    peer = client_writer.get_extra_info("peername")
    try:
        remote_reader, remote_writer = await asyncio.open_connection(target_host, target_port)
    except Exception as e:
        print(f"[relay] {peer} -> {target_host}:{target_port} failed: {e}", flush=True)
        client_writer.close()
        return
    print(f"[relay] {peer} <-> {target_host}:{target_port} established", flush=True)
    await asyncio.gather(
        pipe(client_reader, remote_writer),
        pipe(remote_reader, client_writer),
    )
    print(f"[relay] {peer} closed", flush=True)


async def main():
    listen_host, listen_port, target_host, target_port = sys.argv[1:5]
    listen_port, target_port = int(listen_port), int(target_port)

    async def on_client(r, w):
        await handle(r, w, target_host, target_port)

    server = await asyncio.start_server(on_client, listen_host, listen_port)
    print(f"[relay] listening on {listen_host}:{listen_port} -> {target_host}:{target_port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
