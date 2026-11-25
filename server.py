#!/usr/bin/env python3
"""Cymatics audio fetch server.
POST / with {"url": "<youtube or soundcloud link>"} -> returns the audio as a WAV file.
Needs yt-dlp and ffmpeg on PATH. No other dependencies.

Environment (all optional):
  PORT           port to listen on (default 8080)
  ACCESS_KEY     if set, requests must send  Authorization: Bearer <key>
  MAX_MINUTES    longest track allowed (default 30)
  ALLOW_ANY      set to 1 to allow any site yt-dlp supports (default: YouTube + SoundCloud only)
"""
import json, os, re, shutil, subprocess, sys, tempfile, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get('PORT', '8080'))
KEY = os.environ.get('ACCESS_KEY', '').strip()
MAX_MIN = int(os.environ.get('MAX_MINUTES', '30'))
ALLOW_ANY = os.environ.get('ALLOW_ANY') == '1'
HOSTS = re.compile(r'^(.+\.)?(youtube\.com|youtu\.be|music\.youtube\.com|soundcloud\.com|snd\.sc)$', re.I)
SLOTS = threading.Semaphore(2)   # at most 2 downloads at once


def cors(h):
    h.send_header('Access-Control-Allow-Origin', '*')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept')
    h.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
    h.send_header('Access-Control-Expose-Headers', 'X-Filename')


class H(BaseHTTPRequestHandler):
    server_version = 'CymaticsFetch/1'

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); cors(self)
        self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); cors(self); self.end_headers()

    def do_GET(self):
        self._json(200, {'ok': True, 'service': 'cymatics-audio-fetch'})

    def do_POST(self):
        if KEY and self.headers.get('Authorization', '').replace('Api-Key ', 'Bearer ') != 'Bearer ' + KEY:
            return self._json(401, {'status': 'error', 'error': 'wrong or missing key'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
            link = (json.loads(self.rfile.read(n) or b'{}').get('url') or '').strip()
            u = urllib.parse.urlparse(link)
            if u.scheme not in ('http', 'https') or not u.hostname:
                raise ValueError('that is not a link')
            if not ALLOW_ANY and not HOSTS.match(u.hostname):
                raise ValueError('only YouTube and SoundCloud links are allowed')
        except Exception as e:
            return self._json(400, {'status': 'error', 'error': str(e)})
        if not SLOTS.acquire(timeout=60):
            return self._json(503, {'status': 'error', 'error': 'busy, try again in a minute'})
        tmp = tempfile.mkdtemp(prefix='cym-')
        try:
            cmd = ['yt-dlp', '--no-playlist', '--no-warnings', '--quiet', '--match-filter', 'duration<=%d' % (MAX_MIN * 60),
                   '-x', '--audio-format', 'wav', '--postprocessor-args', 'ffmpeg:-ar 44100 -ac 2',
                   '--print', 'after_move:filepath', '-o', os.path.join(tmp, '%(title).80B.%(ext)s'), link]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            files = [f for f in os.listdir(tmp) if f.lower().endswith('.wav')]
            if r.returncode != 0 or not files:
                msg = (r.stderr or r.stdout or 'download failed').strip().splitlines()
                return self._json(502, {'status': 'error', 'error': (msg[-1] if msg else 'download failed')[:200]})
            path = os.path.join(tmp, files[0]); size = os.path.getsize(path)
            self.send_response(200); cors(self)
            self.send_header('Content-Type', 'audio/wav'); self.send_header('Content-Length', str(size))
            self.send_header('X-Filename', urllib.parse.quote(files[0]))
            self.end_headers()
            with open(path, 'rb') as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
        except subprocess.TimeoutExpired:
            self._json(504, {'status': 'error', 'error': 'took too long'})
        except BrokenPipeError:
            pass
        except Exception as e:
            try: self._json(500, {'status': 'error', 'error': str(e)[:200]})
            except Exception: pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True); SLOTS.release()

    def log_message(self, fmt, *a):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % a))


if __name__ == '__main__':
    print('Cymatics audio fetch server on port %d%s' % (PORT, ' (key required)' if KEY else ''))
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
