"""Bounded, cached public downloads with persistent Stack Exchange throttle state."""
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_HOSTS = {'api.stackexchange.com', 'docs.python.org', 'docs.djangoproject.com',
                 'arxiv.org', 'export.arxiv.org', 'info.arxiv.org', 'raw.githubusercontent.com',
                 'www.postgresql.org', 'docs.docker.com', 'api.github.com'}


class FetchStopped(RuntimeError):
    pass


class _AllowedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _check_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in ALLOWED_HOSTS or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('source_url_not_allowlisted')


class CachedClient:
    def __init__(self, root, *, max_requests=180, min_interval=1.0, max_bytes=64 * 1024 * 1024, transient_retries=0):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / 'network-state.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.max_requests, self.min_interval, self.max_bytes = max_requests, min_interval, max_bytes
        self.requests, self.cache_hits, self.last_request = 0, 0, 0
        self.transient_retries = min(2, max(0, transient_retries))
        if self.state.get('quota_day') != datetime.now(timezone.utc).date().isoformat():
            self.state.pop('se_quota_remaining', None)

    def _save(self):
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, indent=2), encoding='utf-8')
        temporary.replace(self.state_path)

    def _download(self, url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Knowpipe-course-fulltext/1.0', 'Accept-Encoding': 'identity'})
        try:
            with urllib.request.build_opener(_AllowedRedirects()).open(req, timeout=40) as response:
                raw = response.read(self.max_bytes + 1)
        except urllib.error.HTTPError as error:
            retry_after = error.headers.get('Retry-After')
            if retry_after:
                try:
                    until = time.time() + max(0, int(retry_after))
                except ValueError:
                    try:
                        until = parsedate_to_datetime(retry_after).timestamp()
                    except (TypeError, ValueError, OverflowError):
                        until = time.time() + 60
                self.state.setdefault('host_next_allowed', {})[urlsplit(url).hostname] = until
                self._save()
            raise FetchStopped('http_' + str(error.code)) from error
        except (urllib.error.URLError, OSError) as error:
            raise FetchStopped('network_unavailable') from error
        if len(raw) > self.max_bytes:
            raise FetchStopped('response_exceeds_limit')
        return raw

    def bytes(self, url):
        _check_url(url)
        name = hashlib.sha256(url.encode()).hexdigest()
        target, meta = self.root / (name + '.raw'), self.root / (name + '.json')
        if target.exists() and meta.exists():
            raw = target.read_bytes(); provenance = json.loads(meta.read_text())
            if hashlib.sha256(raw).hexdigest() != provenance['raw_sha256']:
                raise FetchStopped('cache_integrity_error')
            self.cache_hits += 1
            return raw, provenance
        if self.requests >= self.max_requests:
            raise FetchStopped('request_budget_exhausted')
        is_se = urlsplit(url).hostname == 'api.stackexchange.com'
        if is_se and self.state.get('se_quota_remaining') == 0:
            raise FetchStopped('stackexchange_quota_exhausted')
        delay = max(0, self.min_interval - (time.monotonic() - self.last_request),
                    self.state.get('se_next_allowed', 0) - time.time() if is_se else 0,
                    self.state.get('host_next_allowed', {}).get(urlsplit(url).hostname, 0) - time.time())
        if delay:
            if delay > 60:
                raise FetchStopped('source_backoff_pending')
            if delay > 1:
                print(json.dumps({'waiting_for_backoff_seconds': round(delay, 1)}), flush=True)
            time.sleep(delay)
        for attempt in range(self.transient_retries + 1):
            if self.requests >= self.max_requests:
                raise FetchStopped('request_budget_exhausted')
            self.requests += 1
            try:
                raw = self._download(url)
                break
            except FetchStopped as error:
                # API replays remain opt-in, and never retry access-denied/quota errors.
                if attempt == self.transient_retries or is_se or str(error) not in {'network_unavailable', 'http_500', 'http_502', 'http_503', 'http_504'}:
                    raise
                retry_delay = max(5 * (attempt + 1), self.state.get('host_next_allowed', {}).get(urlsplit(url).hostname, 0) - time.time())
                if retry_delay > 60:
                    raise FetchStopped('source_backoff_pending') from error
                print(json.dumps({'retrying_public_source': urlsplit(url).hostname, 'attempt': attempt + 2, 'delay_seconds': retry_delay}), flush=True)
                time.sleep(retry_delay)
            finally:
                self.last_request = time.monotonic()
        if is_se:
            data = json.loads(raw)
            if isinstance(data.get('quota_remaining'), int):
                self.state.update(se_quota_remaining=data['quota_remaining'], quota_day=datetime.now(timezone.utc).date().isoformat())
            if data.get('backoff'):
                self.state['se_next_allowed'] = time.time() + data['backoff']
            self._save()
            if data.get('error_id'):
                raise FetchStopped('stackexchange_api_error_' + str(data['error_id']))
        provenance = {'fetch_url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                      'raw_sha256': hashlib.sha256(raw).hexdigest(), 'raw_bytes': len(raw)}
        temporary = target.with_suffix('.tmp'); temporary.write_bytes(raw); temporary.replace(target)
        meta.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
        return raw, provenance

    def json(self, url):
        raw, provenance = self.bytes(url)
        return json.loads(raw), provenance
