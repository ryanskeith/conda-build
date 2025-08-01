# Copyright (C) 2014 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
"""
This module tests the build API.  These are high-level integration tests.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tarfile
import uuid
from collections import OrderedDict
from contextlib import nullcontext
from glob import glob
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING

# for version
import conda
import pytest
import yaml
from binstar_client.commands import remove, show
from binstar_client.errors import NotFound
from conda import __version__ as conda_version
from conda.base.context import context, reset_context
from conda.common.compat import on_linux, on_mac, on_win
from conda.exceptions import ClobberError, CondaError, CondaMultiError, LinkError
from conda.utils import url_path
from conda_index.api import update_index
from packaging.version import Version

from conda_build import __version__, api, exceptions
from conda_build.config import Config
from conda_build.exceptions import (
    BuildScriptException,
    CondaBuildException,
    CondaBuildUserError,
    DependencyNeedsBuildingError,
    OverDependingError,
    OverLinkingError,
    RecipeError,
)
from conda_build.os_utils.external import find_executable
from conda_build.render import finalize_metadata
from conda_build.utils import (
    check_call_env,
    check_output_env,
    convert_path_for_cygwin_or_msys2,
    copy_into,
    env_var,
    get_conda_operation_locks,
    package_has_file,
    prepend_bin_path,
    rm_rf,
    walk,
)

from .utils import (
    add_mangling,
    fail_dir,
    get_valid_recipes,
    metadata_dir,
    metadata_path,
    reset_config,
)

if TYPE_CHECKING:
    from pytest import CaptureFixture, FixtureRequest, LogCaptureFixture, MonkeyPatch
    from pytest_mock import MockerFixture

    from conda_build.metadata import MetaData


def represent_ordereddict(dumper, data):
    value = []

    for item_key, item_value in data.items():
        node_key = dumper.represent_data(item_key)
        node_value = dumper.represent_data(item_value)

        value.append((node_key, node_value))

    return yaml.nodes.MappingNode("tag:yaml.org,2002:map", value)


yaml.add_representer(OrderedDict, represent_ordereddict)


class AnacondaClientArgs:
    def __init__(
        self, specs, token=None, site=None, log_level=logging.INFO, force=False
    ):
        from binstar_client.utils import parse_specs

        self.specs = [parse_specs(specs)]
        self.spec = self.specs[0]
        self.token = token
        self.site = site
        self.log_level = log_level
        self.force = force


def describe_root(cwd=None):
    if not cwd:
        cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    tag = check_output_env(["git", "describe", "--abbrev=0"], cwd=cwd).rstrip()
    tag = tag.decode("utf-8")
    return tag


# This tests any of the folders in the test-recipes/metadata folder that don't start with _
@pytest.mark.slow
@pytest.mark.serial
@pytest.mark.parametrize(
    "recipe",
    [
        pytest.param(recipe, id=recipe.name)
        for recipe in get_valid_recipes(metadata_dir)
    ],
)
@pytest.mark.flaky(reruns=3, reruns_delay=1)  # Add flaky marker for recipe builds
def test_recipe_builds(
    recipe: Path,
    testing_config,
    monkeypatch: pytest.MonkeyPatch,
    conda_build_test_recipe_envvar: str,
):
    # TODO: After we fix #3754 this mark can be removed. This specific test
    #   ``source_setup_py_data_subdir`` reproduces the problem.
    if recipe.name == "source_setup_py_data_subdir":
        pytest.xfail("Issue related to #3754 on conda-build.")
    elif recipe.name == "unicode_all_over" and context.solver == "libmamba":
        pytest.xfail("Unicode package names not supported in libmamba.")
    elif recipe.name == "numpy_build_run" and sys.version_info >= (3, 13):
        pytest.xfail("Numpy build doesn't run on Python 3.13 yet.")
    elif recipe.name == "numpy_build" and sys.version_info >= (3, 13):
        pytest.xfail("Numpy build doesn't run on Python 3.13 yet.")

    # These variables are defined solely for testing purposes,
    # so they can be checked within build scripts
    testing_config.activate = True
    monkeypatch.setenv("CONDA_TEST_VAR", "conda_test")
    monkeypatch.setenv("CONDA_TEST_VAR_2", "conda_test_2")
    api.build(str(recipe), config=testing_config)


@pytest.mark.slow
@pytest.mark.serial
def test_python_version_independent(
    testing_config,
    monkeypatch: pytest.MonkeyPatch,
):
    recipe = os.path.join(metadata_dir, "_python_version_independent")
    testing_config.activate = True
    monkeypatch.setenv("CONDA_TEST_VAR", "conda_test")
    monkeypatch.setenv("CONDA_TEST_VAR_2", "conda_test_2")
    output = api.build(str(recipe), config=testing_config)[0]
    subdir = os.path.basename(os.path.dirname(output))
    assert subdir != "noarch"


@pytest.mark.serial
@pytest.mark.skipif(
    "CI" in os.environ and "GITHUB_WORKFLOW" in os.environ,
    reason="This test does not run on Github Actions yet. We will need to adjust "
    "where to look for the pkgs. The github action for setup-miniconda sets "
    "pkg_dirs to conda_pkgs_dir.",
)
# Regardless of the reason for skipping, we should definitely find a better way for tests to look for the packages
# Rather than assuming they will be at $ROOT/pkgs since that can change and we don't care where they are in terms of the
# tests.
def test_ignore_prefix_files(testing_config, monkeypatch):
    recipe = os.path.join(metadata_dir, "_ignore_prefix_files")
    testing_config.activate = True
    monkeypatch.setenv("CONDA_TEST_VAR", "conda_test")
    monkeypatch.setenv("CONDA_TEST_VAR_2", "conda_test_2")
    api.build(recipe, config=testing_config)


@pytest.mark.serial
@pytest.mark.skipif(
    "CI" in os.environ and "GITHUB_WORKFLOW" in os.environ,
    reason="This test does not run on Github Actions yet. We will need to adjust "
    "where to look for the pkgs. The github action for setup-miniconda sets "
    "pkg_dirs to conda_pkgs_dir.",
)
# Regardless of the reason for skipping, we should definitely find a better way for tests to look for the packages
# Rather than assuming they will be at $ROOT/pkgs since that can change and we don't care where they are in terms of the
# tests.
# Need more time to figure the problem circumventing..
def test_ignore_some_prefix_files(testing_config, monkeypatch):
    recipe = os.path.join(metadata_dir, "_ignore_some_prefix_files")
    testing_config.activate = True
    monkeypatch.setenv("CONDA_TEST_VAR", "conda_test")
    monkeypatch.setenv("CONDA_TEST_VAR_2", "conda_test_2")
    api.build(recipe, config=testing_config)


@pytest.mark.serial
@pytest.mark.xfail
def test_token_upload(testing_metadata):
    folder_uuid = uuid.uuid4().hex
    # generated with conda_test_account user, command:
    #    anaconda auth --create --name CONDA_BUILD_UPLOAD_TEST --scopes 'api repos conda'
    args = AnacondaClientArgs(
        specs="conda_build_test/test_token_upload_" + folder_uuid,
        token="co-143399b8-276e-48db-b43f-4a3de839a024",
        force=True,
    )

    with pytest.raises(NotFound):
        show.main(args)

    testing_metadata.meta["package"]["name"] = "_".join(
        [testing_metadata.name(), folder_uuid]
    )
    testing_metadata.config.token = args.token

    # the folder with the test recipe to upload
    api.build(testing_metadata, notest=True)

    # make sure that the package is available (should raise if it doesn't)
    show.main(args)

    # clean up - we don't actually want this package to exist
    remove.main(args)

    # verify cleanup:
    with pytest.raises(NotFound):
        show.main(args)


@pytest.mark.sanity
@pytest.mark.serial
@pytest.mark.parametrize("service_name", ["binstar", "anaconda"])
def test_no_anaconda_upload_condarc(
    service_name: str,
    testing_config,
    capfd,
    conda_build_test_recipe_envvar: str,
):
    api.build(str(metadata_path / "empty_sections"), config=testing_config, notest=True)
    output, error = capfd.readouterr()
    assert "Automatic uploading is disabled" in output, error


@pytest.mark.sanity
@pytest.mark.serial
@pytest.mark.parametrize("service_name", ["binstar", "anaconda"])
def test_offline(
    service_name: str, testing_config, conda_build_test_recipe_envvar: str
):
    with env_var("CONDA_OFFLINE", "True", reset_context):
        api.build(str(metadata_path / "empty_sections"), config=testing_config)


def test_git_describe_info_on_branch(testing_config):
    recipe_path = os.path.join(metadata_dir, "_git_describe_number_branch")
    metadata = api.render(recipe_path, config=testing_config)[0][0]
    output = api.get_output_file_paths(metadata)[0]
    # missing hash because we set custom build string in meta.yaml
    test_path = os.path.join(
        testing_config.croot,
        testing_config.host_subdir,
        "git_describe_number_branch-1.20.2.0-1_g82c6ba6.conda",
    )
    assert test_path == output


@pytest.mark.slow
def test_no_include_recipe_config_arg(testing_metadata):
    """Two ways to not include recipe: build/include_recipe: False in meta.yaml; or this.
    Former is tested with specific recipe."""
    outputs = api.build(testing_metadata)
    assert package_has_file(outputs[0], "info/recipe/meta.yaml")

    # make sure that it is not there when the command line flag is passed
    testing_metadata.config.include_recipe = False
    testing_metadata.meta["build"]["number"] = 2
    # We cannot test packages without recipes as we cannot render them
    output_file = api.build(testing_metadata, notest=True)[0]
    assert not package_has_file(output_file, "info/recipe/meta.yaml")


@pytest.mark.slow
def test_no_include_recipe_meta_yaml(testing_metadata, testing_config):
    # first, make sure that the recipe is there by default.  This test copied from above, but copied
    # as a sanity check here.
    outputs = api.build(testing_metadata, notest=True)
    assert package_has_file(outputs[0], "info/recipe/meta.yaml")

    output_file = api.build(
        os.path.join(metadata_dir, "_no_include_recipe"),
        config=testing_config,
        notest=True,
    )[0]
    assert not package_has_file(output_file, "info/recipe/meta.yaml")

    with pytest.raises(CondaBuildUserError):
        # we are testing that even with the recipe excluded, we still get the tests in place
        output_file = api.build(
            os.path.join(metadata_dir, "_no_include_recipe"), config=testing_config
        )[0]


@pytest.mark.serial
@pytest.mark.sanity
def test_early_abort(testing_config, capfd):
    """There have been some problems with conda-build dropping out early.
    Make sure we aren't causing them"""
    api.build(os.path.join(metadata_dir, "_test_early_abort"), config=testing_config)
    output, error = capfd.readouterr()
    assert "Hello World" in output


def test_output_build_path_git_source(testing_config):
    recipe_path = os.path.join(metadata_dir, "source_git_jinja2")
    m = api.render(recipe_path, config=testing_config)[0][0]
    output = api.get_output_file_paths(m)[0]
    _hash = m.hash_dependencies()
    test_path = os.path.join(
        testing_config.croot,
        testing_config.host_subdir,
        "conda-build-test-source-git-jinja2-1.20.2-"
        f"py{sys.version_info.major}{sys.version_info.minor}{_hash}_0_g262d444.conda",
    )
    assert output == test_path


@pytest.mark.sanity
@pytest.mark.serial
@pytest.mark.flaky(reruns=5, reruns_delay=2)
def test_build_with_no_activate_does_not_activate():
    api.build(
        os.path.join(metadata_dir, "_set_env_var_no_activate_build"),
        activate=False,
        anaconda_upload=False,
    )


@pytest.mark.sanity
@pytest.mark.serial
@pytest.mark.xfail(
    on_win and len(os.getenv("PATH")) > 1024,
    reason="Long PATHs make activation fail with obscure messages",
)
def test_build_with_activate_does_activate():
    api.build(
        os.path.join(metadata_dir, "_set_env_var_activate_build"),
        activate=True,
        anaconda_upload=False,
    )


@pytest.mark.sanity
@pytest.mark.skipif(on_win, reason="no binary prefix manipulation done on windows.")
def test_binary_has_prefix_files(testing_config):
    api.build(
        os.path.join(metadata_dir, "_binary_has_prefix_files"), config=testing_config
    )


@pytest.mark.xfail
@pytest.mark.sanity
@pytest.mark.skipif(on_win, reason="no binary prefix manipulation done on windows.")
def test_binary_has_prefix_files_non_utf8(testing_config):
    api.build(
        os.path.join(metadata_dir, "_binary_has_utf_non_8"), config=testing_config
    )


def test_relative_path_git_versioning(
    testing_config,
    conda_build_test_recipe_path: Path,
    conda_build_test_recipe_envvar: str,
):
    tag = describe_root(conda_build_test_recipe_path)
    output = api.get_output_file_paths(
        metadata_path / "_source_git_jinja2_relative_path",
        config=testing_config,
    )[0]
    assert tag in output


def test_relative_git_url_git_versioning(
    testing_config,
    conda_build_test_recipe_path: Path,
    conda_build_test_recipe_envvar: str,
):
    tag = describe_root(conda_build_test_recipe_path)
    output = api.get_output_file_paths(
        metadata_path / "_source_git_jinja2_relative_git_url",
        config=testing_config,
    )[0]
    assert tag in output


def test_dirty_variable_available_in_build_scripts(testing_config):
    recipe = os.path.join(metadata_dir, "_dirty_skip_section")
    testing_config.dirty = True
    api.build(recipe, config=testing_config)

    with pytest.raises(BuildScriptException):
        testing_config.dirty = False
        api.build(recipe, config=testing_config)


def dummy_executable(folder, exename):
    # empty prefix by default - extra bit at beginning of file
    if on_win:
        exename = exename + ".bat"
    dummyfile = os.path.join(folder, exename)
    if on_win:
        prefix = "@echo off\n"
    else:
        prefix = "#!/bin/bash\nexec 1>&2\n"
    with open(dummyfile, "w") as f:
        f.write(
            prefix
            + f"""
    echo ******* You have reached the dummy {exename}. It is likely there is a bug in
    echo ******* conda that makes it not add the _build/bin directory onto the
    echo ******* PATH before running the source checkout tool
    exit -1
    """
        )
    if not on_win:
        import stat

        st = os.stat(dummyfile)
        os.chmod(dummyfile, st.st_mode | stat.S_IEXEC)
    return exename


@pytest.mark.skip(
    reason="GitHub discontinued SVN, see https://github.com/conda/conda-build/issues/5098"
)
def test_checkout_tool_as_dependency(testing_workdir, testing_config, monkeypatch):
    # "hide" svn by putting a known bad one on PATH
    exename = dummy_executable(testing_workdir, "svn")
    monkeypatch.setenv("PATH", testing_workdir, prepend=os.pathsep)
    FNULL = open(os.devnull, "w")
    with pytest.raises(subprocess.CalledProcessError):
        check_call_env([exename, "--version"], stderr=FNULL)
    FNULL.close()
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([testing_workdir, env["PATH"]])
    testing_config.activate = True
    api.build(
        os.path.join(metadata_dir, "_checkout_tool_as_dependency"),
        config=testing_config,
    )


platforms = ["64" if sys.maxsize > 2**32 else "32"]
if on_win:
    platforms = sorted({"32", *platforms})
    compilers = ["3.10", "3.11", "3.12", "3.13"]
    msvc_vers = ["15.0"]
else:
    msvc_vers = []
    compilers = [".".join([str(sys.version_info.major), str(sys.version_info.minor)])]


@pytest.mark.skipif(not on_win, reason="MSVC only on windows")
@pytest.mark.parametrize("msvc_ver", msvc_vers)
def test_build_msvc_compiler(msvc_ver: str, monkeypatch: MonkeyPatch) -> None:
    # verify that the correct compiler is available
    # Remember this is the version of the compiler, not the version of the VS installation
    cl_versions = {
        "9.0": 16,
        "10.0": 16,
        "11.0": 17,
        "12.0": 18,
        "14.0": 19,
        "15.0": 19,
        "16.0": 19,
        "17.0": 19,
    }

    monkeypatch.setenv("CONDATEST_MSVC_VER", msvc_ver)
    monkeypatch.setenv("CL_EXE_VERSION", str(cl_versions[msvc_ver]))

    # Always build Python 2.7 - but set MSVC version manually via Jinja template
    api.build(
        os.path.join(metadata_dir, "_build_msvc_compiler"),
        python="2.7",
    )


@pytest.mark.sanity
@pytest.mark.parametrize("platform", platforms)
@pytest.mark.parametrize("target_compiler", compilers)
@pytest.mark.flaky(reruns=5, reruns_delay=2)
@pytest.mark.serial
def test_cmake_generator(platform, target_compiler, testing_config):
    testing_config.variant["python"] = target_compiler
    testing_config.activate = True
    api.build(os.path.join(metadata_dir, "_cmake_generator"), config=testing_config)


@pytest.mark.skipif(on_win, reason="No windows symlinks")
def test_symlink_fail(testing_config):
    with pytest.raises((SystemExit, FileNotFoundError)):
        api.build(os.path.join(fail_dir, "symlinks"), config=testing_config)


@pytest.mark.sanity
def test_pip_in_meta_yaml_fail(testing_config):
    with pytest.raises(ValueError, match="environment.yml"):
        api.build(
            os.path.join(fail_dir, "pip_reqs_fail_informatively"), config=testing_config
        )


@pytest.mark.sanity
def test_recursive_fail(testing_config):
    with pytest.raises(
        (RuntimeError, exceptions.DependencyNeedsBuildingError),
        match="recursive-build2",
    ):
        api.build(os.path.join(fail_dir, "recursive-build"), config=testing_config)
    # indentation critical here.  If you indent this, and the exception is not raised, then
    #     the exc variable here isn't really completely created and shows really strange errors:
    #     AttributeError: 'ExceptionInfo' object has no attribute 'typename'


@pytest.mark.sanity
def test_jinja_typo(testing_config):
    with pytest.raises(CondaBuildUserError, match="GIT_DSECRIBE_TAG"):
        api.build(
            os.path.join(fail_dir, "source_git_jinja2_oops"), config=testing_config
        )


@pytest.mark.sanity
def test_skip_existing(testing_config, capfd, conda_build_test_recipe_envvar: str):
    # build the recipe first
    api.build(str(metadata_path / "empty_sections"), config=testing_config)
    api.build(
        str(metadata_path / "empty_sections"), config=testing_config, skip_existing=True
    )
    output, error = capfd.readouterr()
    assert "are already built" in output


@pytest.mark.sanity
def test_skip_existing_url(testing_metadata, testing_workdir, capfd):
    # make sure that it is built
    outputs = api.build(testing_metadata)

    # Copy our package into some new folder
    output_dir = os.path.join(testing_workdir, "someoutput")
    platform = os.path.join(output_dir, testing_metadata.config.host_subdir)
    os.makedirs(platform)
    copy_into(outputs[0], os.path.join(platform, os.path.basename(outputs[0])))

    # create the index so conda can find the file
    update_index(output_dir)

    testing_metadata.config.skip_existing = True
    testing_metadata.config.channel_urls = [url_path(output_dir)]

    api.build(testing_metadata)

    output, error = capfd.readouterr()
    assert "are already built" in output


def test_failed_tests_exit_build(testing_config):
    """https://github.com/conda/conda-build/issues/1112"""
    with pytest.raises(CondaBuildUserError, match="TESTS FAILED"):
        api.build(
            os.path.join(metadata_dir, "_test_failed_test_exits"), config=testing_config
        )


@pytest.mark.sanity
def test_requirements_txt_for_run_reqs(testing_config):
    """
    If run reqs are blank, then conda-build looks for requirements.txt in the recipe folder.
    There has been a report of issue with unsatisfiable requirements at

    https://github.com/Anaconda-Platform/anaconda-server/issues/2565

    This test attempts to reproduce those conditions: a channel other than defaults with this
    requirements.txt
    """
    testing_config.channel_urls = ("conda_build_test",)
    api.build(
        os.path.join(metadata_dir, "_requirements_txt_run_reqs"), config=testing_config
    )


@pytest.mark.skipif(
    sys.version_info >= (3, 10),
    reason="Python 3.10+, py_compile terminates once it finds an invalid file",
)
def test_compileall_compiles_all_good_files(testing_config):
    testing_config.conda_pkg_format = 1
    output = api.build(
        os.path.join(metadata_dir, "_compile-test"), config=testing_config
    )[0]
    good_files = ["f1.py", "f3.py"]
    bad_file = "f2_bad.py"
    for f in good_files:
        assert package_has_file(output, f)
        # look for the compiled file also
        assert package_has_file(output, add_mangling(f))
    assert package_has_file(output, bad_file)
    assert not package_has_file(output, add_mangling(bad_file))


@pytest.mark.sanity
@pytest.mark.skipif(
    not on_win, reason="only Windows is insane enough to have backslashes in paths"
)
def test_backslash_in_always_include_files_path():
    api.build(os.path.join(metadata_dir, "_backslash_in_include_files"))
    with pytest.raises(RuntimeError):
        api.build(os.path.join(fail_dir, "backslash_in_include_files"))


@pytest.mark.sanity
def test_build_metadata_object(testing_metadata):
    api.build(testing_metadata)


@pytest.mark.serial
@pytest.mark.skipif(
    sys.version_info >= (3, 12),
    reason="numpy.distutils deprecated in Python 3.12+",
)
def test_numpy_setup_py_data(testing_config):
    recipe_path = os.path.join(metadata_dir, "_numpy_setup_py_data")
    # this shows an error that is OK to ignore:
    # (Is this Error still relevant)

    # PackagesNotFoundError: The following packages are missing from the target environment:
    #    - cython
    subprocess.call("conda remove -y cython".split())
    with pytest.raises(CondaBuildException) as exc_info:
        api.render(recipe_path, config=testing_config, numpy="1.16")
    assert exc_info.match("Cython")
    subprocess.check_call(["conda", "install", "-y", "cython"])
    metadata = api.render(recipe_path, config=testing_config, numpy="1.16")[0][0]
    _hash = metadata.hash_dependencies()
    assert (
        os.path.basename(api.get_output_file_paths(metadata)[0])
        == f"load_setup_py_test-0.1.0-np116py{sys.version_info.major}{sys.version_info.minor}{_hash}_0.conda"
    )


@pytest.mark.slow
def test_relative_git_url_submodule_clone(testing_workdir, testing_config, monkeypatch):
    """
    A multi-part test encompassing the following checks:

    1. That git submodules identified with both relative and absolute URLs can be mirrored
       and cloned.

    2. That changes pushed to the original repository are updated in the mirror and finally
       reflected in the package version and filename via `GIT_DESCRIBE_TAG`.

    3. That `source.py` is using `check_call_env` and `check_output_env` and that those
       functions are using tools from the build env.
    """

    toplevel = os.path.join(testing_workdir, "toplevel")
    os.mkdir(toplevel)
    relative_sub = os.path.join(testing_workdir, "relative_sub")
    os.mkdir(relative_sub)
    absolute_sub = os.path.join(testing_workdir, "absolute_sub")
    os.mkdir(absolute_sub)

    sys_git_env = os.environ.copy()
    sys_git_env["GIT_AUTHOR_NAME"] = "conda-build"
    sys_git_env["GIT_AUTHOR_EMAIL"] = "conda@conda-build.org"
    sys_git_env["GIT_COMMITTER_NAME"] = "conda-build"
    sys_git_env["GIT_COMMITTER_EMAIL"] = "conda@conda-build.org"

    # Find the git executable before putting our dummy one on PATH.
    git = find_executable("git")

    # Put the broken git on os.environ["PATH"]
    exename = dummy_executable(testing_workdir, "git")
    monkeypatch.setenv("PATH", testing_workdir, prepend=os.pathsep)
    # .. and ensure it gets run (and fails).
    FNULL = open(os.devnull, "w")
    # Strangely ..
    #   stderr=FNULL suppresses the output from echo on OS X whereas
    #   stdout=FNULL suppresses the output from echo on Windows
    with pytest.raises(subprocess.CalledProcessError):
        check_call_env([exename, "--version"], stdout=FNULL, stderr=FNULL)
    FNULL.close()

    for tag in range(2):
        os.chdir(absolute_sub)
        if tag == 0:
            check_call_env([git, "init"], env=sys_git_env)
        with open("absolute", "w") as f:
            f.write(str(tag))
        check_call_env([git, "add", "absolute"], env=sys_git_env)
        check_call_env([git, "commit", "-m", f"absolute{tag}"], env=sys_git_env)

        os.chdir(relative_sub)
        if tag == 0:
            check_call_env([git, "init"], env=sys_git_env)
        with open("relative", "w") as f:
            f.write(str(tag))
        check_call_env([git, "add", "relative"], env=sys_git_env)
        check_call_env([git, "commit", "-m", f"relative{tag}"], env=sys_git_env)

        os.chdir(toplevel)
        if tag == 0:
            check_call_env([git, "init"], env=sys_git_env)
        with open("toplevel", "w") as f:
            f.write(str(tag))
        check_call_env([git, "add", "toplevel"], env=sys_git_env)
        check_call_env([git, "commit", "-m", f"toplevel{tag}"], env=sys_git_env)
        if tag == 0:
            check_call_env(
                [
                    git,
                    # CVE-2022-39253
                    *("-c", "protocol.file.allow=always"),
                    "submodule",
                    "add",
                    convert_path_for_cygwin_or_msys2(git, absolute_sub),
                    "absolute",
                ],
                env=sys_git_env,
            )
            check_call_env(
                [
                    git,
                    # CVE-2022-39253
                    *("-c", "protocol.file.allow=always"),
                    "submodule",
                    "add",
                    "../relative_sub",
                    "relative",
                ],
                env=sys_git_env,
            )
        else:
            # Once we use a more recent Git for Windows than 2.6.4 on Windows or m2-git we
            # can change this to `git submodule update --recursive`.
            gits = git.replace("\\", "/")
            check_call_env(
                [
                    git,
                    # CVE-2022-39253
                    *("-c", "protocol.file.allow=always"),
                    "submodule",
                    "foreach",
                    gits,
                    "pull",
                ],
                env=sys_git_env,
            )
        check_call_env(
            [git, "commit", "-am", f"added submodules@{tag}"], env=sys_git_env
        )
        check_call_env(
            [git, "tag", "-a", str(tag), "-m", f"tag {tag}"], env=sys_git_env
        )

        # It is possible to use `Git for Windows` here too, though you *must* not use a different
        # (type of) git than the one used above to add the absolute submodule, because .gitmodules
        # stores the absolute path and that is not interchangeable between MSYS2 and native Win32.
        #
        # Also, git is set to False here because it needs to be rebuilt with the longer prefix. As
        # things stand, my _b_env folder for this test contains more than 80 characters.

        recipe_dir = os.path.join(testing_workdir, "recipe")
        if not os.path.exists(recipe_dir):
            os.makedirs(recipe_dir)
        filename = os.path.join(testing_workdir, "recipe", "meta.yaml")
        data = {
            "package": {
                "name": "relative_submodules",
                "version": "{{ GIT_DESCRIBE_TAG }}",
            },
            "source": {"git_url": toplevel, "git_tag": str(tag)},
            "requirements": {
                "build": [
                    "git            # [False]",
                    "m2-git         # [win]",
                    "m2-filesystem  # [win]",
                ],
            },
            "build": {
                "script": [
                    "git --no-pager submodule --quiet foreach git log -n 1 --pretty=format:%%s > "
                    "%PREFIX%\\summaries.txt  # [win]",
                    "git --no-pager submodule --quiet foreach git log -n 1 --pretty=format:%s > "
                    "$PREFIX/summaries.txt   # [not win]",
                ],
            },
            "test": {
                "commands": [
                    f"echo absolute{tag}relative{tag} > %PREFIX%\\expected_summaries.txt # [win]",
                    "fc.exe /W %PREFIX%\\expected_summaries.txt %PREFIX%\\summaries.txt # [win]",
                    f"echo absolute{tag}relative{tag} > $PREFIX/expected_summaries.txt # [not win]",
                    "diff -wuN ${PREFIX}/expected_summaries.txt ${PREFIX}/summaries.txt # [not win]",
                ],
            },
        }

        with open(filename, "w") as outfile:
            outfile.write(yaml.dump(data, default_flow_style=False, width=999999999))
        # Reset the path because our broken, dummy `git` would cause `render_recipe`
        # to fail, while no `git` will cause the build_dependencies to be installed.
        monkeypatch.undo()
        # This will (after one spin round the loop) install and run 'git' with the
        # build env prepended to os.environ[]
        metadata = api.render(testing_workdir, config=testing_config)[0][0]
        output = api.get_output_file_paths(metadata, config=testing_config)[0]
        assert f"relative_submodules-{tag}-" in output
        api.build(metadata, config=testing_config)


def test_noarch(testing_workdir):
    filename = os.path.join(testing_workdir, "meta.yaml")
    for noarch in (False, True):
        data = OrderedDict(
            [
                ("package", OrderedDict([("name", "test"), ("version", "0.0.0")])),
                ("build", OrderedDict([("noarch", noarch)])),
            ]
        )
        with open(filename, "w") as outfile:
            outfile.write(yaml.dump(data, default_flow_style=False, width=999999999))
        output = api.get_output_file_paths(testing_workdir)[0]
        assert os.path.sep + "noarch" + os.path.sep in output or not noarch
        assert os.path.sep + "noarch" + os.path.sep not in output or noarch


def test_disable_pip(testing_metadata):
    testing_metadata.config.disable_pip = True
    testing_metadata.meta["requirements"] = {"host": ["python"], "run": ["python"]}
    testing_metadata.meta["build"]["script"] = (
        'python -c "import pip; print(pip.__version__)"'
    )
    with pytest.raises(BuildScriptException):
        api.build(testing_metadata)

    testing_metadata.meta["build"]["script"] = (
        'python -c "import setuptools; print(setuptools.__version__)"'
    )
    with pytest.raises(BuildScriptException):
        api.build(testing_metadata)


@pytest.mark.sanity
@pytest.mark.skipif(on_win, reason="rpath fixup not done on Windows.")
def test_rpath_unix(testing_config, variants_conda_build_sysroot):
    testing_config.activate = True
    api.build(
        os.path.join(metadata_dir, "_rpath"),
        config=testing_config,
        variants=variants_conda_build_sysroot,
    )


def test_noarch_none_value(testing_config):
    recipe = os.path.join(metadata_dir, "_noarch_none")
    with pytest.raises(exceptions.CondaBuildException):
        api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_noarch_foo_value(testing_config):
    outputs = api.build(
        os.path.join(metadata_dir, "noarch_generic"), config=testing_config
    )
    metadata = json.loads(package_has_file(outputs[0], "info/index.json"))
    assert metadata["noarch"] == "generic"


def test_about_json_content(testing_metadata):
    outputs = api.build(testing_metadata)
    about = json.loads(package_has_file(outputs[0], "info/about.json"))
    assert "conda_version" in about and about["conda_version"] == conda.__version__
    assert (
        "conda_build_version" in about and about["conda_build_version"] == __version__
    )
    assert "channels" in about and about["channels"]
    assert "tags" in about and about["tags"] == ["a", "b"]
    # this one comes in as a string - test type coercion
    assert "identifiers" in about and about["identifiers"] == ["a"]
    assert "env_vars" in about and about["env_vars"]

    assert "root_pkgs" in about and about["root_pkgs"]


@pytest.mark.parametrize(
    "name,field", [("license", "license_file"), ("prelink_message", "prelink_message")]
)
def test_about_license_file_and_prelink_message(testing_config, name, field):
    testing_config.conda_pkg_format = 1
    base_dir = os.path.join(metadata_dir, f"_about_{field}/recipes")

    recipe = os.path.join(base_dir, "single")
    outputs = api.build(recipe, config=testing_config)
    assert package_has_file(outputs[0], f"info/{name}s/{name}-from-source.txt")

    recipe = os.path.join(base_dir, "list")
    outputs = api.build(recipe, config=testing_config)
    assert package_has_file(outputs[0], f"info/{name}s/{name}-from-source.txt")
    assert package_has_file(outputs[0], f"info/{name}s/{name}-from-recipe.txt")

    recipe = os.path.join(base_dir, "dir")
    outputs = api.build(recipe, config=testing_config)
    assert package_has_file(
        outputs[0], f"info/{name}s/{name}-dir-from-source/first-{name}.txt"
    )
    assert package_has_file(
        outputs[0], f"info/{name}s/{name}-dir-from-source/second-{name}.txt"
    )
    assert package_has_file(
        outputs[0], f"info/{name}s/{name}-dir-from-recipe/first-{name}.txt"
    )
    assert package_has_file(
        outputs[0], f"info/{name}s/{name}-dir-from-recipe/second-{name}.txt"
    )

    recipe = os.path.join(base_dir, "dir-no-slash-suffix")
    assert os.path.isdir(recipe)
    str_match = f"{field}.*{name}-dir-from-recipe.*directory"
    with pytest.raises(ValueError, match=str_match):
        api.build(recipe, config=testing_config)


@pytest.mark.slow
@pytest.mark.skipif(
    "CI" in os.environ and "GITHUB_WORKFLOW" in os.environ,
    reason="This test does not run on Github Actions yet. We will need to adjust "
    "where to look for the pkgs. The github action for setup-miniconda sets "
    "pkg_dirs to conda_pkgs_dir.",
)
# Regardless of the reason for skipping, we should definitely find a better way for tests to look for the packages
# Rather than assuming they will be at $ROOT/pkgs since that can change and we don't care where they are in terms of the
# tests.
def test_noarch_python_with_tests(testing_config):
    recipe = os.path.join(metadata_dir, "_noarch_python_with_tests")
    pkg = api.build(recipe, config=testing_config)[0]
    # noarch recipes with commands should generate both .bat and .sh files.
    assert package_has_file(pkg, "info/test/run_test.bat")
    assert package_has_file(pkg, "info/test/run_test.sh")


@pytest.mark.sanity
def test_noarch_python_1(testing_config):
    output = api.build(
        os.path.join(metadata_dir, "_noarch_python"), config=testing_config
    )[0]
    assert package_has_file(output, "info/files") != ""
    extra = json.loads(package_has_file(output, "info/link.json"))
    assert "noarch" in extra
    assert "entry_points" in extra["noarch"]
    assert "type" in extra["noarch"]
    assert "package_metadata_version" in extra


@pytest.mark.sanity
def test_skip_compile_pyc(testing_config):
    testing_config.conda_pkg_format = 1
    outputs = api.build(
        os.path.join(metadata_dir, "skip_compile_pyc"), config=testing_config
    )
    tf = tarfile.open(outputs[0])
    pyc_count = 0
    for f in tf.getmembers():
        filename = os.path.basename(f.name)
        _, ext = os.path.splitext(filename)
        basename = filename.split(".", 1)[0]
        if basename == "skip_compile_pyc":
            assert not ext == ".pyc", (
                f"a skip_compile_pyc .pyc was compiled: {filename}"
            )
        if ext == ".pyc":
            assert basename == "compile_pyc", (
                f"an unexpected .pyc was compiled: {filename}"
            )
            pyc_count = pyc_count + 1
    assert pyc_count == 2, (
        f"there should be 2 .pyc files, instead there were {pyc_count}"
    )


def test_detect_binary_files_with_prefix(testing_config):
    testing_config.conda_pkg_format = 1
    outputs = api.build(
        os.path.join(metadata_dir, "_detect_binary_files_with_prefix"),
        config=testing_config,
    )
    matches = []
    with tarfile.open(outputs[0]) as tf:
        has_prefix = tf.extractfile("info/has_prefix")
        contents = [p.strip().decode("utf-8") for p in has_prefix.readlines()]
        has_prefix.close()
        matches = [
            entry
            for entry in contents
            if entry.endswith("binary-has-prefix")
            or entry.endswith('"binary-has-prefix"')
        ]
    assert len(matches) == 1, "binary-has-prefix not recorded in info/has_prefix"
    assert " binary " in matches[0], (
        "binary-has-prefix not recorded as binary in info/has_prefix"
    )


def test_skip_detect_binary_files_with_prefix(testing_config):
    testing_config.conda_pkg_format = 1
    recipe = os.path.join(metadata_dir, "_skip_detect_binary_files_with_prefix")
    outputs = api.build(recipe, config=testing_config)
    matches = []
    with tarfile.open(outputs[0]) as tf:
        try:
            has_prefix = tf.extractfile("info/has_prefix")
            contents = [p.strip().decode("utf-8") for p in has_prefix.readlines()]
            has_prefix.close()
            matches = [
                entry
                for entry in contents
                if entry.endswith("binary-has-prefix")
                or entry.endswith('"binary-has-prefix"')
            ]
        except:
            pass
    assert len(matches) == 0, (
        "binary-has-prefix recorded in info/has_prefix despite:"
        "build/detect_binary_files_with_prefix: false"
    )


def test_fix_permissions(testing_config):
    testing_config.conda_pkg_format = 1
    recipe = os.path.join(metadata_dir, "fix_permissions")
    outputs = api.build(recipe, config=testing_config)
    with tarfile.open(outputs[0]) as tf:
        for f in tf.getmembers():
            assert f.mode & 0o444 == 0o444, (
                f"tar member '{f.name}' has invalid (read) mode"
            )


@pytest.mark.sanity
@pytest.mark.skipif(not on_win, reason="windows-only functionality")
@pytest.mark.parametrize(
    "recipe_name", ["_script_win_creates_exe", "_script_win_creates_exe_garbled"]
)
def test_script_win_creates_exe(testing_config, recipe_name):
    recipe = os.path.join(metadata_dir, recipe_name)
    outputs = api.build(recipe, config=testing_config)
    assert package_has_file(outputs[0], "Scripts/test-script.exe")
    assert package_has_file(outputs[0], "Scripts/test-script-script.py")


@pytest.mark.sanity
def test_output_folder_moves_file(testing_metadata, testing_workdir):
    testing_metadata.config.output_folder = testing_workdir
    outputs = api.build(testing_metadata, no_test=True)
    assert outputs[0].startswith(testing_workdir)


@pytest.mark.sanity
@pytest.mark.skipif(
    "CI" in os.environ and "GITHUB_WORKFLOW" in os.environ,
    reason="This test does not run on Github Actions yet. We will need to adjust "
    "where to look for the pkgs. The github action for setup-miniconda sets "
    "pkg_dirs to conda_pkgs_dir.",
)
def test_info_files_json(testing_config):
    testing_config.conda_pkg_format = 1
    outputs = api.build(
        os.path.join(metadata_dir, "_ignore_some_prefix_files"), config=testing_config
    )
    assert package_has_file(outputs[0], "info/paths.json")
    with tarfile.open(outputs[0]) as tf:
        data = json.loads(tf.extractfile("info/paths.json").read().decode("utf-8"))
    fields = [
        "_path",
        "sha256",
        "size_in_bytes",
        "path_type",
        "file_mode",
        "no_link",
        "prefix_placeholder",
        "inode_paths",
    ]
    for key in data.keys():
        assert key in ["paths", "paths_version"]
    for paths in data.get("paths"):
        for field in paths.keys():
            assert field in fields
    assert len(data.get("paths")) == 2
    for file in data.get("paths"):
        for key in file.keys():
            assert key in fields
        short_path = file.get("_path")
        if short_path == "test.sh" or short_path == "test.bat":
            assert file.get("prefix_placeholder") is not None
            assert file.get("file_mode") is not None
        else:
            assert file.get("prefix_placeholder") is None
            assert file.get("file_mode") is None


def test_build_expands_wildcards(mocker):
    build_tree = mocker.patch("conda_build.build.build_tree")
    config = api.Config()
    files = ["abc", "acb"]
    for f in files:
        os.makedirs(f)
        with open(os.path.join(f, "meta.yaml"), "w") as fh:
            fh.write("\n")
    api.build(["a*"], config=config)
    output = sorted(os.path.join(os.getcwd(), path, "meta.yaml") for path in files)

    build_tree.assert_called_once_with(
        output,
        config=mocker.ANY,
        stats=mocker.ANY,
        build_only=False,
        post=None,
        notest=False,
        variants=None,
    )


@pytest.mark.parametrize("set_build_id", [True, False])
def test_remove_workdir_default(testing_config, caplog, set_build_id):
    recipe = os.path.join(metadata_dir, "_keep_work_dir")
    # make a metadata object - otherwise the build folder is computed within the build, but does
    #    not alter the config object that is passed in.  This is by design - we always make copies
    #    of the config object rather than edit it in place, so that variants don't clobber one
    #    another
    metadata = api.render(recipe, config=testing_config)[0][0]
    api.build(metadata, set_build_id=set_build_id)
    assert not glob(os.path.join(metadata.config.work_dir, "*"))


def test_keep_workdir_and_dirty_reuse(testing_config, capfd):
    recipe = os.path.join(metadata_dir, "_keep_work_dir")
    # make a metadata object - otherwise the build folder is computed within the build, but does
    #    not alter the config object that is passed in.  This is by design - we always make copies
    #    of the config object rather than edit it in place, so that variants don't clobber one
    #    another

    metadata = api.render(
        recipe, config=testing_config, dirty=True, remove_work_dir=False
    )[0][0]
    workdir = metadata.config.work_dir
    api.build(metadata)
    out, err = capfd.readouterr()
    assert glob(os.path.join(metadata.config.work_dir, "*"))

    # test that --dirty reuses the same old folder
    metadata = api.render(
        recipe, config=testing_config, dirty=True, remove_work_dir=False
    )[0][0]
    assert workdir == metadata.config.work_dir

    # test that without --dirty, we don't reuse the folder
    metadata = api.render(recipe, config=testing_config)[0][0]
    assert workdir != metadata.config.work_dir

    testing_config.clean()


@pytest.mark.sanity
def test_workdir_removal_warning(testing_config, caplog):
    recipe = os.path.join(metadata_dir, "_test_uses_src_dir")
    with pytest.raises(ValueError) as exc:
        api.build(recipe, config=testing_config)
        assert "work dir is removed" in str(exc)


@pytest.mark.sanity
@pytest.mark.skipif(not on_mac, reason="relevant to mac only")
def test_append_python_app_osx(testing_config, conda_build_test_recipe_envvar: str):
    """Recipes that use osx_is_app need to have python.app in their runtime requirements.

    conda-build will add it if it's missing."""
    recipe = os.path.join(metadata_dir, "_osx_is_app_missing_python_app")
    # tests will fail here if python.app is not added to the run reqs by conda-build, because
    #    without it, pythonw will be missing.
    api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_run_exports(testing_metadata, testing_config, testing_workdir):
    api.build(
        os.path.join(metadata_dir, "_run_exports"), config=testing_config, notest=True
    )
    api.build(
        os.path.join(metadata_dir, "_run_exports_implicit_weak"),
        config=testing_config,
        notest=True,
    )

    # run_exports is tricky.  We mostly only ever want things in "host".  Here are the conditions:

    #    1. only build section present (legacy recipe).  Here, use run_exports from build.  Because build and host
    #       will be merged when build subdir == host_subdir, the weak run_exports should be present.
    testing_metadata.meta["requirements"]["build"] = ["test_has_run_exports"]
    api.output_yaml(testing_metadata, "meta.yaml")
    metadata = api.render(testing_workdir, config=testing_config)[0][0]
    assert "strong_pinned_package 1.0.*" in metadata.meta["requirements"]["run"]
    assert "weak_pinned_package 1.0.*" in metadata.meta["requirements"]["run"]

    #    2. host present.  Use run_exports from host, ignore 'weak' ones from build.  All are
    #           weak by default.
    testing_metadata.meta["requirements"]["build"] = [
        "test_has_run_exports_implicit_weak",
        '{{ compiler("c") }}',
    ]
    testing_metadata.meta["requirements"]["host"] = ["python"]
    api.output_yaml(testing_metadata, "host_present_weak/meta.yaml")
    metadata = api.render(
        os.path.join(testing_workdir, "host_present_weak"), config=testing_config
    )[0][0]
    assert "weak_pinned_package 2.0.*" not in metadata.meta["requirements"].get(
        "run", []
    )

    #    3. host present, and deps in build have "strong" run_exports section.  use host, add
    #           in "strong" from build.
    testing_metadata.meta["requirements"]["build"] = [
        "test_has_run_exports",
        '{{ compiler("c") }}',
    ]
    testing_metadata.meta["requirements"]["host"] = [
        "test_has_run_exports_implicit_weak"
    ]
    api.output_yaml(testing_metadata, "host_present_strong/meta.yaml")
    metadata = api.render(
        os.path.join(testing_workdir, "host_present_strong"), config=testing_config
    )[0][0]
    assert "strong_pinned_package 1.0 0" in metadata.meta["requirements"]["host"]
    assert "strong_pinned_package 1.0.*" in metadata.meta["requirements"]["run"]
    # weak one from test_has_run_exports should be excluded, since it is a build dep
    assert "weak_pinned_package 1.0.*" not in metadata.meta["requirements"]["run"]
    # weak one from test_has_run_exports_implicit_weak should be present, since it is a host dep
    assert "weak_pinned_package 2.0.*" in metadata.meta["requirements"]["run"]


@pytest.mark.sanity
def test_ignore_run_exports(testing_metadata, testing_config):
    # build the package with run exports for ensuring that we ignore it
    api.build(
        os.path.join(metadata_dir, "_run_exports"), config=testing_config, notest=True
    )
    # customize our fixture metadata with our desired changes
    testing_metadata.meta["requirements"]["host"] = ["test_has_run_exports"]
    testing_metadata.meta["build"]["ignore_run_exports"] = ["downstream_pinned_package"]
    testing_metadata.config.index = None
    m = finalize_metadata(testing_metadata)
    assert "downstream_pinned_package 1.0" not in m.meta["requirements"].get("run", [])


@pytest.mark.sanity
def test_ignore_run_exports_from(testing_metadata, testing_config):
    # build the package with run exports for ensuring that we ignore it
    api.build(
        os.path.join(metadata_dir, "_run_exports"), config=testing_config, notest=True
    )
    # customize our fixture metadata with our desired changes
    testing_metadata.meta["requirements"]["host"] = ["test_has_run_exports"]
    testing_metadata.meta["build"]["ignore_run_exports_from"] = ["test_has_run_exports"]
    testing_metadata.config.index = None
    m = finalize_metadata(testing_metadata)
    assert "downstream_pinned_package 1.0" not in m.meta["requirements"].get("run", [])


@pytest.mark.skipif(
    "CI" in os.environ and "GITHUB_WORKFLOW" in os.environ,
    reason="This test does not run on Github Actions yet. We will need to adjust "
    "where to look for the pkgs. The github action for setup-miniconda sets "
    "pkg_dirs to conda_pkgs_dir.",
)
def test_run_exports_noarch_python(testing_metadata, testing_config):
    # build the package with run exports for ensuring that we ignore it
    api.build(
        os.path.join(metadata_dir, "_run_exports_noarch"),
        config=testing_config,
        notest=True,
    )
    # customize our fixture metadata with our desired changes
    testing_metadata.meta["requirements"]["host"] = ["python"]
    testing_metadata.meta["requirements"]["run"] = ["python"]
    testing_metadata.meta["build"]["noarch"] = "python"
    testing_metadata.config.index = None
    testing_metadata.config.variant["python"] = "3.8 with_run_exports"

    m = finalize_metadata(testing_metadata)
    assert "python 3.6 with_run_exports" in m.meta["requirements"].get("host", [])
    assert "python 3.6 with_run_exports" not in m.meta["requirements"].get("run", [])


def test_run_exports_constrains(testing_metadata, testing_config, testing_workdir):
    api.build(
        os.path.join(metadata_dir, "_run_exports_constrains"),
        config=testing_config,
        notest=True,
    )

    testing_metadata.meta["requirements"]["build"] = ["run_exports_constrains"]
    testing_metadata.meta["requirements"]["host"] = []
    api.output_yaml(testing_metadata, "in_build/meta.yaml")
    metadata = api.render(
        os.path.join(testing_workdir, "in_build"), config=testing_config
    )[0][0]
    reqs_set = lambda section: set(metadata.meta["requirements"].get(section, []))
    assert {"strong_run_export"} == reqs_set("run")
    assert {"strong_constrains_export"} == reqs_set("run_constrained")

    testing_metadata.meta["requirements"]["build"] = []
    testing_metadata.meta["requirements"]["host"] = ["run_exports_constrains"]
    api.output_yaml(testing_metadata, "in_host/meta.yaml")
    metadata = api.render(
        os.path.join(testing_workdir, "in_host"), config=testing_config
    )[0][0]
    reqs_set = lambda section: set(metadata.meta["requirements"].get(section, []))
    assert {"strong_run_export", "weak_run_export"} == reqs_set("run")
    assert {"strong_constrains_export", "weak_constrains_export"} == reqs_set(
        "run_constrained"
    )

    testing_metadata.meta["requirements"]["build"] = [
        "run_exports_constrains_only_weak"
    ]
    testing_metadata.meta["requirements"]["host"] = []
    api.output_yaml(testing_metadata, "only_weak_in_build/meta.yaml")
    metadata = api.render(
        os.path.join(testing_workdir, "only_weak_in_build"), config=testing_config
    )[0][0]
    reqs_set = lambda section: set(metadata.meta["requirements"].get(section, []))
    assert set() == reqs_set("run")
    assert set() == reqs_set("run_constrained")

    testing_metadata.meta["requirements"]["build"] = []
    testing_metadata.meta["requirements"]["host"] = ["run_exports_constrains_only_weak"]
    api.output_yaml(testing_metadata, "only_weak_in_host/meta.yaml")
    metadata = api.render(
        os.path.join(testing_workdir, "only_weak_in_host"), config=testing_config
    )[0][0]
    reqs_set = lambda section: set(metadata.meta["requirements"].get(section, []))
    assert {"weak_run_export"} == reqs_set("run")
    assert {"weak_constrains_export"} == reqs_set("run_constrained")


def test_pin_subpackage_exact(testing_config):
    recipe = os.path.join(metadata_dir, "_pin_subpackage_exact")
    metadata_tuples = api.render(recipe, config=testing_config)
    assert len(metadata_tuples) == 2
    assert any(
        re.match(r"run_exports_subpkg\ 1\.0\ 0", req)
        for metadata, _, _ in metadata_tuples
        for req in metadata.meta.get("requirements", {}).get("run", [])
    )


@pytest.mark.sanity
@pytest.mark.serial
@pytest.mark.skipif(on_mac and not which("xattr"), reason="`xattr` unavailable")
@pytest.mark.skipif(on_linux and not which("setfattr"), reason="`setfattr` unavailable")
@pytest.mark.skipif(on_win, reason="Windows doesn't support xattr")
def test_copy_read_only_file_with_xattr(testing_config: Config, testing_homedir: Path):
    recipe = Path(testing_homedir, "_xattr_copy")
    copy_into(metadata_path / "_xattr_copy", recipe)

    # file is u=rw,go=r (0o644) to start, change it to u=r,go= (0o400) after setting the attribute
    ro_file = recipe / "mode_400_file"

    # set extended attributes
    if on_linux:
        # tmpfs on modern Linux does not support xattr in general.
        # https://stackoverflow.com/a/46598063
        # tmpfs can support extended attributes if you enable CONFIG_TMPFS_XATTR in Kernel config.
        # But Currently this enables support for the trusted.* and security.* namespaces
        try:
            subprocess.run(
                f"setfattr -n user.attrib -v somevalue {ro_file}",
                shell=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            pytest.xfail("`setfattr` failed, see https://stackoverflow.com/a/46598063")
    else:
        subprocess.run(
            f"xattr -w user.attrib somevalue {ro_file}",
            shell=True,
            check=True,
        )

    # restrict file permissions
    ro_file.chmod(0o400)

    api.build(str(recipe), config=testing_config)


@pytest.mark.sanity
@pytest.mark.serial
def test_env_creation_fail_exits_build(testing_config):
    recipe = os.path.join(metadata_dir, "_post_link_exits_after_retry")
    with pytest.raises((RuntimeError, LinkError, CondaError, KeyError)):
        api.build(recipe, config=testing_config)

    recipe = os.path.join(metadata_dir, "_post_link_exits_tests")
    with pytest.raises((RuntimeError, LinkError, CondaError, KeyError)):
        api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_recursion_packages(testing_config):
    """Two packages that need to be built are listed in the recipe

    make sure that both get built before the one needing them gets built."""
    recipe = os.path.join(metadata_dir, "_recursive-build-two-packages")
    api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_recursion_layers(testing_config):
    """go two 'hops' - try to build a, but a needs b, so build b first, then come back to a"""
    recipe = os.path.join(metadata_dir, "_recursive-build-two-layers")
    api.build(recipe, config=testing_config)


@pytest.mark.sanity
@pytest.mark.skipif(
    not on_win,
    reason="spaces break openssl prefix replacement on *nix",
)
def test_croot_with_spaces(testing_metadata, testing_workdir):
    testing_metadata.config.croot = os.path.join(testing_workdir, "space path")
    api.build(testing_metadata)


@pytest.mark.sanity
def test_unknown_selectors(testing_config):
    recipe = os.path.join(metadata_dir, "unknown_selector")
    api.build(recipe, config=testing_config)


# the locks can be very flaky on GitHub Windows Runners
# https://github.com/conda/conda-build/issues/4685
@pytest.mark.flaky(reruns=5, reruns_delay=2)
def test_failed_recipe_leaves_folders(testing_config):
    recipe = os.path.join(fail_dir, "recursive-build")
    metadata = api.render(recipe, config=testing_config)[0][0]
    locks = get_conda_operation_locks(
        metadata.config.locking,
        metadata.config.bldpkgs_dirs,
        metadata.config.timeout,
    )
    with pytest.raises((RuntimeError, exceptions.DependencyNeedsBuildingError)):
        api.build(metadata)
    assert os.path.isdir(metadata.config.build_folder), "build folder was removed"
    assert os.listdir(metadata.config.build_folder), "build folder has no files"

    # make sure that it does not leave lock files, though, as these cause permission errors on
    #    centralized installations
    assert [lock.lock_file for lock in locks if os.path.isfile(lock.lock_file)] == []


@pytest.mark.sanity
def test_only_r_env_vars_defined(testing_config):
    recipe = os.path.join(metadata_dir, "_r_env_defined")
    api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_only_perl_env_vars_defined(testing_config):
    recipe = os.path.join(metadata_dir, "_perl_env_defined")
    api.build(recipe, config=testing_config)


@pytest.mark.sanity
@pytest.mark.skipif(on_win, reason="no lua package on win")
def test_only_lua_env(testing_config):
    recipe = os.path.join(metadata_dir, "_lua_env_defined")
    testing_config.set_build_id = False
    api.build(recipe, config=testing_config)


def test_run_constrained_stores_constrains_info(testing_config):
    recipe = os.path.join(metadata_dir, "_run_constrained")
    out_file = api.build(recipe, config=testing_config)[0]
    info_contents = json.loads(package_has_file(out_file, "info/index.json"))
    assert "constrains" in info_contents
    assert len(info_contents["constrains"]) == 1
    assert info_contents["constrains"][0] == "bzip2  1.*"


def test_run_constrained_is_validated(testing_config: Config):
    recipe = os.path.join(metadata_dir, "_run_constrained_error")
    with pytest.raises(RecipeError):
        api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_no_locking(testing_config):
    recipe = os.path.join(metadata_dir, "source_git_jinja2")
    update_index(os.path.join(testing_config.croot))
    api.build(recipe, config=testing_config, locking=False)


@pytest.mark.sanity
def test_test_dependencies(testing_config):
    recipe = os.path.join(fail_dir, "check_test_dependencies")

    with pytest.raises(exceptions.DependencyNeedsBuildingError) as e:
        api.build(recipe, config=testing_config)

    assert "Unsatisfiable dependencies for platform " in str(e.value)
    assert "pytest-package-does-not-exist" in str(e.value)


@pytest.mark.sanity
def test_runtime_dependencies(testing_config):
    recipe = os.path.join(fail_dir, "check_runtime_dependencies")

    with pytest.raises(exceptions.DependencyNeedsBuildingError) as e:
        api.build(recipe, config=testing_config)

    assert "Unsatisfiable dependencies for platform " in str(e.value)
    assert "some-nonexistent-package1" in str(e.value)


@pytest.mark.sanity
def test_no_force_upload(
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
    testing_workdir: str | os.PathLike | Path,
    testing_metadata: MetaData,
    request: FixtureRequest,
):
    # this is nearly identical to tests/cli/test_main_build.py::test_no_force_upload
    # only difference is this tests `conda_build.api.build`
    request.addfinalizer(reset_config)
    call = mocker.patch("subprocess.call")
    anaconda = find_executable("anaconda")

    # render recipe
    api.output_yaml(testing_metadata, "meta.yaml")

    # mock Config.set_keys to always set anaconda_upload to True
    # conda's Context + conda_build's MetaData & Config objects interact in such an
    # awful way that mocking these configurations is ugly and confusing, all of it
    # needs major refactoring
    set_keys = Config.set_keys  # store original method
    override = {"anaconda_upload": True}
    monkeypatch.setattr(
        Config,
        "set_keys",
        lambda self, **kwargs: set_keys(self, **{**kwargs, **override}),
    )

    # check for normal upload
    override["force_upload"] = False
    pkg = api.build(testing_workdir)
    call.assert_called_once_with([anaconda, "upload", *pkg])
    call.reset_mock()

    # check for force upload
    override["force_upload"] = True
    pkg = api.build(testing_workdir)
    call.assert_called_once_with([anaconda, "upload", "--force", *pkg])


@pytest.mark.sanity
def test_setup_py_data_in_env(testing_config):
    recipe = os.path.join(metadata_dir, "_setup_py_data_in_env")
    # should pass with any modern python (just not 3.5)
    api.build(recipe, config=testing_config)
    # make sure it fails with our special python logic
    with pytest.raises((BuildScriptException, CondaBuildException)):
        api.build(recipe, config=testing_config, python="3.5")


@pytest.mark.sanity
def test_numpy_xx(testing_config):
    recipe = os.path.join(metadata_dir, "_numpy_xx")
    api.render(recipe, config=testing_config, numpy="1.15", python="3.6")


@pytest.mark.sanity
def test_numpy_xx_host(testing_config):
    recipe = os.path.join(metadata_dir, "_numpy_xx_host")
    api.render(recipe, config=testing_config, numpy="1.15", python="3.6")


@pytest.mark.sanity
def test_python_xx(testing_config):
    recipe = os.path.join(metadata_dir, "_python_xx")
    api.render(recipe, config=testing_config, python="3.5")


@pytest.mark.sanity
def test_indirect_numpy_dependency(testing_metadata, testing_workdir):
    testing_metadata.meta["requirements"]["build"] = ["pandas"]
    api.output_yaml(testing_metadata, os.path.join(testing_workdir, "meta.yaml"))
    api.render(testing_workdir, numpy="1.13", notest=True)


@pytest.mark.sanity
def test_dependencies_with_notest(testing_config):
    recipe = os.path.join(metadata_dir, "_test_dependencies")
    api.build(recipe, config=testing_config, notest=True)

    with pytest.raises(DependencyNeedsBuildingError) as excinfo:
        api.build(recipe, config=testing_config, notest=False)

    assert "Unsatisfiable dependencies for platform" in str(excinfo.value)
    assert "somenonexistentpackage1" in str(excinfo.value)


@pytest.mark.sanity
def test_source_cache_build(testing_workdir):
    recipe = os.path.join(metadata_dir, "source_git_jinja2")
    config = api.Config(src_cache_root=testing_workdir)
    api.build(recipe, notest=True, config=config)

    git_cache_directory = f"{testing_workdir}/git_cache"
    assert os.path.isdir(git_cache_directory)

    files = [
        filename
        for _, _, filenames in walk(git_cache_directory)
        for filename in filenames
    ]

    assert len(files) > 0


@pytest.mark.slow
def test_copy_test_source_files(testing_config):
    testing_config.conda_pkg_format = 1
    recipe = os.path.join(metadata_dir, "_test_test_source_files")
    filenames = set()
    for copy in (False, True):
        testing_config.copy_test_source_files = copy
        outputs = api.build(recipe, notest=False, config=testing_config)
        filenames.add(os.path.basename(outputs[0]))
        tf = tarfile.open(outputs[0])
        found = False
        files = []
        for f in tf.getmembers():
            files.append(f.name)
            # nesting of test/test here is because info/test is the main folder
            # for test files, then test is the source_files folder we specify,
            # and text.txt is within that.
            if f.name == "info/test/test_files_folder/text.txt":
                found = True
                break
        if found:
            assert copy, (
                "'info/test/test_files_folder/text.txt' found in tar.bz2 "
                "but not copying test source files"
            )
            if copy:
                api.test(outputs[0])
            else:
                with pytest.raises(RuntimeError):
                    api.test(outputs[0])
        else:
            assert not copy, (
                "'info/test/test_files_folder/text.txt' not found in tar.bz2 "
                f"but copying test source files. File list: {files!r}"
            )


@pytest.mark.sanity
def test_copy_test_source_files_deps(testing_config):
    recipe = os.path.join(metadata_dir, "_test_test_source_files")
    for copy in (False, True):
        testing_config.copy_test_source_files = copy
        # test is that pytest is a dep either way.  Builds will fail if it's not.
        api.build(recipe, notest=False, config=testing_config)


def test_pin_depends(testing_config):
    """purpose of 'record' argument is to put a 'requires' file that records pinned run
    dependencies
    """
    recipe = os.path.join(metadata_dir, "_pin_depends_record")
    metadata = api.render(recipe, config=testing_config)[0][0]
    # the recipe python is not pinned, and having pin_depends set to record
    # will not show it in record
    assert not any(
        re.search(r"python\s+[23]\.", dep)
        for dep in metadata.meta["requirements"]["run"]
    )
    output = api.build(metadata, config=testing_config)[0]
    requires = package_has_file(output, "info/requires")
    assert requires
    if hasattr(requires, "decode"):
        requires = requires.decode()
    assert re.search(r"python\=[23]\.", requires), (
        "didn't find pinned python in info/requires"
    )


@pytest.mark.sanity
def test_failed_patch_exits_build(testing_config):
    with pytest.raises(RuntimeError):
        api.build(os.path.join(metadata_dir, "_bad_patch"), config=testing_config)


@pytest.mark.sanity
def test_version_mismatch_in_variant_does_not_infinitely_rebuild_folder(testing_config):
    # unsatisfiable; also not buildable (test_a recipe version is 2.0)
    testing_config.variant["test_a"] = "1.0"
    recipe = os.path.join(metadata_dir, "_build_deps_no_infinite_loop", "test_b")
    with pytest.raises(DependencyNeedsBuildingError):
        api.build(recipe, config=testing_config)
    # passes now, because package can be built, or is already built.  Doesn't matter which.
    testing_config.variant["test_a"] = "2.0"
    api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_provides_features_metadata(testing_config):
    recipe = os.path.join(metadata_dir, "_requires_provides_features")
    out = api.build(recipe, config=testing_config)[0]
    index = json.loads(package_has_file(out, "info/index.json"))
    assert "requires_features" in index
    assert index["requires_features"] == {"test": "ok"}
    assert "provides_features" in index
    assert index["provides_features"] == {"test2": "also_ok"}


@pytest.mark.sanity
def test_python_site_packages_path(testing_config):
    recipe = os.path.join(metadata_dir, "_python_site_packages_path")
    out = api.build(recipe, config=testing_config)[0]
    index = json.loads(package_has_file(out, "info/index.json"))
    assert "python_site_packages_path" in index
    assert index["python_site_packages_path"] == "some/path"


def test_overlinking_detection(
    testing_config, testing_workdir, variants_conda_build_sysroot
):
    testing_config.activate = True
    testing_config.error_overlinking = True
    testing_config.verify = False
    recipe = os.path.join(testing_workdir, "recipe")
    copy_into(
        os.path.join(metadata_dir, "_overlinking_detection"),
        recipe,
    )
    dest_sh = os.path.join(recipe, "build.sh")
    dest_bat = os.path.join(recipe, "bld.bat")
    copy_into(
        os.path.join(recipe, "build_scripts", "default.sh"), dest_sh, clobber=True
    )
    copy_into(
        os.path.join(recipe, "build_scripts", "default.bat"), dest_bat, clobber=True
    )
    api.build(recipe, config=testing_config, variants=variants_conda_build_sysroot)
    copy_into(
        os.path.join(recipe, "build_scripts", "no_as_needed.sh"), dest_sh, clobber=True
    )
    copy_into(
        os.path.join(recipe, "build_scripts", "with_bzip2.bat"), dest_bat, clobber=True
    )
    with pytest.raises(OverLinkingError):
        api.build(recipe, config=testing_config, variants=variants_conda_build_sysroot)
    rm_rf(dest_sh)
    rm_rf(dest_bat)


def test_overlinking_detection_ignore_patterns(
    testing_config, testing_workdir, variants_conda_build_sysroot
):
    testing_config.activate = True
    testing_config.error_overlinking = True
    testing_config.verify = False
    recipe = os.path.join(testing_workdir, "recipe")
    copy_into(
        os.path.join(metadata_dir, "_overlinking_detection_ignore_patterns"),
        recipe,
    )
    dest_sh = os.path.join(recipe, "build.sh")
    dest_bat = os.path.join(recipe, "bld.bat")
    copy_into(
        os.path.join(recipe, "build_scripts", "default.sh"), dest_sh, clobber=True
    )
    copy_into(
        os.path.join(recipe, "build_scripts", "default.bat"), dest_bat, clobber=True
    )
    api.build(recipe, config=testing_config, variants=variants_conda_build_sysroot)
    copy_into(
        os.path.join(recipe, "build_scripts", "no_as_needed.sh"), dest_sh, clobber=True
    )
    copy_into(
        os.path.join(recipe, "build_scripts", "with_bzip2.bat"), dest_bat, clobber=True
    )
    api.build(recipe, config=testing_config, variants=variants_conda_build_sysroot)
    rm_rf(dest_sh)
    rm_rf(dest_bat)


@pytest.mark.flaky(reruns=5, reruns_delay=2)
def test_overdepending_detection(testing_config, variants_conda_build_sysroot):
    testing_config.activate = True
    testing_config.error_overlinking = True
    testing_config.error_overdepending = True
    testing_config.verify = False
    recipe = os.path.join(metadata_dir, "_overdepending_detection")
    with pytest.raises(OverDependingError):
        api.build(recipe, config=testing_config, variants=variants_conda_build_sysroot)


@pytest.mark.skipif(not on_linux, reason="cannot compile for linux-ppc64le")
def test_sysroots_detection(testing_config, variants_conda_build_sysroot):
    recipe = os.path.join(metadata_dir, "_sysroot_detection")
    testing_config.activate = True
    testing_config.error_overlinking = True
    testing_config.error_overdepending = True
    testing_config.channel_urls = [
        "conda-forge",
    ]
    api.build(recipe, config=testing_config, variants=variants_conda_build_sysroot)


@pytest.mark.skipif(not on_mac, reason="macOS-only test (at present)")
def test_macos_tbd_handling(testing_config, variants_conda_build_sysroot):
    """
    Test path handling after installation... The test case uses a Hello World
    example in C/C++ for testing the installation of C libraries...
    """
    testing_config.activate = True
    testing_config.error_overlinking = True
    testing_config.error_overdepending = True
    testing_config.verify = False
    recipe = os.path.join(metadata_dir, "_macos_tbd_handling")
    api.build(recipe, config=testing_config, variants=variants_conda_build_sysroot)


@pytest.mark.sanity
def test_empty_package_with_python_in_build_and_host_barfs(testing_config):
    recipe = os.path.join(metadata_dir, "_empty_pkg_with_python_build_host")
    with pytest.raises(CondaBuildException):
        api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_empty_package_with_python_and_compiler_in_build_barfs(testing_config):
    recipe = os.path.join(metadata_dir, "_compiler_python_build_section")
    with pytest.raises(CondaBuildException):
        api.build(recipe, config=testing_config)


@pytest.mark.sanity
def test_downstream_tests(testing_config):
    upstream = os.path.join(metadata_dir, "_test_downstreams/upstream")
    downstream = os.path.join(metadata_dir, "_test_downstreams/downstream")
    api.build(downstream, config=testing_config, notest=True)
    with pytest.raises(CondaBuildUserError):
        api.build(upstream, config=testing_config)


@pytest.mark.sanity
def test_warning_on_file_clobbering(
    testing_config: Config,
    capfd: CaptureFixture,
    caplog: LogCaptureFixture,
) -> None:
    recipe_dir = os.path.join(metadata_dir, "_overlapping_files_warning")

    api.build(
        os.path.join(
            recipe_dir,
            "a",
        ),
        config=testing_config,
    )
    api.build(
        os.path.join(
            recipe_dir,
            "b",
        ),
        config=testing_config,
    )
    # The clobber warning here is raised when creating the test environment for b
    if Version(conda_version) >= Version("24.9.0"):
        # conda >=24.9.0
        clobber_warning_found = False
        for record in caplog.records:
            if "ClobberWarning:" in record.message:
                clobber_warning_found = True
        assert clobber_warning_found
    else:
        # before the new lazy index added in conda 24.9.0
        # see https://github.com/conda/conda/commit/1984b287548a1a526e8258802a6f1fec2a11ecc3
        out, err = capfd.readouterr()
        assert "ClobberWarning" in err

    with pytest.raises((ClobberError, CondaMultiError)):
        with env_var("CONDA_PATH_CONFLICT", "prevent", reset_context):
            api.build(os.path.join(recipe_dir, "b"), config=testing_config)


@pytest.mark.sanity
@pytest.mark.skip(reason="conda-verify is deprecated because it is unsupported")
def test_verify_bad_package(testing_config):
    from conda_verify.errors import PackageError

    recipe_dir = os.path.join(fail_dir, "create_bad_folder_for_conda_verify")
    api.build(recipe_dir, config=testing_config)
    with pytest.raises(PackageError):
        testing_config.exit_on_verify_error = True
        api.build(recipe_dir, config=testing_config)
    # ignore the error that we know should be raised, and re-run to make sure it is actually ignored
    testing_config.ignore_verify_codes = ["C1125", "C1115"]
    api.build(recipe_dir, config=testing_config)


@pytest.mark.sanity
def test_ignore_verify_codes(testing_config):
    recipe_dir = os.path.join(metadata_dir, "_ignore_verify_codes")
    testing_config.exit_on_verify_error = True
    # this recipe intentionally has a license error.  If ignore_verify_codes works,
    #    it will build OK.  If not, it will error out.
    api.build(recipe_dir, config=testing_config)


@pytest.mark.sanity
def test_extra_meta(testing_config, caplog):
    recipe_dir = os.path.join(metadata_dir, "_extra_meta")
    extra_meta_data = {"foo": "bar"}
    testing_config.extra_meta = extra_meta_data
    outputs = api.build(recipe_dir, config=testing_config)
    about = json.loads(package_has_file(outputs[0], "info/about.json"))
    assert "foo" in about["extra"] and about["extra"]["foo"] == "bar"
    assert (
        f"Adding the following extra-meta data to about.json: {extra_meta_data}"
        in caplog.text
    )


def test_symlink_dirs_in_always_include_files(testing_config):
    recipe = os.path.join(metadata_dir, "_symlink_dirs_in_always_include_files")
    api.build(recipe, config=testing_config)


def test_clean_rpaths(testing_config):
    recipe = os.path.join(metadata_dir, "_clean_rpaths")
    api.build(recipe, config=testing_config, activate=True)


def test_script_env_warnings(testing_config, recwarn):
    recipe_dir = os.path.join(metadata_dir, "_script_env_warnings")
    token = "CONDA_BUILD_PYTEST_SCRIPT_ENV_TEST_TOKEN"

    def assert_keyword(keyword):
        messages = [str(w.message) for w in recwarn.list]
        assert any([token in m and keyword in m for m in messages])
        recwarn.clear()

    api.build(recipe_dir, config=testing_config)
    assert_keyword("undefined")

    os.environ[token] = "SECRET"
    try:
        api.build(recipe_dir, config=testing_config)
        assert_keyword("SECRET")

        testing_config.suppress_variables = True
        api.build(recipe_dir, config=testing_config)
        assert_keyword("<hidden>")
    finally:
        os.environ.pop(token)


@pytest.mark.slow
def test_activated_prefixes_in_actual_path(testing_metadata):
    """
    Check if build and host env are properly added to PATH in the correct order.
    Do this in an actual build and not just in a unit test to avoid regression.
    Currently only tests for single non-"outputs" recipe with build/host split
    and proper env activation (Metadata.is_cross and Config.activate both True).
    """
    file = "env-path-dump"
    testing_metadata.config.activate = True
    testing_metadata.config.conda_pkg_format = 1
    meta = testing_metadata.meta
    meta["requirements"]["host"] = []
    meta["build"]["script"] = [
        f"echo %PATH%>%PREFIX%/{file}" if on_win else f"echo $PATH>$PREFIX/{file}"
    ]
    outputs = api.build(testing_metadata)
    env = {"PATH": ""}
    # We get the PATH entries twice: (which we should fix at some point)
    #   1. from the environment activation hooks,
    #   2. also beforehand from utils.path_prepended at the top of
    #      - build.write_build_scripts on Unix
    #      - windows.build on Windows
    #        And apparently here the previously added build env gets deactivated
    #        from the activation hook, hence only host is on PATH twice.
    prepend_bin_path(env, testing_metadata.config.host_prefix)
    if not on_win:
        prepend_bin_path(env, testing_metadata.config.build_prefix)
    prepend_bin_path(env, testing_metadata.config.host_prefix)
    prepend_bin_path(env, testing_metadata.config.build_prefix)
    expected_paths = [path for path in env["PATH"].split(os.pathsep) if path]
    actual_paths = [
        path
        for path in package_has_file(outputs[0], file).strip().split(os.pathsep)
        if path in expected_paths
    ]
    assert actual_paths == expected_paths


@pytest.mark.parametrize("add_pip_as_python_dependency", [False, True])
def test_add_pip_as_python_dependency_from_condarc_file(
    testing_metadata: MetaData,
    testing_workdir: str | os.PathLike,
    add_pip_as_python_dependency: bool,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """
    Test whether settings from .condarc files are needed.
    ref: https://github.com/conda/conda-libmamba-solver/issues/393
    """
    # TODO: SubdirData._cache_ clearing might not be needed for future conda versions.
    #       See https://github.com/conda/conda/pull/13365 for proposed changes.
    from conda.core.subdir_data import SubdirData

    # SubdirData's cache doesn't distinguish on add_pip_as_python_dependency.
    SubdirData._cache_.clear()

    # clear cache
    mocker.patch("conda.base.context.Context.pkgs_dirs", pkgs_dirs := (str(tmp_path),))
    assert context.pkgs_dirs == pkgs_dirs

    testing_metadata.meta["build"]["script"] = ['python -c "import pip"']
    testing_metadata.meta["requirements"]["host"] = ["python"]
    del testing_metadata.meta["test"]
    if add_pip_as_python_dependency:
        check_build_fails = nullcontext()
    else:
        check_build_fails = pytest.raises(BuildScriptException)

    conda_rc = Path(testing_workdir, ".condarc")
    conda_rc.write_text(f"add_pip_as_python_dependency: {add_pip_as_python_dependency}")
    with env_var("CONDARC", conda_rc, reset_context):
        with check_build_fails:
            api.build(testing_metadata)


def test_rendered_is_reported(testing_config, capsys):
    recipe_dir = os.path.join(metadata_dir, "outputs_overwrite_base_file")
    api.build(recipe_dir, config=testing_config)

    captured = capsys.readouterr()
    assert "Rendered as:" in captured.out
    assert "name: base-outputs_overwrite_base_file" in captured.out
    assert "- name: base-outputs_overwrite_base_file" in captured.out
    assert "- base-outputs_overwrite_base_file >=1.0,<2.0a0" in captured.out


@pytest.mark.skipif(on_win, reason="Tests cross-compilation targeting Windows")
def test_cross_unix_windows_mingw(testing_config):
    recipe = os.path.join(metadata_dir, "_cross_unix_windows_mingw")
    testing_config.channel_urls = [
        "conda-forge",
    ]
    api.build(recipe, config=testing_config)


@pytest.mark.parametrize(
    "recipe", sorted(Path(metadata_dir, "_build_script_errors").glob("*"))
)
@pytest.mark.parametrize("debug", (False, True))
def test_conda_build_script_errors_without_conda_info_handlers(tmp_path, recipe, debug):
    env = os.environ.copy()
    if debug:
        env["CONDA_VERBOSITY"] = "3"
    process = subprocess.run(
        ["conda", "build", recipe],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert process.returncode > 0
    all_output = process.stdout + "\n" + process.stderr

    # These should NOT appear in the output
    assert ">>> ERROR REPORT <<<" not in all_output
    assert "An unexpected error has occurred." not in all_output
    assert "Conda has prepared the above report." not in all_output

    # These should appear
    assert "returned non-zero exit status 1" in all_output

    # With verbose mode, we should actually see the traceback
    if debug:
        assert "Traceback" in all_output
        assert "CalledProcessError" in all_output
        assert "returned non-zero exit status 1" in all_output


def test_api_build_inject_jinja2_vars_on_first_pass(testing_config):
    recipe_dir = os.path.join(metadata_dir, "_inject_jinja2_vars_on_first_pass")
    with pytest.raises((RuntimeError, CondaBuildUserError)):
        api.build(recipe_dir, config=testing_config)

    testing_config.variant = {"python_min": "3.12"}
    api.build(recipe_dir, config=testing_config)


def test_ignore_run_exports_from_substr(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    api.build(str(metadata_path / "ignore_run_exports_from_substr"))
    assert "- python_abi " in capsys.readouterr().out


@pytest.mark.skipif(not on_linux, reason="One platform is enough")
def test_build_strings_glob_match(testing_config: Config) -> None:
    """
    Test issues observed in:
    - https://github.com/conda/conda-build/issues/5571#issuecomment-2605223563
    - https://github.com/conda-forge/conda-smithy/pull/2232#issuecomment-2618825581
    - https://github.com/conda-forge/blas-feedstock/pull/132
    - https://github.com/conda/conda-build/pull/5600
    """
    testing_config.channel_urls = ["conda-forge"]
    with pytest.raises(RuntimeError, match="Could not download"):
        # We expect an error fetching the license because we added a bad path on purpose
        # so we don't start the actual build. However, this is enough to get us through
        # the multi-output render phase where we examine compatibility of pins.
        api.build(metadata_path / "_blas_pins", config=testing_config)


@pytest.mark.skipif(not on_linux, reason="needs __glibc virtual package")
def test_api_build_grpc_issue5645(monkeypatch, tmp_path, testing_config):
    if Version(conda_version) < Version("25.1.0"):
        pytest.skip("needs conda 25.1.0")
    testing_config.channel_urls = ["conda-forge"]

    monkeypatch.chdir(tmp_path)
    api.build(str(metadata_path / "_grpc"), config=testing_config)


@pytest.mark.skipif(
    not on_mac, reason="needs to cross-compile from osx-64 to osx-arm64"
)
def test_api_build_pytorch_cpu_issue5644(monkeypatch, tmp_path, testing_config):
    # this test has to cross-compile from osx-64 to osx-arm64
    try:
        if "CONDA_SUBDIR" in os.environ:
            old_subdir = os.environ["CONDA_SUBDIR"]
            has_old_subdir = True
        else:
            has_old_subdir = False
            old_subdir = None
        os.environ["CONDA_SUBDIR"] = "osx-64"

        testing_config.channel_urls = ["conda-forge"]
        monkeypatch.chdir(tmp_path)
        api.build(str(metadata_path / "_pytorch_cpu"), config=testing_config)
    finally:
        if has_old_subdir:
            os.environ["CONDA_SUBDIR"] = old_subdir
        else:
            del os.environ["CONDA_SUBDIR"]


@pytest.mark.skipif(on_win, reason="file permissions not relevant on Windows")
def test_build_script_permissions(testing_config):
    recipe = os.path.join(metadata_dir, "_noarch_python")
    metadata = api.render(
        recipe, config=testing_config, dirty=True, remove_work_dir=False
    )[0][0]
    api.build(metadata, notest=True)
    build_script = os.path.join(metadata.config.work_dir, "conda_build.sh")
    assert (os.stat(build_script).st_mode & 0o777) == 0o700
    env_script = os.path.join(metadata.config.work_dir, "build_env_setup.sh")
    assert (os.stat(env_script).st_mode & 0o777) == 0o600
