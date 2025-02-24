import os
import shutil
from copy import deepcopy
from itertools import filterfalse
from pathlib import Path
from typing import Any

import tomlkit
from poetry.core.version.helpers import format_python_constraint

from poetry2rye.project import BasicDependency, PoetryProject
from poetry2rye.utils import get_next_backup_path


def read_name_email(string: str) -> dict[str, str]:
    name, _, email = string.rpartition(" ")
    return {"name": name, "email": email[1:-1]}


def convert(
    project_path: Path, ensure_src: bool = True, virtual_project: bool = False
) -> None:
    poetry_project = PoetryProject(project_path, ensure_src=ensure_src)

    project_sec = {}
    urls_sec = {}
    tool_rye_sec: dict[str, Any] = {"managed": True, "virtual": virtual_project}

    # required
    project_sec["name"] = poetry_project.project_name
    project_sec["version"] = poetry_project.poetry["version"]
    project_sec["description"] = poetry_project.poetry["description"]

    # license (not used due to different format)
    if poetry_project.poetry.get("license"):
        pass

    # authors / maintainers
    authors = list(map(read_name_email, poetry_project.poetry["authors"]))
    if authors:
        project_sec["authors"] = tomlkit.array()
        project_sec["authors"].extend(authors)

    maintainers = list(
        map(read_name_email, poetry_project.poetry.get("maintainers", []))
    )
    if maintainers:
        project_sec["maintainers"] = tomlkit.array()
        project_sec["maintainers"].extend(maintainers)

    # readme
    if poetry_project.poetry.get("readme"):
        project_sec["readme"] = poetry_project.poetry["readme"]

    # urls
    if poetry_project.poetry.get("homepage"):
        urls_sec["Homepage"] = poetry_project.poetry["homepage"]
    if poetry_project.poetry.get("repository"):
        urls_sec["Repository"] = poetry_project.poetry["repository"]
    if poetry_project.poetry.get("documentation"):
        urls_sec["Documentation"] = poetry_project.poetry["documentation"]

    # keywords / classifiers
    if poetry_project.poetry.get("keywords"):
        project_sec["keywords"] = poetry_project.poetry["keywords"]
    if poetry_project.poetry.get("classifiers"):
        project_sec["classifiers"] = poetry_project.poetry["classifiers"]

    # packages
    if poetry_project.poetry.get("packages"):
        # ToDo
        pass

    # dependencies
    project_sec["dependencies"] = tomlkit.array()
    for dep in poetry_project.dependencies:
        if dep.is_python_dep():
            assert isinstance(dep, BasicDependency)
            project_sec["requires-python"] = format_python_constraint(dep.version)
        else:
            if dep.is_dev:
                tool_rye_sec.setdefault("dev-dependencies", tomlkit.array())
                tool_rye_sec["dev-dependencies"].add_line(dep.to_str())
            else:
                project_sec["dependencies"].add_line(dep.to_str())
    if "dev-dependencies" in tool_rye_sec:
        tool_rye_sec["dev-dependencies"].add_line(indent="")
    project_sec["dependencies"].add_line(indent="")

    # project scripts (aka executables)
    if poetry_project.poetry.get("scripts"):
        project_sec["scripts"] = _convert_scripts(poetry_project.poetry["scripts"])

    with open(project_path / "pyproject.toml") as f:
        pyproject = tomlkit.load(f)

    # create result
    result = tomlkit.document()

    result["project"] = project_sec
    if urls_sec:
        result["project.urls"] = urls_sec

    # first add rye to tool section
    tool_table = tomlkit.table()
    tool_table.add("rye", tool_rye_sec)

    for name in pyproject.keys():
        if name == "project":
            continue
        elif name == "tool":
            # add items to tool table
            for key, value in pyproject["tool"].items():
                tool_table.add(key, value)
            # remove poetry
            tool_table.pop("poetry")
            result["tool"] = tool_table

        elif name == "build-system":
            if not virtual_project:
                result["build-system"] = deepcopy(pyproject["build-system"])
                result["build-system"]["requires"] = list(
                    filterfalse(
                        lambda x: "poetry-core" in x, result["build-system"]["requires"]
                    )
                )
                result["build-system"]["requires"].append("hatchling")
                result["build-system"]["build-backend"] = "hatchling.build"
        else:
            result[name] = deepcopy(pyproject[name])

    # handle build config if project is not virtual (virtual : only dependency manager)
    if not virtual_project:
        if ensure_src:
            packages = [f"src/{poetry_project.module_name}"]
        else:
            packages = [
                item["include"]
                for item in poetry_project.poetry.get("packages", [])
                if "include" in item
            ]

        result["tool"]["hatch"] = {"metadata": {"allow-direct-references": True}}
        result["tool"]["hatch"]["build"] = {
            "targets": {"wheel": {"packages": packages}}
        }

    project_backup = get_next_backup_path(project_path)
    shutil.copytree(project_path, project_backup, dirs_exist_ok=True, symlinks=True)
    print(f"created backup: {project_backup}")

    with open(project_path / "pyproject.toml", "w") as f:
        f.write(tomlkit.dumps(result))

    # find "poetry" in pyproject.toml and print it
    with open(project_path / "pyproject.toml") as f:
        for num, content in enumerate(f.readlines(), start=1):
            if "poetry" in content:
                print(f"Warning: found 'poetry' in line {num}: {content.strip()}")

    if (project_path / "poetry.lock").exists():
        os.remove(project_path / "poetry.lock")

    if ensure_src:
        if not (project_path / "src").exists():
            (project_path / "src").mkdir()
            shutil.move(
                poetry_project.module_path,
                project_path / "src" / poetry_project.module_name,
            )


def _convert_scripts(poetry_scripts):
    rye_scripts = {}
    for script_name, script_value in poetry_scripts.items():
        if isinstance(script_value, str):
            rye_scripts[script_name] = script_value
        elif isinstance(script_value, dict):
            # Handle more complex script definitions if needed
            rye_scripts[script_name] = script_value.get("callable", "")
    return rye_scripts
