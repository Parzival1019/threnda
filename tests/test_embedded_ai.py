"""Hermetic tests for the managed, checksum-pinned local-AI bundle."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from threnda.app.core.config import Settings
from threnda.app.services.ai import embedded
from threnda.app.services.ai.embedded import Artifact, EmbeddedAIError


def _artifact(path: Path, url: str = "https://example.test/artifact") -> Artifact:
    return Artifact(
        filename=path.name,
        url=url,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size=path.stat().st_size,
    )


def _runtime_archive(path: Path) -> None:
    payload = b"#!/bin/sh\nexit 0\n"
    info = tarfile.TarInfo("llama-test/llama-server")
    info.size = len(payload)
    info.mode = 0o755
    with tarfile.open(path, "w:gz") as package:
        package.addfile(info, io.BytesIO(payload))


def test_install_from_offline_bundle_verifies_and_extracts_everything(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    runtime_path = bundle / "runtime.tar.gz"
    model_path = bundle / "model.gguf"
    _runtime_archive(runtime_path)
    model_path.write_bytes(b"GGUF" + b"synthetic-model-data")
    runtime = _artifact(runtime_path)
    model = _artifact(model_path)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, ai_data_dir=str(tmp_path / "installed")
    )

    with (
        patch.dict(embedded._RUNTIME_ARTIFACTS, {("TestOS", "testarch"): runtime}, clear=True),
        patch.object(embedded, "_MODEL_ARTIFACTS", (model,)),
        patch("threnda.app.services.ai.embedded.platform.system", return_value="TestOS"),
        patch("threnda.app.services.ai.embedded.platform.machine", return_value="testarch"),
        patch.object(embedded, "_health", return_value=False),
    ):
        status = embedded.install_embedded_ai(settings, bundle_dir=bundle)

    paths = embedded.embedded_ai_paths(settings)
    assert status.installed is True
    assert paths.server.is_file()
    assert paths.server.stat().st_mode & 0o100
    assert (paths.models / "model.gguf").read_bytes().startswith(b"GGUF")
    assert '"stage": "ready"' in paths.state.read_text(encoding="utf-8")


def test_install_rejects_a_tampered_offline_artifact(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    runtime_path = bundle / "runtime.tar.gz"
    _runtime_archive(runtime_path)
    runtime = _artifact(runtime_path)
    runtime_path.write_bytes(b"tampered")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, ai_data_dir=str(tmp_path / "installed")
    )

    with (
        patch.dict(embedded._RUNTIME_ARTIFACTS, {("TestOS", "testarch"): runtime}, clear=True),
        patch.object(embedded, "_MODEL_ARTIFACTS", ()),
        patch("threnda.app.services.ai.embedded.platform.system", return_value="TestOS"),
        patch("threnda.app.services.ai.embedded.platform.machine", return_value="testarch"),
        pytest.raises(EmbeddedAIError, match="failed verification"),
    ):
        embedded.install_embedded_ai(settings, bundle_dir=bundle)

    assert '"stage": "failed"' in embedded.embedded_ai_paths(settings).state.read_text(
        encoding="utf-8"
    )


def test_runtime_archive_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    payload = b"bad"
    info = tarfile.TarInfo("../../outside")
    info.size = len(payload)
    with tarfile.open(archive, "w:gz") as package:
        package.addfile(info, io.BytesIO(payload))
    paths = embedded.embedded_ai_paths(
        Settings(_env_file=None, ai_data_dir=str(tmp_path / "installed"))  # type: ignore[call-arg]
    )
    paths.root.mkdir(parents=True)

    with pytest.raises(EmbeddedAIError, match="unsafe path"):
        embedded._extract_runtime(archive, paths)
    assert not (tmp_path / "outside").exists()


def test_unsupported_platform_is_reported_without_network_or_execution(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, ai_data_dir=str(tmp_path / "installed")
    )
    with (
        patch("threnda.app.services.ai.embedded.platform.system", return_value="Plan9"),
        patch("threnda.app.services.ai.embedded.platform.machine", return_value="mips"),
        patch.object(embedded, "urlopen") as mocked,
    ):
        status = embedded.embedded_ai_status(settings)

    assert status.supported is False
    assert status.installed is False
    mocked.assert_not_called()
