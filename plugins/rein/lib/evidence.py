"""Bounded, read-only checks of explicitly cited local files. No command execution."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat

MAX_REFERENCES = 64
MAX_FILE_BYTES = 2 * 1024 * 1024


def check(records, roots=None):
    """Validate freshness/location, not the truth of an agent's interpretation.

    Roots are supplied by the caller, never taken from the untrusted report.
    Hash a file once per invocation even when several symbols cite it.
    """
    if not isinstance(records, dict) or len(records) > MAX_REFERENCES:
        raise ValueError('evidence must be an object with at most 64 references')
    roots = {name: Path(path).resolve() for name, path in (roots or {}).items()}
    cache, result = {}, {}
    for key, ref in records.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(ref, dict):
            raise ValueError('evidence IDs must name reference objects')
        item = {'status': 'invalid', 'reference': dict(ref)}
        result[key] = item
        path, digest = ref.get('path'), ref.get('sha256')
        lines = ref.get('lines')
        if (not isinstance(path, str) or not path or Path(path).is_absolute()
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or not isinstance(lines, list) or len(lines) != 2
                or any(type(n) is not int for n in lines) or not 1 <= lines[0] <= lines[1]):
            item['reason'] = 'relative path, SHA-256 and positive inclusive line range required'
            continue
        repo = ref.get('repo', '.')
        if not isinstance(repo, str) or repo not in roots:
            item.update(status='unavailable', reason='repository root not supplied')
            continue
        try:
            target = (roots[repo] / path).resolve()
            target.relative_to(roots[repo])
            if target not in cache:
                metadata = target.stat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
                    raise ValueError('citation must be a regular file of at most 2 MiB')
                with target.open('rb') as fh:
                    data = fh.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError('citation exceeded 2 MiB')
                cache[target] = (hashlib.sha256(data).hexdigest(), data.decode('utf-8').splitlines())
            actual, content = cache[target]
            if actual != digest:
                item.update(status='stale', reason='file content changed')
            elif lines[1] > len(content):
                item['reason'] = 'line range exceeds file'
            elif 'symbol' in ref and (not isinstance(ref['symbol'], str) or not ref['symbol'].strip()
                    or ref['symbol'] not in '\n'.join(content[lines[0] - 1:lines[1]])):
                item['reason'] = 'symbol text absent from cited lines'
            else:
                item.update(status='current', reason='content and location checked; interpretation not verified')
        except (OSError, ValueError, RuntimeError) as exc:
            item['reason'] = str(exc)
    return result
