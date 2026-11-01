import json
from pathlib import Path
import tempfile
import unittest

from phase1.repo_credential_helper import credentials_for


class RepoCredentialHelperTest(unittest.TestCase):
    def test_only_serves_matching_repository_and_requires_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'credential.json'
            credential = {'username': 'test', 'password': 'not-a-real-token'}
            path.write_text(json.dumps(credential))
            path.chmod(0o600)
            request = dict(protocol='https', host='github.com',
                           path='codewithrk-108/phase-1-fastllamacpp.git')
            self.assertEqual(credentials_for(request, path), credential)
            for changes in [dict(host='example.com'), dict(protocol='http'),
                            dict(path='someone/another-repo.git'), dict(path='')]:
                self.assertEqual(credentials_for({**request, **changes}, path), {})
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                credentials_for(request, path)
