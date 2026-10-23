#!/usr/bin/env python3
"""Build CI-only Vane and signed Ossie provider wheels from the exact manifest."""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from packaging.tags import sys_tags

ROOT = Path(__file__).resolve().parents[1]
TRUST_IDENTITY = "vane-ci-test-key"
LICENSE_EXPRESSION = (
    "0BSD AND Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC "
    "AND MIT AND Unicode-DFS-2015 AND Zlib AND curl"
)


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+ " + shlex.join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def one_wheel(directory: Path, pattern: str) -> Path:
    wheels = list(directory.glob(pattern))
    if len(wheels) != 1:
        raise RuntimeError(
            f"expected one {pattern} in {directory}, found {len(wheels)}"
        )
    return wheels[0]


def license_files(vane_source: Path, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    with (vane_source / "pyproject.toml").open("rb") as source:
        patterns = tomllib.load(source)["project"]["license-files"]
    bundle = directory / "DuckDB-third-party.txt"
    sections = []
    for pattern in patterns:
        if not pattern.startswith("external/duckdb/") or pattern.startswith(
            "external/duckdb/extension/"
        ):
            continue
        matches = sorted(vane_source.glob(pattern))
        if not matches:
            raise RuntimeError(f"missing DuckDB license files: {pattern}")
        for path in matches:
            sections.append(
                f"Source: {path.relative_to(vane_source)}\n\n{path.read_text()}\n"
            )
    if not sections:
        raise RuntimeError("the Vane manifest declares no DuckDB license files")
    bundle.write_text("\n".join(sections), encoding="utf-8")
    result = [bundle]
    for name, source in (
        ("Ossie-MIT.txt", ROOT / "LICENSE"),
        ("fkYAML-MIT.txt", ROOT / "third_party/fkYAML/LICENSE.txt"),
        ("Vane-Apache-2.0.txt", vane_source / "LICENSE"),
        ("Vane-NOTICE.txt", vane_source / "NOTICE"),
        ("Bison-parser-notice.txt", vane_source / "LICENSES/Bison-parser-notice.txt"),
        (
            "Vane-native-dependencies.txt",
            vane_source / "LICENSES/vcpkg-binary-dependencies.txt",
        ),
    ):
        target = directory / name
        shutil.copyfile(source, target)
        result.append(target)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vane-source", required=True, type=Path)
    parser.add_argument("--ci-tools-source", required=True, type=Path)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--vcpkg-installed", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64" or args.jobs < 1:
        parser.error("the CI wheel lane requires Linux x86-64 and a positive job count")

    with (ROOT / "vane-extension.toml").open("rb") as source:
        pins = tomllib.load(source)
    if pins["ci_tools"]["repository"] != "AstroVela/vane-extension-ci-tools":
        raise RuntimeError("CI tools must come from AstroVela/vane-extension-ci-tools")
    ci_tools = args.ci_tools_source.resolve()
    actual = subprocess.check_output(
        ["git", "-C", str(ci_tools), "rev-parse", "HEAD"], text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "-C", str(ci_tools), "status", "--porcelain"], text=True
    ).strip()
    if actual != pins["ci_tools"]["revision"] or status:
        raise RuntimeError(
            "CI tools must be clean and at the manifest's exact revision before import"
        )
    tools_path = ci_tools / "scripts/vane_extension.py"
    spec = importlib.util.spec_from_file_location("_ossie_vane_tools", tools_path)
    tools = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = tools
    spec.loader.exec_module(tools)
    tools.verify_ci_tools_checkout(ci_tools, pins["ci_tools"]["revision"])
    manifest = tools.load_manifest(ROOT / "vane-extension.toml", ROOT)
    vane_source = args.vane_source.resolve()
    tools.resolve_vane_identity(vane_source, manifest)
    tools.reject_duckdb_local_extension_config(vane_source)
    toolchain = tools.resolve_vcpkg_toolchain(manifest)
    triplet = os.environ.get("VCPKG_TARGET_TRIPLET", "x64-linux-release")
    tools.require_vane_wheel_platform(triplet)
    prefix = args.vcpkg_installed.resolve() / triplet
    for name in ("arrow", "arrowflight"):
        if not (prefix / "share" / name).is_dir():
            raise RuntimeError(
                f"Vane native dependencies are missing: {prefix}/share/{name}"
            )

    build_dir, dist_dir = args.build_dir.resolve(), args.dist_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    if list(dist_dir.glob("*.whl")):
        raise RuntimeError("the output directory must not already contain wheels")
    prefix_config = build_dir / "vane-prefix.cmake"
    prefix_config.write_text(
        f'list(PREPEND CMAKE_PREFIX_PATH "{prefix}")\n', encoding="utf-8"
    )
    cmake_args = [
        "-DBUILD_DISTRIBUTED_EXCHANGE=ON",
        "-DOSSIE_VANE_DISTRIBUTED=ON",
        "-DEXTENSION_STATIC_BUILD=ON",
        "-DVANE_LOADABLE_EXTENSIONS=",
        f"-DDUCKDB_EXTENSION_CONFIGS={ROOT / 'extension_config_vane_loadable.cmake'}",
        "-DVANE_ENABLE_TEST_EXTENSION_SIGNING_KEY=ON",
        "-DVANE_ENABLE_TESTPYPI_EXTENSION_SIGNING_KEY=OFF",
        "-DENABLE_EXTENSION_AUTOLOADING=OFF",
        "-DENABLE_EXTENSION_AUTOINSTALL=OFF",
        f"-DOSSIE_VANE_ARTIFACT_DIRECTORY={build_dir / 'vane_extensions'}",
        "-DVCPKG_BUILD=ON",
        f"-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES={prefix_config}",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
        f"-DVCPKG_MANIFEST_DIR={ROOT}",
        f"-DVCPKG_INSTALLED_DIR={build_dir / 'vcpkg_installed'}",
        f"-DVCPKG_TARGET_TRIPLET={triplet}",
        *tools.cmake_compiler_launcher_args(),
    ]
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "CMAKE_ARGS",
            "CMAKE_PREFIX_PATH",
            "GITHUB_REF_NAME",
            "GITHUB_BASE_REF",
            "VANE_VERSION_BRANCH",
            "VCPKG_CHAINLOAD_TOOLCHAIN_FILE",
            "VCPKG_DEFAULT_HOST_TRIPLET",
            "VCPKG_DEFAULT_TRIPLET",
            "VCPKG_OVERLAY_PORTS",
            "VCPKG_OVERLAY_TRIPLETS",
        }
        and not key.startswith(("SKBUILD_", "SETUPTOOLS_SCM_PRETEND_VERSION"))
        and not (key.startswith("DUCKDB_") and key.endswith("_DIRECTORY"))
    }
    env.update(
        CMAKE_ARGS=shlex.join(cmake_args),
        CMAKE_BUILD_PARALLEL_LEVEL=str(args.jobs),
        CMAKE_GENERATOR="Ninja",
        DUCKDB_OSSIE_DIRECTORY=str(ROOT / "vane"),
        SKBUILD_BUILD_DIR=str(build_dir),
        SKBUILD_CMAKE_BUILD_TYPE="Release",
        VCPKG_MAX_CONCURRENCY=str(args.jobs),
    )

    with tempfile.TemporaryDirectory(
        prefix="vane-ossie-wheels-", dir=build_dir.parent
    ) as temporary:
        staging = Path(temporary)
        run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(staging),
                str(vane_source),
            ],
            env=env,
        )
        run(
            [
                "cmake",
                "--build",
                str(build_dir),
                "--target",
                "vane_ossie_artifact",
                "--parallel",
                str(args.jobs),
            ],
            env=env,
        )
        artifact = build_dir / "vane_extensions/ossie.duckdb_extension"
        symbols = subprocess.check_output(
            ["nm", "-D", "-C", "--undefined-only", str(artifact)], text=True
        )
        if any("duckdb::" in line for line in symbols.splitlines()):
            raise RuntimeError("the Ossie artifact has unresolved DuckDB C++ symbols")
        signed = staging / artifact.name
        run(
            [
                sys.executable,
                "-I",
                str(vane_source / "scripts/sign_test_dynamic_extension.py"),
                str(artifact),
                str(signed),
                "--private-key",
                str(vane_source / "external/duckdb/test/mbedtls/private.pem"),
            ]
        )
        platform_tag = next(
            tag.platform for tag in sys_tags() if tag.platform.startswith("manylinux_")
        )
        repaired = staging / "runtime"
        repaired.mkdir()
        run(
            [
                sys.executable,
                "-m",
                "auditwheel",
                "repair",
                "--plat",
                platform_tag,
                "--wheel-dir",
                str(repaired),
                str(one_wheel(staging, "vane_ai-*.whl")),
            ]
        )
        runtime = one_wheel(repaired, "vane_ai-*.whl")
        builder = staging / "builder"
        run([sys.executable, "-m", "venv", str(builder)])
        python = str(builder / "bin/python")
        run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(runtime),
                "packaging>=24.2",
            ]
        )
        command = [
            python,
            "-I",
            str(vane_source / "scripts/build_extension_wheel.py"),
            "--artifact",
            str(signed),
            "--extension-name",
            "ossie",
            "--output-directory",
            str(staging),
            "--platform-tag",
            platform_tag,
            "--trust-identity",
            TRUST_IDENTITY,
            "--license-expression",
            LICENSE_EXPRESSION,
        ]
        for path in license_files(vane_source, build_dir / "licenses"):
            command.extend(["--license-file", str(path)])
        run(command)
        provider = one_wheel(staging, "vane_extension_ossie-*.whl")
        run(
            [
                sys.executable,
                "-I",
                str(vane_source / "scripts/verify_extension_wheel.py"),
                "--base-wheel",
                str(runtime),
                "--extension-wheel",
                str(provider),
                "--extension-name",
                "ossie",
                "--trust-identity",
                TRUST_IDENTITY,
            ]
        )
        for wheel in (runtime, provider):
            shutil.copyfile(wheel, dist_dir / wheel.name)


if __name__ == "__main__":
    main()
