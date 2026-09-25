"""Bounded public audio retrieval and local decoding, with no retained audio files."""
from __future__ import annotations

import http.client
import json
import math
import socket
import ssl
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlunsplit

from ..learning.providers import ProviderUnavailable, TextResult
from .network import FetchError, public_target

# Playlists and formats capable of fetching remote segments are intentionally absent.
AUDIO_FORMATS = 'aac,flac,mp3,ogg,wav,mov,matroska,webm,asf'


@dataclass(frozen=True)
class AudioLimits:
    max_bytes: int = 128 * 1024 * 1024
    max_duration_seconds: float = 7200
    download_timeout_seconds: float = 120
    decode_timeout_seconds: float = 180

    def __post_init__(self):
        for value in (self.max_bytes, self.max_duration_seconds, self.download_timeout_seconds, self.decode_timeout_seconds):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid_audio_limits')
        if not isinstance(self.max_bytes, int):
            raise ValueError('invalid_audio_limits')


def download_audio(url, path: Path, limits=AudioLimits()):
    """Pin every redirect to a validated public address; never use ambient proxies."""
    deadline = time.monotonic() + limits.download_timeout_seconds
    path = Path(path)
    created = False
    try:
        for _ in range(5):
            parts, hostname, port, address = public_target(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError('audio_download_timeout')
            connection = http.client.HTTPConnection(hostname, port, timeout=min(15, remaining))
            raw_socket = None
            try:
                raw_socket = socket.create_connection((address, port), timeout=min(15, remaining))
                if parts.scheme == 'https':
                    raw_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=hostname)
                connection.sock = raw_socket
                target = urlunsplit(('', '', parts.path or '/', parts.query, ''))
                connection.request('GET', target, headers={'User-Agent': 'Knowpipe/1.0 podcast audio reader',
                    'Accept-Encoding': 'identity', 'Connection': 'close'})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader('Location')
                    if not location:
                        raise FetchError('invalid_redirect')
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise FetchError('audio_http_error')
                if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                    raise FetchError('unsupported_encoding')
                length = response.getheader('Content-Length')
                expected = int(length) if length is not None else None
                if expected is not None and (expected < 0 or expected > limits.max_bytes):
                    raise FetchError('audio_too_large')
                size = 0
                with path.open('xb') as output:
                    created = True
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise FetchError('audio_download_timeout')
                        raw_socket.settimeout(min(15, remaining))
                        chunk = response.read1(min(65536, limits.max_bytes - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > limits.max_bytes:
                            raise FetchError('audio_too_large')
                        output.write(chunk)
                if not size or expected is not None and size != expected:
                    raise FetchError('audio_incomplete_download')
                return size
            finally:
                connection.close()
                if raw_socket is not None:
                    raw_socket.close()
        raise FetchError('too_many_redirects')
    except Exception as exc:
        if created:
            path.unlink(missing_ok=True)
        if isinstance(exc, FetchError):
            raise
        if isinstance(exc, (OSError, ValueError, http.client.HTTPException)):
            raise FetchError('audio_fetch_failed') from exc
        raise


def validate_audio(path: Path, limits=AudioLimits()):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file',
            '-format_whitelist', AUDIO_FORMATS, '-show_entries', 'format=duration:stream=codec_type',
            '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30)
        info = json.loads(result.stdout)
        duration = float(info.get('format', {}).get('duration', 0))
        if not any(stream.get('codec_type') == 'audio' for stream in info.get('streams', [])):
            raise ValueError('audio_stream_missing')
        if not math.isfinite(duration) or duration <= 0 or duration > limits.max_duration_seconds:
            raise ValueError('audio_duration_exceeded')
        return duration
    except FileNotFoundError as exc:
        raise ProviderUnavailable('audio_decoder_unavailable') from exc
    except (subprocess.SubprocessError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError('invalid_audio') from exc


def normalize_audio(path: Path, destination: Path, limits=AudioLimits()):
    """Bound actual decoded duration, not just untrusted container metadata."""
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file',
            '-format_whitelist', AUDIO_FORMATS, '-i', str(path), '-map', '0:a:0', '-vn',
            '-t', str(limits.max_duration_seconds + 1), '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', str(destination)], capture_output=True, check=True,
            timeout=limits.decode_timeout_seconds)
        with wave.open(str(destination), 'rb') as decoded:
            duration = decoded.getnframes() / decoded.getframerate()
        if duration <= 0 or duration > limits.max_duration_seconds:
            raise ValueError('audio_duration_exceeded')
        return destination
    except FileNotFoundError as exc:
        raise ProviderUnavailable('audio_decoder_unavailable') from exc
    except (subprocess.SubprocessError, wave.Error, EOFError) as exc:
        raise ValueError('audio_decode_failed') from exc


def transcribe_url(url, transcriber, limits=AudioLimits()):
    # Local model configuration can exist while its files have not been mounted.
    # Check before any network work; generic protocol implementations remain valid.
    preflight = getattr(transcriber, 'check_available', None)
    if callable(preflight):
        preflight()
    with tempfile.TemporaryDirectory(prefix='knowpipe-audio-') as directory:
        path = Path(directory) / 'download.audio'
        download_audio(url, path, limits)
        validate_audio(path, limits)
        normalized = normalize_audio(path, Path(directory) / 'normalized.wav', limits)
        result = transcriber.transcribe(normalized)
        if not isinstance(result, TextResult) or result.complete is not True or not result.text.strip():
            raise ValueError('incomplete_transcription')
        return result
