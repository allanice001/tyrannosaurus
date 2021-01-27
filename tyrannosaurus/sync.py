"""
Original source: https://github.com/dmyersturnbull/tyrannosaurus
Copyright 2020–2021 Douglas Myers-Turnbull
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0

Module that syncs metadata from pyproject.toml.
"""
from __future__ import annotations

import logging
import re
import textwrap
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union

from tyrannosaurus.enums import License
from tyrannosaurus.context import Context

logger = logging.getLogger(__package__)


class Sync:
    def __init__(self, context: Context):
        self.context = context

    def sync(self) -> Sequence[str]:  # pragma: no cover
        self.fix_init()
        self.fix_recipe()
        self.fix_codemeta()
        self.fix_citation()
        return [str(s) for s in self.context.targets]

    def has(self, key: str):
        return self.context.has_target(key) and self.context.path_source(key).exists()

    def fix_init(self) -> Sequence[str]:  # pragma: no cover
        if self.has("init"):
            return self.fix_init_internal(self.context.path / self.context.project / "__init__.py")
        return []

    def fix_init_internal(self, init_path: Path) -> Sequence[str]:
        return self._replace_substrs(
            init_path,
            {
                "__status__ = ": f'__status__ = "{self.context.source("status")}"',
                "__copyright__ = ": f'__copyright__ = "{self.context.source("copyright")}"',
                "__date__ = ": f'__date__ = "{self.context.source("date")}"',
            },
        )

    def fix_citation(self) -> Sequence[str]:
        if not self.has("citation"):
            return []
        return self._replace_substrs(
            self.context.path_source("citation"),
            {
                re.compile("^version: .*$"): f"version: {self.context.version}",
                re.compile("^abstract: .*$"): f"abstract: {self.context.description}",
            },
        )

    def fix_codemeta(self) -> Sequence[str]:
        if not self.has("codemeta"):
            return []
        return self._replace_substrs(
            self.context.path_source("codemeta"),
            {
                re.compile(' {4}"version" *: *"'): f'"version":"{self.context.version}"',
                re.compile(
                    ' {4}"description" *: *"'
                ): f'"description":"{self.context.description}"',
                re.compile(' {4}"license" *: *"'): f'"description":"{self.context.license.url}"',
            },
        )

    def fix_recipe(self) -> Sequence[str]:  # pragma: no cover
        if self.has("recipe"):
            return self.fix_recipe_internal(self.context.path_source("recipe"))
        return []

    def fix_recipe_internal(self, recipe_path: Path) -> Sequence[str]:
        # TODO this is all quite bad
        # Well, I guess this is still an alpha release
        python_vr = self.context.deps["python"]
        pat = re.compile(r"github:([a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38})")
        summary = self._careful_wrap(self.context.poetry("description"))
        if "long_description" in self.context.sources:
            long_desc = self._careful_wrap(self.context.source("long_description"))
        else:
            long_desc = summary
        poetry_vr = self.context.build_sys_reqs["poetry"]
        maintainers = self.context.source("maintainers")
        maintainers = [m.group(1) for m in pat.finditer(maintainers)]
        maintainers = "\n    - ".join(maintainers)
        # the pip >= 20 gets changed for BOTH test and host; this is OK
        # The same is true for the poetry >=1.1,<2.0 line: it's added to both sections
        lines = self._replace_substrs(
            recipe_path,
            {
                "{% set version = ": '{% set version = "' + str(self.context.version) + '" %}',
                "    - python >=": f"    - python {python_vr.replace(' ', '')}",
                re.compile(
                    "^ {4}- pip *$"
                ): f"    - pip >=20\n    - poetry {poetry_vr.replace(' ', '')}",
            },
        )
        new_lines = self._until_line(lines, "about:")
        last_section = f"""
about:
  home: {self.context.poetry("homepage")}
  summary: |
    {summary}
  license_family: {self.context.license.family}
  license: {self.context.license.spdx}
  license_file: LICENSE.txt
  description: |
    {long_desc}
  doc_url: {self.context.poetry("documentation")}
  dev_url: {self.context.poetry("repository")}

extra:
  recipe-maintainers:
    - {maintainers}
"""
        final_lines = [*new_lines, *last_section.splitlines()]
        final_lines = [x.rstrip(" ") for x in final_lines]
        final_str = "\n".join(final_lines)
        final_str = re.compile(r"\n\s*\n").sub("\n\n", final_str)
        if not self.context.dry_run:
            recipe_path.write_text(final_str, encoding="utf8")
        logger.debug(f"Wrote to {recipe_path}")
        return final_str.split("\n")

    def _until_line(self, lines: Sequence[str], stop_at: str):
        new_lines = []
        for line in lines:
            if line.startswith(stop_at):
                break
            new_lines.append(line)
        return new_lines

    def _careful_wrap(self, s: str, indent: int = 4) -> str:
        txt = " ".join(s.split())
        width = self._get_line_length()
        # TODO: I don't know why replace_whitespace=True, drop_whitespace=True isn't sufficient
        return textwrap.fill(
            txt,
            width=width,
            subsequent_indent=" " * indent,
            break_long_words=False,
            break_on_hyphens=False,
            replace_whitespace=True,
            drop_whitespace=True,
        )

    def _replace_substrs(
        self,
        path: Path,
        replace: Mapping[Union[str, re.Pattern], str],
    ) -> Sequence[str]:
        if not self.context.dry_run:
            self.context.back_up(path)
        new_lines = "\n".join(
            [self._fix_line(line, replace) for line in path.read_text(encoding="utf8").splitlines()]
        )
        if not self.context.dry_run:
            path.write_text(new_lines, encoding="utf8")
        logger.debug(f"Wrote to {path}")
        return new_lines.splitlines()

    def _fix_line(self, line: str, replace: Mapping[Union[str, re.Pattern], str]) -> str:
        for k, v in replace.items():
            replace = self._replace(line, k, v)
            if replace is not None:
                return replace
        else:
            return line

    def _replace(self, line: str, k: Union[str, re.Pattern], v: str) -> Optional[str]:
        if isinstance(k, re.Pattern):
            if k.fullmatch(line) is not None:
                return k.sub(line, v)
        elif line.startswith(k):
            return v
        return None

    def _get_line_length(self) -> int:
        if "linelength" in self.context.sources:
            return int(self.context.source("linelength"))
        elif "tool.black.line-length" in self.context.data:
            return int(self.context.data["tool.black.line-length"])
        return 100


__all__ = ["Sync"]
