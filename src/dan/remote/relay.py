"""Transparent TCP relay over an existing private network, including WebSockets.

No laptop tunnel or SSH credentials are needed by the running relay. Authentication
is enforced by the destination DAN service. Never bind this plaintext relay publicly.
"""
import argparse
import asyncio
import ipaddress


def private_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError("Use a specific private/VPN IP address")
    return str(address)


async def forward(reader, writer, host, port):
    upstream = None
    try:
        remote_reader, upstream = await asyncio.wait_for(asyncio.open_connection(host, port), 10)

        async def copy(source, destination):
            while chunk := await source.read(65536):
                destination.write(chunk)
                await destination.drain()

        tasks = [asyncio.create_task(copy(reader, upstream)), asyncio.create_task(copy(remote_reader, writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def serve(bind, port, target, target_port):
    server = await asyncio.start_server(lambda r, w: forward(r, w, target, target_port), bind, port)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", type=private_address, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--target", type=private_address, required=True)
    parser.add_argument("--target-port", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.bind, args.port, args.target, args.target_port))


if __name__ == "__main__":
    main()
