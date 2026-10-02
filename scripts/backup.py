"""对 Compose 数据卷执行停机备份，并只恢复到全新命名空间。"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

VOLUMES = (
    "mysql_data",
    "redis_data",
    "etcd_data",
    "minio_data",
    "milvus_data",
    "uploads",
)
IMAGE = "medizj-app:local"


def docker(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=True, **kwargs)


def backup(project: str, output: Path, compose_files: list[str], image: str) -> None:
    """应用和基础设施停止后归档，结束时恢复原先运行的容器。"""
    if output.exists():
        raise ValueError("备份目录已存在，禁止覆盖")
    compose = ["compose", "-p", project]
    for filename in compose_files:
        compose.extend(["-f", filename])
    running = docker(
        "ps",
        "-q",
        "--filter",
        f"label=com.docker.compose.project={project}",
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.split()
    if not running:
        raise ValueError("没有运行中的 Compose 容器")
    volume_names = json.loads(
        docker(
            *compose,
            "config",
            "--format",
            "json",
            stdout=subprocess.PIPE,
            text=True,
        ).stdout
    )["volumes"]
    sources = {key: volume_names[key]["name"] for key in VOLUMES}
    for source in sources.values():
        docker("volume", "inspect", source, stdout=subprocess.DEVNULL)
    output.mkdir(parents=True)
    manifest = {"format": 1, "project": project, "volumes": {}}
    stopped = False
    try:
        stopped = True
        docker("stop", "-t", "35", *running, stdout=subprocess.DEVNULL)
        for key, source in sources.items():
            target = output / f"{key}.tar"
            with target.open("wb") as archive:
                docker(
                    "run",
                    "--rm",
                    "--user",
                    "0",
                    "-v",
                    f"{source}:/source:ro",
                    image,
                    "python",
                    "-c",
                    "import sys,tarfile; "
                    "t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|'); "
                    "t.add('/source',arcname='.',filter=lambda m: "
                    "None if m.name.endswith('.sock') else m); t.close()",
                    stdout=archive,
                )
            with target.open("rb") as archived:
                manifest["volumes"][key] = hashlib.file_digest(
                    archived, "sha256"
                ).hexdigest()
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    finally:
        if stopped:
            docker("start", *running, stdout=subprocess.DEVNULL)


def restore(project: str, source: Path, image: str) -> None:
    """完整验证归档后恢复新卷，不允许覆盖已有数据卷。"""
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest["format"] != 1 or set(manifest["volumes"]) != set(VOLUMES):
        raise ValueError("备份格式不匹配")
    for key in VOLUMES:
        with (source / f"{key}.tar").open("rb") as archive:
            if (
                hashlib.file_digest(archive, "sha256").hexdigest()
                != manifest["volumes"][key]
            ):
                raise ValueError(f"备份校验失败: {key}")
        existing = subprocess.run(
            ["docker", "volume", "inspect", f"{project}_{key}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if existing.returncode == 0:
            raise ValueError(f"目标卷已存在: {project}_{key}")
    for key in VOLUMES:
        name = f"{project}_{key}"
        docker(
            "volume",
            "create",
            "--label",
            f"com.docker.compose.project={project}",
            "--label",
            f"com.docker.compose.volume={key}",
            name,
            stdout=subprocess.DEVNULL,
        )
        with (source / f"{key}.tar").open("rb") as archive:
            docker(
                "run",
                "--rm",
                "-i",
                "--user",
                "0",
                "-v",
                f"{name}:/target",
                image,
                "python",
                "-c",
                "import sys,tarfile; "
                "t=tarfile.open(fileobj=sys.stdin.buffer,mode='r|'); "
                "t.extractall('/target',filter=lambda m,p: "
                "tarfile.data_filter(m,p).replace(uid=m.uid,gid=m.gid))",
                stdin=archive,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--compose", action="append", default=[])
    parser.add_argument("--image", default=IMAGE)
    args = parser.parse_args()
    if args.action == "backup":
        backup(
            args.project, args.directory, args.compose or ["compose.yaml"], args.image
        )
    else:
        restore(args.project, args.directory, args.image)


if __name__ == "__main__":
    main()
