"""Offline reviewer probes; all data synthetic; no external sockets or credentials."""
import io, json, ssl, sys, tempfile, unittest, http.client, socket, threading, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_github_retry import retry, Clock, FakeGh, FakeOpener, FakeResponse, included, CORE_URL, BODY_SHA

class ReviewerTests(unittest.TestCase):
    def test_buffered_download_obeys_180_second_deadline(self):
        clock = Clock()
        class Drip(io.RawIOBase):
            def __init__(self):
                self.header = bytearray(b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n')
                self.remaining = 20
            def readable(self): return True
            def readinto(self, b):
                if self.header:
                    n=min(len(b),len(self.header)); b[:n]=self.header[:n]; del self.header[:n]; return n
                if not self.remaining: return 0
                clock.now += 20  # each socket receive is under the 30s idle limit
                self.remaining -= 1; b[0]=120; return 1
        class Sock:
            def makefile(self, *a, **kw): return io.BufferedReader(Drip())
        response = http.client.HTTPResponse(Sock()); response.begin()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(retry.GitHubReadError):
                retry.download(CORE_URL, Path(d)/'archive', 'synthetic-review-token',
                               opener=FakeOpener(response), **clock.bounds())
        self.assertLessEqual(clock.now, 180, f'buffered read consumed {clock.now}s')

    def test_tls_handshake_failure_is_not_retried(self):
        opener=FakeOpener(ssl.SSLError(1, 'synthetic TLS handshake failure'), FakeResponse())
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(retry.GitHubReadError):
                retry.download(CORE_URL, Path(d)/'archive', 'synthetic-review-token',
                               BODY_SHA, opener, **Clock().bounds())
        self.assertEqual(len(opener.requests), 1)

    def test_real_gh_tls_failure_is_not_retried(self):
        # Local CONNECT proxy returns an invalid TLS record; never connects outward.
        try:
            listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(1)
        except PermissionError:
            self.skipTest('sandbox denies loopback sockets')
        listener.settimeout(5)
        proxy=f'http://127.0.0.1:{listener.getsockname()[1]}'
        def serve():
            with listener:
                conn,_=listener.accept()
                with conn:
                    conn.settimeout(3)
                    conn.recv(8192)
                    conn.sendall(b'HTTP/1.1 200 Connection established\r\n\r\nnot a TLS record\r\n')
        thread=threading.Thread(target=serve,daemon=True); thread.start()
        calls=[]
        with tempfile.TemporaryDirectory() as cfg:
            def run(args, **kwargs):
                calls.append(args)
                if len(calls)>1:
                    kwargs['stdout'].write(included(200,b'{}'))
                    return subprocess.CompletedProcess(args,0)
                env={'PATH':'/usr/bin:/bin', 'GH_CONFIG_DIR':cfg, 'GH_TOKEN':'synthetic-review-token',
                     'HTTPS_PROXY':proxy,'HTTP_PROXY':proxy,'ALL_PROXY':proxy,'NO_PROXY':''}
                result=subprocess.run(args,env=env,**kwargs)
                self.assertEqual(result.returncode,1)
                self.assertEqual(kwargs['stdout'].tell(),0)
                return result
            with self.assertRaises(retry.GitHubReadError):
                retry.bounded_gh_read('repos/o/r/pulls/1',4000,run,**Clock().bounds())
        thread.join(5)

    def test_pagination_rejects_more_than_5000_events(self):
        fake=FakeGh((included(200,json.dumps([{'id':n} for n in range(5001)]).encode()),0),
                    (included(200,b'[]'),0))
        with self.assertRaises(ValueError):
            retry.api_get('repos/o/r/issues/1/timeline?per_page=100',50,fake,**Clock().bounds())

    def test_retry_after_delay_is_capped_at_60_including_jitter(self):
        self.assertLessEqual(retry.backoff_delay(1,3600,0.75),60)

if __name__ == '__main__': unittest.main(verbosity=2)
