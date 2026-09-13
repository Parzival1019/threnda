"""Managed llama.cpp runtime and model for zero-configuration local AI.

Release bundles may ship the artifacts in advance.  A smaller Threnda
distribution can install the exact same pinned, checksum-verified artifacts
on first use.  Nothing is executed until a user explicitly asks for AI.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from threnda.app.core.config import Settings

RUNTIME_VERSION = "b10516"
MODEL_NAME = "Qwen2.5 1.5B Instruct Q4_K_M"
MODEL_ID = "qwen2.5-1.5b-instruct-q4_k_m"
MODEL_REVISION = "91cad51170dc346986eccefdc2dd33a9da36ead9"
DOWNLOAD_TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class Artifact:
    filename: str
    url: str
    sha256: str
    size: int


_RUNTIME_ARTIFACTS = {
    ("Darwin", "arm64"): Artifact(
        "llama-b10516-bin-macos-arm64.tar.gz",
        "https://github.com/ggml-org/llama.cpp/releases/download/b10516/"
        "llama-b10516-bin-macos-arm64.tar.gz",
        "ee3324327d621026ae80c24031670e65fa62a0b23a3a027dbe2f65f240affd30",
        11_089_823,
    ),
    ("Darwin", "x86_64"): Artifact(
        "llama-b10516-bin-macos-x64.tar.gz",
        "https://github.com/ggml-org/llama.cpp/releases/download/b10516/"
        "llama-b10516-bin-macos-x64.tar.gz",
        "b7adecf7bd2cde577ddabee8357a72409165d8104f43b4acee9f1b98cc9c447a",
        11_395_897,
    ),
    ("Linux", "x86_64"): Artifact(
        "llama-b10516-bin-ubuntu-x64.tar.gz",
        "https://github.com/ggml-org/llama.cpp/releases/download/b10516/"
        "llama-b10516-bin-ubuntu-x64.tar.gz",
        "f263a91280471b4c33c4999d7c76259c0f3a0a53a0b3e692b2c0b84380137a35",
        16_667_775,
    ),
    ("Linux", "aarch64"): Artifact(
        "llama-b10516-bin-ubuntu-arm64.tar.gz",
        "https://github.com/ggml-org/llama.cpp/releases/download/b10516/"
        "llama-b10516-bin-ubuntu-arm64.tar.gz",
        "e7491dca79c9799fc3ae169675a79f5777d3027e31ffb08ae679e5e0a7ae3c97",
        13_529_264,
    ),
}

_MODEL_ARTIFACTS = (
    Artifact(
        "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        f"https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/"
        f"{MODEL_REVISION}/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
        1_117_320_736,
    ),
    Artifact(
        "QWEN-MODEL-LICENSE-APACHE-2.0.txt",
        f"https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/{MODEL_REVISION}/LICENSE",
        "832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e",
        11_343,
    ),
)


class EmbeddedAIError(RuntimeError):
    """The managed runtime is unsupported, missing, or failed to start."""


@dataclass(frozen=True)
class EmbeddedAIPaths:
    root: Path
    runtime: Path
    models: Path
    state: Path
    log: Path

    @property
    def server(self) -> Path:
        return self.runtime / "llama-server"

    @property
    def primary_model(self) -> Path:
        return self.models / _MODEL_ARTIFACTS[0].filename


@dataclass(frozen=True)
class EmbeddedAIStatus:
    supported: bool
    installed: bool
    runtime_installed: bool
    model_installed: bool
    running: bool
    installing: bool
    stage: str
    error: str | None
    model: str = MODEL_NAME
    runtime_version: str = RUNTIME_VERSION


_install_lock = threading.Lock()
_install_thread: threading.Thread | None = None
_server_lock = threading.Lock()
_server_process: subprocess.Popen[bytes] | None = None
_server_log: BinaryIO | None = None


def embedded_ai_paths(settings: Settings) -> EmbeddedAIPaths:
    root = Path(settings.ai_data_dir).expanduser().resolve()
    return EmbeddedAIPaths(
        root=root,
        runtime=root / "runtime",
        models=root / "models",
        state=root / "install-state.json",
        log=root / "llama-server.log",
    )


def _runtime_artifact() -> Artifact:
    machine = platform.machine().lower()
    if machine == "amd64":
        machine = "x86_64"
    try:
        return _RUNTIME_ARTIFACTS[(platform.system(), machine)]
    except KeyError as exc:
        raise EmbeddedAIError(
            f"Embedded AI is not yet packaged for {platform.system()} {machine}"
        ) from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path: Path, artifact: Artifact) -> bool:
    return (
        path.is_file() and path.stat().st_size == artifact.size and _sha256(path) == artifact.sha256
    )


def _present(path: Path, artifact: Artifact) -> bool:
    """Cheap status check; install already verifies the full checksum."""
    return path.is_file() and path.stat().st_size == artifact.size


def _write_state(paths: EmbeddedAIPaths, *, stage: str, error: str | None = None) -> None:
    paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"stage": stage, "error": error, "updated_at": time.time()}
    temporary = paths.state.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(paths.state)


def _download(artifact: Artifact, destination: Path, bundle_dir: Path | None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _verified(destination, artifact):
        return
    bundled = bundle_dir / artifact.filename if bundle_dir is not None else None
    if bundled is not None and bundled.is_file():
        if not _verified(bundled, artifact):
            raise EmbeddedAIError(f"Bundled artifact failed verification: {artifact.filename}")
        shutil.copyfile(bundled, destination)
        return

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as target:
            temporary = Path(target.name)
            with urlopen(artifact.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
                total = 0
                while chunk := response.read(CHUNK_SIZE):
                    total += len(chunk)
                    if total > artifact.size:
                        raise EmbeddedAIError(
                            f"Download exceeded expected size: {artifact.filename}"
                        )
                    target.write(chunk)
        if temporary is None or not _verified(temporary, artifact):
            raise EmbeddedAIError(f"Downloaded artifact failed verification: {artifact.filename}")
        temporary.replace(destination)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise EmbeddedAIError(f"Could not download {artifact.filename}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _extract_runtime(archive: Path, paths: EmbeddedAIPaths) -> None:
    with tempfile.TemporaryDirectory(dir=paths.root) as temporary_name:
        temporary = Path(temporary_name)
        with tarfile.open(archive, "r:gz") as package:
            destination_root = temporary.resolve()
            for member in package.getmembers():
                member_path = (temporary / member.name).resolve()
                if destination_root not in member_path.parents and member_path != destination_root:
                    raise EmbeddedAIError("Runtime archive contained an unsafe path")
            package.extractall(temporary, filter="data")  # noqa: S202 - paths validated above
        roots = [item for item in temporary.iterdir() if item.is_dir()]
        if len(roots) != 1 or not (roots[0] / "llama-server").is_file():
            raise EmbeddedAIError("Runtime archive did not contain llama-server")
        replacement = paths.root / "runtime-new"
        shutil.rmtree(replacement, ignore_errors=True)
        shutil.copytree(roots[0], replacement)
        replacement.joinpath("llama-server").chmod(0o755)
        shutil.rmtree(paths.runtime, ignore_errors=True)
        replacement.replace(paths.runtime)


def install_embedded_ai(settings: Settings, *, bundle_dir: Path | None = None) -> EmbeddedAIStatus:
    """Install pinned runtime/model artifacts, verifying every byte before use."""
    paths = embedded_ai_paths(settings)
    configured_bundle = (
        Path(settings.ai_bundle_dir).expanduser() if settings.ai_bundle_dir else None
    )
    selected_bundle = bundle_dir or configured_bundle
    with _install_lock:
        try:
            runtime = _runtime_artifact()
            _write_state(paths, stage="downloading runtime")
            archive = paths.root / runtime.filename
            _download(runtime, archive, selected_bundle)
            _write_state(paths, stage="installing runtime")
            _extract_runtime(archive, paths)
            archive.unlink(missing_ok=True)
            for index, artifact in enumerate(_MODEL_ARTIFACTS, start=1):
                _write_state(paths, stage=f"downloading model {index}/{len(_MODEL_ARTIFACTS)}")
                _download(artifact, paths.models / artifact.filename, selected_bundle)
            _write_state(paths, stage="ready")
        except EmbeddedAIError as exc:
            _write_state(paths, stage="failed", error=str(exc))
            raise
    return embedded_ai_status(settings)


def build_offline_bundle(output_dir: Path, *, source_dir: Path | None = None) -> Path:
    """Create a verified, platform-specific offline bundle and manifest."""
    runtime = _runtime_artifact()
    output = output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifacts = (runtime, *_MODEL_ARTIFACTS)
    for artifact in artifacts:
        _download(artifact, output / artifact.filename, source_dir)
    manifest = {
        "schema_version": 1,
        "runtime_version": RUNTIME_VERSION,
        "model": MODEL_NAME,
        "platform": {"system": platform.system(), "machine": platform.machine().lower()},
        "artifacts": [asdict(artifact) for artifact in artifacts],
    }
    temporary = output / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output / "manifest.json")
    return output


def start_embedded_ai_install(settings: Settings) -> bool:
    """Start a single background install and return false if one is active."""
    global _install_thread
    if _install_thread is not None and _install_thread.is_alive():
        return False

    def run() -> None:
        try:
            install_embedded_ai(settings)
        except EmbeddedAIError:
            return

    _install_thread = threading.Thread(target=run, name="threnda-ai-install", daemon=True)
    _install_thread.start()
    return True


def _health(settings: Settings) -> bool:
    try:
        with urlopen(  # noqa: S310 - fixed loopback URL
            f"http://127.0.0.1:{settings.ai_embedded_port}/health", timeout=0.5
        ) as response:
            return int(response.status) == 200
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def embedded_ai_status(settings: Settings) -> EmbeddedAIStatus:
    paths = embedded_ai_paths(settings)
    try:
        _runtime_artifact()
        supported = True
    except EmbeddedAIError:
        supported = False
    runtime_installed = paths.server.is_file()
    model_installed = all(_present(paths.models / item.filename, item) for item in _MODEL_ARTIFACTS)
    stage = "not installed"
    error: str | None = None
    if paths.state.is_file():
        try:
            state = json.loads(paths.state.read_text(encoding="utf-8"))
            stage = str(state.get("stage", stage))
            error_value = state.get("error")
            error = str(error_value) if error_value else None
        except (json.JSONDecodeError, OSError):
            stage = "state unavailable"
    installing = _install_thread is not None and _install_thread.is_alive()
    installed = supported and runtime_installed and model_installed
    return EmbeddedAIStatus(
        supported=supported,
        installed=installed,
        runtime_installed=runtime_installed,
        model_installed=model_installed,
        running=_health(settings) if installed else False,
        installing=installing,
        stage=stage,
        error=error,
    )


def ensure_embedded_ai_running(settings: Settings) -> str:
    """Start the managed loopback-only llama-server and wait until healthy."""
    global _server_log, _server_process
    if _health(settings):
        return f"http://127.0.0.1:{settings.ai_embedded_port}"
    status = embedded_ai_status(settings)
    if not status.installed:
        raise EmbeddedAIError("Embedded AI is not installed; use Settings > Local AI")
    paths = embedded_ai_paths(settings)
    with _server_lock:
        if not _health(settings):
            if _server_process is not None and _server_process.poll() is None:
                _server_process.terminate()
                _server_process.wait(timeout=5)
            env = os.environ.copy()
            library_key = (
                "DYLD_LIBRARY_PATH" if platform.system() == "Darwin" else "LD_LIBRARY_PATH"
            )
            env[library_key] = str(paths.runtime)
            args = [
                str(paths.server),
                "--model",
                str(paths.primary_model),
                "--host",
                "127.0.0.1",
                "--port",
                str(settings.ai_embedded_port),
                "--ctx-size",
                "8192",
                "--parallel",
                "1",
                "--jinja",
                "--no-webui",
            ]
            if platform.system() == "Darwin":
                args.extend(["--n-gpu-layers", "99"])
            _server_log = paths.log.open("ab")
            try:
                _server_process = subprocess.Popen(  # noqa: S603
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=_server_log,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
            except OSError as exc:
                _server_log.close()
                _server_log = None
                raise EmbeddedAIError("Could not start the embedded AI runtime") from exc

    deadline = time.monotonic() + settings.ai_startup_timeout_seconds
    while time.monotonic() < deadline:
        if _health(settings):
            return f"http://127.0.0.1:{settings.ai_embedded_port}"
        if _server_process is not None and _server_process.poll() is not None:
            raise EmbeddedAIError("Embedded AI runtime exited while loading the model")
        time.sleep(0.25)
    shutdown_embedded_ai()
    raise EmbeddedAIError("Embedded AI model did not become ready before the timeout")


def shutdown_embedded_ai() -> None:
    global _server_log, _server_process
    with _server_lock:
        if _server_process is not None and _server_process.poll() is None:
            _server_process.terminate()
            try:
                _server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _server_process.kill()
                _server_process.wait(timeout=5)
        _server_process = None
        if _server_log is not None:
            _server_log.close()
            _server_log = None


def status_dict(settings: Settings) -> dict[str, object]:
    """Stable serializable form used by the CLI and web UI."""
    return asdict(embedded_ai_status(settings))


atexit.register(shutdown_embedded_ai)
