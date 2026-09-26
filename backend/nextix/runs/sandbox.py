"""The agent sandbox: one locked-down Docker container per run.

`Sandbox` is the interface the worker uses; `DockerSandbox` implements it with the Docker
SDK (the worker has the host's Docker socket; sandboxes never do). Tests use a fake.
"""

import contextlib
import io
import logging
import tarfile
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol

log = logging.getLogger(__name__)

RUN_LABEL = "nextix.run_id"
# "<worker boot id>:<pid>" of the worker process watching the sandbox (see executor).
OWNER_LABEL = "nextix.owner"
OUT_DIR = "/work/.nextix-out"
RESULT_PATH = f"{OUT_DIR}/result.json"
BUNDLE_PATH = f"{OUT_DIR}/branch.bundle"


def container_name(run_id: uuid.UUID) -> str:
    return f"nextix-run-{run_id.hex[:12]}"


@dataclass(frozen=True)
class SandboxSpec:
    run_id: uuid.UUID
    image: str
    env: dict[str, str] = field(repr=False)  # holds credentials: never printed
    owner: str = ""
    network: str | None = None
    mem_limit: str = "4g"
    nano_cpus: int = 2_000_000_000
    pids_limit: int = 512


class Sandbox(Protocol):
    def start(self, spec: SandboxSpec) -> str:
        """Create and start the container; return its id."""
        ...

    def is_running(self, container_id: str) -> bool: ...

    def read_file(self, container_id: str, path: str) -> bytes | None:
        """A file from the (possibly stopped) container, or None if it isn't there."""
        ...

    def read_tree(self, container_id: str, path: str, *, max_bytes: int) -> dict[str, bytes] | None:
        """Regular files under a directory, keyed by their path relative to it.

        None if the directory isn't there, or its archive is bigger than ``max_bytes``.
        """
        ...

    def kill(self, container_id: str) -> None: ...

    def remove(self, container_id: str) -> None: ...

    def kill_run(self, run_id: uuid.UUID) -> bool:
        """Kill and remove any container labelled with this run; True if one existed."""
        ...

    def labelled_runs(self) -> dict[uuid.UUID, str]:
        """Every sandbox container that still exists, running or not: run id -> owner."""
        ...


class DockerSandbox:
    def __init__(self) -> None:
        import docker

        self._docker = docker.from_env()

    def start(self, spec: SandboxSpec) -> str:
        container = self._docker.containers.run(
            spec.image,
            detach=True,
            # A recognisable name in Docker Desktop, instead of a random one.
            name=container_name(spec.run_id),
            init=True,  # reap processes the agent leaves behind; the runner isn't PID 1
            environment=spec.env,
            labels={RUN_LABEL: str(spec.run_id), OWNER_LABEL: spec.owner},
            network=spec.network,
            user="1000:1000",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=spec.mem_limit,
            memswap_limit=spec.mem_limit,
            nano_cpus=spec.nano_cpus,
            pids_limit=spec.pids_limit,
            # Docker Desktop's names for the host machine lead to ports published there
            # (Postgres, Redis); point them nowhere. The API is reached as `api`.
            extra_hosts={
                "host.docker.internal": "127.0.0.1",
                "gateway.docker.internal": "127.0.0.1",
            },
            # No volumes, no binds, no Docker socket, no privileged mode.
        )
        return str(container.id)

    def _get(self, container_id: str):  # type: ignore[no-untyped-def]
        import docker.errors

        try:
            return self._docker.containers.get(container_id)
        except docker.errors.NotFound:
            return None

    def is_running(self, container_id: str) -> bool:
        container = self._get(container_id)
        if container is None:
            return False
        container.reload()
        return bool(container.status in ("created", "running", "restarting"))

    def read_file(self, container_id: str, path: str) -> bytes | None:
        import docker.errors

        container = self._get(container_id)
        if container is None:
            return None
        try:
            stream, _ = container.get_archive(path)
        except docker.errors.NotFound:
            return None
        archive = io.BytesIO(b"".join(stream))
        with tarfile.open(fileobj=archive) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    extracted = tar.extractfile(member)
                    return extracted.read() if extracted else None
        return None

    def read_tree(self, container_id: str, path: str, *, max_bytes: int) -> dict[str, bytes] | None:
        import docker.errors

        container = self._get(container_id)
        if container is None:
            return None
        try:
            stream, _ = container.get_archive(path)
        except docker.errors.NotFound:
            return None
        archive = io.BytesIO()
        for chunk in stream:
            archive.write(chunk)
            if archive.tell() > max_bytes:
                log.warning(
                    "%s in %s is over %d bytes; ignored", path, container_id[:12], max_bytes
                )
                return None
        archive.seek(0)
        files: dict[str, bytes] = {}
        with tarfile.open(fileobj=archive) as tar:
            for member in tar.getmembers():
                # Regular files only (no links or devices); drop the directory's own name.
                parts = PurePosixPath(member.name).parts[1:]
                if not member.isfile() or not parts or any(p in ("", ".", "..") for p in parts):
                    continue
                extracted = tar.extractfile(member)
                if extracted is not None:
                    files["/".join(parts)] = extracted.read()
        return files

    def kill(self, container_id: str) -> None:
        import docker.errors

        container = self._get(container_id)
        if container is None:
            return
        with contextlib.suppress(docker.errors.APIError):  # already stopped
            container.kill()

    def remove(self, container_id: str) -> None:
        import docker.errors

        container = self._get(container_id)
        if container is None:
            return
        try:
            container.remove(force=True)
        except docker.errors.APIError:
            log.warning("could not remove container %s", container_id[:12])

    def kill_run(self, run_id: uuid.UUID) -> bool:
        found = self._docker.containers.list(all=True, filters={"label": f"{RUN_LABEL}={run_id}"})
        for container in found:
            self.remove(str(container.id))
        return bool(found)

    def labelled_runs(self) -> dict[uuid.UUID, str]:
        found: dict[uuid.UUID, str] = {}
        for container in self._docker.containers.list(all=True, filters={"label": RUN_LABEL}):
            with contextlib.suppress(ValueError, TypeError):
                found[uuid.UUID(container.labels.get(RUN_LABEL))] = container.labels.get(
                    OWNER_LABEL, ""
                )
        return found
