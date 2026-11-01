"""Read a temporary private credential file only for this repository's HTTPS Git requests.

This restricts helper responses, not the underlying token's GitHub permissions.
The run launcher removes the private file after successfully pushing results.
"""

import json
import os
from pathlib import Path
import stat
import sys

REPOSITORY = 'codewithrk-108/phase-1-fastllamacpp'


def credentials_for(request, path):
    if (request.get('protocol') != 'https' or request.get('host') != 'github.com'
            or request.get('path', '').lstrip('/').removesuffix('.git') != REPOSITORY):
        return {}
    path = Path(path)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Credential file must be private')
    data = json.loads(path.read_text())
    return {'username': data['username'], 'password': data['password']}


def main():
    if len(sys.argv) != 2 or sys.argv[1] != 'get':
        return
    request = dict(line.rstrip('\n').split('=', 1) for line in sys.stdin if '=' in line)
    path = os.environ.get('PHASE1_GITHUB_CREDENTIAL',
                          str(Path.home() / '.config/phase1-full500-v1/github.json'))
    try:
        credential = credentials_for(request, path)
    except (OSError, ValueError, KeyError):
        return
    for key, value in credential.items():
        print(f'{key}={value}')
    if credential:
        print()


if __name__ == '__main__':
    main()
