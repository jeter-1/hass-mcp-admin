import asyncio, socket, ssl, sys
HOSTS = ("dv-g3b-emudhra.we-have-a-problem.com", "dv-g3b-noct-emudhra.we-have-a-problem.com", "dv-g3b-aaa-emudhra.we-have-a-problem.com", "dv-g3b-aaa-noct-emudhra.we-have-a-problem.com", "api.github.com", "raw.githubusercontent.com")
def stdlib(host):
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=15) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
            return "CONNECTED " + tls.version()
    except ssl.SSLCertVerificationError as exc:
        return "REJECTED " + exc.verify_message
    except OSError as exc:
        return "NETWORK_ERROR " + type(exc).__name__
async def via_aiohttp(host):
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session, session.get(f"https://{host}/", timeout=aiohttp.ClientTimeout(total=20)) as r:
            return f"CONNECTED http={r.status}"
    except aiohttp.ClientConnectorCertificateError as exc:
        return "REJECTED " + exc.certificate_error.verify_message
    except aiohttp.ClientError as exc:
        return "NETWORK_ERROR " + type(exc).__name__
for host in HOSTS:
    print(f"stdlib  {host}: {stdlib(host)}")
    if "--aiohttp" in sys.argv:
        print(f"aiohttp {host}: {asyncio.run(via_aiohttp(host))}")
