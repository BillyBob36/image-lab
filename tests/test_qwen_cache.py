import hashlib
import io
import json
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest
from unittest.mock import patch


class PersistentCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.cache = base / 'mounted' / 'version'
        (self.cache / 'model').mkdir(parents=True)
        self.model = b'verified model weights'
        (self.cache / 'model' / 'weights.safetensors').write_bytes(self.model)
        bundle = self.cache / 'venv.tar.gz'
        with tarfile.open(bundle, 'w:gz') as tar:
            info = tarfile.TarInfo('venv/bin/python')
            info.size = len(b'python')
            tar.addfile(info, io.BytesIO(b'python'))
        self.manifest = {'archiveSha256': 'expected-version', 'modelFiles': [
            {'path': 'model/weights.safetensors', 'bytes': len(self.model), 'sha256': hashlib.sha256(self.model).hexdigest()}
        ], 'environment': {'file': 'venv.tar.gz', 'bytes': bundle.stat().st_size, 'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()}}
        (self.cache / 'manifest.json').write_text(json.dumps(self.manifest))
        self.root = base / 'local'
        self.config = base / 'private.json'
        self.config.write_text(json.dumps({'modelRoot': str(self.root), 'persistentCacheRoot': str(self.cache),
                                          'archiveSha256': 'expected-version', 'statusWrite': 'invalid-url'}))
        self.progress = base / 'progress.json'

    def prepare(self, mounted=True):
        script = Path(__file__).resolve().parents[1] / 'qwen/remote/prepare.py'
        with patch('sys.argv', [str(script), str(self.config)]), patch.object(type(self.cache), 'is_mount', return_value=mounted), patch('subprocess.run') as check_gpu:
            runpy.run_path(str(script), run_name='__main__')
        return json.loads(self.progress.read_text()), check_gpu

    def test_cache_restores_verified_weights_environment_and_reuses_local_copy(self):
        state, check = self.prepare()
        self.assertEqual(state['stage'], 'ready')
        self.assertTrue(state['cache'])
        self.assertEqual((self.root / 'model/weights.safetensors').read_bytes(), self.model)
        self.assertTrue((self.root / 'venv/bin/python').exists())
        self.assertFalse((self.config.parent / 'venv.tar.gz').exists())
        check.assert_called_once()
        # A warm replica keeps its verified local files; it needs no remote reads.
        (self.cache / 'manifest.json').unlink()
        state, _ = self.prepare()
        self.assertEqual(state['stage'], 'ready')

    def test_corrupt_cache_fails_before_model_is_marked_ready(self):
        (self.cache / 'model/weights.safetensors').write_bytes(b'x' * len(self.model))
        state, check = self.prepare()
        self.assertEqual(state['stage'], 'failed')
        self.assertIn('Poids du cache incorrects', state['error'])
        self.assertFalse((self.root / 'qwen-studio-model.json').exists())
        check.assert_not_called()

    def test_environment_shards_extract_in_parallel_without_shared_directory_errors(self):
        shards = []
        for index in range(4):
            bundle = self.cache / f'venv-{index}.tar.gz'
            with tarfile.open(bundle, 'w:gz') as tar:
                info = tarfile.TarInfo(f'venv/lib/shared/package-{index}.py')
                info.size = 1
                tar.addfile(info, io.BytesIO(b'x'))
                if index == 0:
                    info = tarfile.TarInfo('venv/bin/python')
                    info.size = 1
                    tar.addfile(info, io.BytesIO(b'x'))
            shards.append({'file': bundle.name, 'bytes': bundle.stat().st_size, 'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()})
        self.manifest['environmentShards'] = shards
        (self.cache / 'manifest.json').write_text(json.dumps(self.manifest))
        state, _ = self.prepare()
        self.assertEqual(state['stage'], 'ready', state)
        for index in range(4):
            self.assertEqual((self.root / f'venv/lib/shared/package-{index}.py').read_bytes(), b'x')

    def test_missing_mount_never_downloads_or_prepares_another_model(self):
        state, check = self.prepare(mounted=False)
        self.assertEqual(state['stage'], 'failed')
        self.assertIn('n’est pas monté', state['error'])
        self.assertFalse(self.root.exists())
        check.assert_not_called()
