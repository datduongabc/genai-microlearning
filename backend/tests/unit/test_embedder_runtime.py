"""The launcher selects the supported GPU image while keeping an explicit CPU fallback."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / 'devops/scripts/compose.sh'


@pytest.mark.parametrize(('device', 'capability', 'gpu'), [
    ('auto', '8.6', True), ('auto', '', False), ('cpu', '8.6', False), ('gpu', '', True),
])
def test_compose_launcher_selects_runtime(tmp_path, device, capability, gpu):
    docker = tmp_path / 'docker'
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    docker.chmod(0o755)
    nvidia = tmp_path / 'nvidia-smi'
    nvidia.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_GPU_CAPABILITY"\n')
    nvidia.chmod(0o755)
    result = subprocess.run(
        [str(LAUNCHER), '-p', 'fm-do-an-media-ingest-speedup-config', 'config'],
        env={**os.environ, 'PATH': f'{tmp_path}:/usr/bin:/bin', 'EMBEDDER_DEVICE': device,
             'TEST_GPU_CAPABILITY': capability},
        capture_output=True, text=True, check=True,
    )
    args = result.stdout.splitlines()
    assert args[:4] == ['compose', '--project-directory', str(ROOT), '--env-file']
    assert args[-3:] == ['-p', 'fm-do-an-media-ingest-speedup-config', 'config']
    assert (str(ROOT / 'devops/docker-compose.gpu.yml') in args) is gpu
