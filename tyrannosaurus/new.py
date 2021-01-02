from __future__ import annotations

import logging
import os
import shutil
import stat
from pathlib import Path
from subprocess import CalledProcessError, check_call, check_output  # nosec
from typing import Sequence, Union, Optional, List

import typer

from tyrannosaurus import __version__ as global_tyranno_version
from tyrannosaurus.context import LiteralParser
from tyrannosaurus.helpers import License

logger = logging.getLogger(__package__)
cli = typer.Typer()


class VersionNotFoundError(LookupError):
    """
    The Git tag corresponding to the version was not found.
    """


class New:
    def __init__(
        self,
        name: str,
        license_name: Union[str, License],
        username: str,
        authors: Sequence[str],
        description: str,
        keywords: Sequence[str],
        version: str,
        should_track: bool,
        tyranno_vr: str,
    ):
        if isinstance(license_name, str):
            license_name = License[license_name.lower()]
        self.project_name = name.lower()
        self.pkg_name = name.replace("_", "").replace("-", "").replace(".", "").lower()
        self.license_name = license_name
        self.username = username
        self.authors = authors
        self.description = description
        self.keywords = keywords
        self.version = version
        self.should_track = should_track
        self.repo_to_track = f"https://github.com/{username}/{name.lower()}.git"
        self.tyranno_vr = str(tyranno_vr)

    def create(self, path: Path) -> None:
        self._checkout(Path(str(path).lower()))
        logger.info("Got git checkout. Fixing...")
        # remove tyrannosaurus-specific files
        Path(path / "poetry.lock").unlink()
        Path(path / "recipes" / "tyrannosaurus" / "meta.yaml").unlink()
        Path(path / "recipes" / "tyrannosaurus").rmdir()
        for p in Path(path / "docs").iterdir():
            if p.is_file() and p.name not in {"conf.py", "requirements.txt"}:
                p.unlink()
        shutil.rmtree(str(path / "tests" / "resources"))
        for p in Path(path / "tests").iterdir():
            if p.is_file() and p.name != "__init__.py":
                p.unlink()
        # copy license
        parser = LiteralParser(
            self.project_name,
            self.username,
            self.authors,
            self.description,
            self.keywords,
            self.version,
            self.license_name.name,
        )
        license_file = (
            path / "tyrannosaurus" / "resources" / ("license_" + self.license_name.name + ".txt")
        )
        if license_file.exists():
            text = parser.parse(license_file.read_text(encoding="utf8"))
            Path(path / "LICENSE.txt").write_text(text, encoding="utf8")
        else:
            logger.error(f"License file for {license_file.name} not found")
        # copy resources, overwriting
        for source in (path / "tyrannosaurus" / "resources").iterdir():
            if not Path(source).is_file():
                continue
            resource = Path(source).name
            if not resource.startswith("license_"):
                # TODO replace project with pkg
                resource = (
                    str(resource)
                    .replace(".py.txt", ".py")
                    .replace(".toml.txt", ".toml")
                    .replace("$project", self.project_name)
                    .replace("$pkg", self.pkg_name)
                )
                dest = path / Path(*resource.split("@"))
                if dest.name.startswith("-"):
                    dest = Path(
                        *reversed(dest.parents),
                        "." + dest.name[1:],
                    )
                dest.parent.mkdir(parents=True, exist_ok=True)
                text = parser.parse(source.read_text(encoding="utf8"))
                dest.write_text(text, encoding="utf8")
        # rename some files
        Path(path / self.pkg_name).mkdir(exist_ok=True)
        Path(path / "recipes" / self.pkg_name).mkdir(parents=True)
        (path / "tyrannosaurus" / "__init__.py").rename(Path(path / self.pkg_name / "__init__.py"))
        shutil.rmtree(str(path / "tyrannosaurus"))
        if self.should_track:
            self._track(path)

    def _track(self, path: Path) -> None:
        is_initialized = self._call(
            ["git", "init"], cwd=path, fail="Failed calling git init. Giving up."
        )
        if is_initialized:
            self._call(
                ["pre-commit", "install"], cwd=path, fail="Failed calling pre-commit install."
            )
            is_tracked = self._call(
                ["git", "remote", "add", "origin", self.repo_to_track],
                cwd=path,
                fail=f"Failed tracking {self.repo_to_track}",
            )
            if is_tracked:
                self._call(
                    ["git", "branch", "--set-upstream-to=origin/main", "main"],
                    cwd=path,
                    fail=f"Failed setting upstream to {self.repo_to_track}",
                )
        logger.info(f"Initialized new git repo tracking remote {self.repo_to_track}")

    def _checkout(self, path: Path) -> None:
        if path.exists():
            raise FileExistsError(f"Path {path} already exists")
        try:
            path.parent.mkdir(exist_ok=True, parents=True)
            logger.info("Running git clone...")
            self._call(
                ["git", "clone", "https://github.com/dmyersturnbull/tyrannosaurus.git", str(path)]
            )
            tyranno_vr = self._parse_tyranno_vr()
            if tyranno_vr is not None:
                self._call(
                    ["git", "checkout", f"tags/{self.tyranno_vr}"],
                    cwd=path,
                    fail=VersionNotFoundError(f"Git tag '{self.tyranno_vr}' was not found."),
                )
        finally:
            self._murder_evil_path_for_sure(path / ".git")

    def _call(
        self,
        cmd: List[str],
        cwd: Optional[Path] = None,
        succeed: Optional[str] = None,
        fail: Union[None, str, BaseException] = None,
    ) -> bool:
        kwargs = {} if cwd is None else dict(cwd=str(cwd))
        try:
            check_call(cmd, **kwargs)  # nosec
        except CalledProcessError:
            logger.debug(f"Failed calling {' '.join(cmd)} in {cwd}", exc_info=True)
            if fail is not None and isinstance(fail, BaseException):
                raise fail
            elif fail is not None:
                logger.error(fail)
            return False
        else:
            logger.debug(f"Succeeded calling {' '.join(cmd)} in {cwd}", exc_info=True)
            if succeed is not None:
                logger.info(succeed)
            return True

    def _parse_tyranno_vr(self) -> Optional[str]:
        vr = self.tyranno_vr.lower().strip()
        if vr == "latest":
            return None
        elif vr == "current":
            return "v" + global_tyranno_version
        elif vr == "stable":
            return check_output(["git", "describe", "--abbrev=0", "--tags"], encoding="utf8")
        elif vr.startswith("v"):
            return vr
        else:
            return "v" + vr

    def _murder_evil_path_for_sure(self, evil_path: Path) -> None:
        """
        There are likely to be permission issues with .git directories.

        Args:
            evil_path: The .git directory
        """
        try:
            shutil.rmtree(str(evil_path))
        except OSError:
            logger.debug("Could not delete .git with rmtree", exc_info=True)

            def on_rm_error(func, path, exc_info):
                # from: https://stackoverflow.com/questions/4829043/how-to-remove-read-only-attrib-directory-with-python-in-windows
                os.chmod(path, stat.S_IWRITE)
                os.unlink(path)

            shutil.rmtree(str(evil_path), onerror=on_rm_error)


__all__ = ["New"]
