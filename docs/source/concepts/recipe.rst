===================
Conda-build recipes
===================

To enable building `conda packages`_, :ref:`install and update conda
and conda-build <install-conda-build>`.

Building a conda package requires a recipe. A conda-build recipe
is a flat directory that contains the following files:

* ``meta.yaml`` — A file that contains all the metadata in the
  recipe. Only ``package/name`` and ``package/version`` are
  required.

* ``build.sh`` — The script that installs the files for the
  package on macOS and Linux. It is executed using the ``bash``
  command.

* ``bld.bat`` — The build script that installs the files for the
  package on Windows. It is executed using ``cmd``.

* ``run_test.[py,pl,sh,bat,r]`` — An optional Python test file, a
  test script that runs automatically if it is part of the recipe.

* Optional patches that are applied to the source.

* Other resources that are not included in the source and cannot
  be generated by the build scripts. Examples are icon files,
  readme files and build notes.

Review :doc:`../resources/define-metadata` to see a breakdown of the
components of a recipe, including:

  * Package name
  * Package version
  * Descriptive metadata
  * Where to obtain source code
  * How to test the package

.. tip::
  When you use the :ref:`conda skeleton <skeleton_ref>` command,
  the first 3 files — ``meta.yaml``, ``build.sh``, and
  ``bld.bat`` — are automatically generated for you.

Conda-build process
===================

Conda-build performs the following steps:

#. Reads the metadata.

#. Downloads the source into a cache.

#. Extracts the source into the source directory.

#. Applies any patches.

#. Re-evaluates the metadata, if source is necessary to fill any
   metadata values.

#. Creates a build environment and then installs the build
   dependencies there.

#. Runs the build script. The current working directory is the
   source directory with environment variables set. The build
   script installs into the build environment.

#. Performs some necessary post-processing steps, such as adding a shebang
   and ``rpath``.

#. Creates a conda package containing all the files in the build
   environment that are new from step 5, along with the necessary
   conda package metadata.

#. Tests the new conda package — if the recipe includes tests — by doing the following:

   * Deletes the build environment and source directory to ensure that the new conda package does not inadvertantly depend on artifacts not included in the package.

   * Creates a test environment with the package and its dependencies.

   * Runs the test scripts.

The archived `conda-recipes`_ repo, `AnacondaRecipes`_ aggregate repo,
and `conda-forge`_ feedstocks repo contain example recipes for many conda packages.

.. caution::
   All recipe files, including ``meta.yaml`` and build
   scripts, are included in the final package archive that is
   distributed to users. Be careful not to put sensitive information
   such as passwords into recipes where it could be made public.

The ``conda skeleton`` command can help to make
skeleton recipes for common repositories, such as PyPI_.


Deep dive
=========

Let's take a closer look at how conda-build uses a recipe
to create a package.

Templates
---------

When you build a conda package, conda-build renders the package
by reading a template in the ``meta.yaml``. See :ref:`jinja-templates`.

Templates are filled in using your conda-build configuration,
which shows the matrix of things to build against. The
conda-build configuration determines how many builds it has to do.
For example, defining a ``conda_build_config.yaml`` of the form
and filling it defines a matrix of 4 packages to build::

   foo:
     - 1.0
     - 2.0
   bar:
     - 1.2.0
     - 1.4.0

After this, conda-build determines what the outputs will be.
For example, if your conda-build configuration indicates that you
want 2 different versions of Python, conda-build will show
you the rendering for each Python version.

Environments
------------

To build the package, conda-build will make an environment for you
and install all of the build and run dependencies in that environment.
Conda-build will indicate where you can successfully build the package.
The prefix will take the form::

  <file path to conda>/conda-bld/<package name and string>/h_env_placeholder…

Conda-build downloads your package source and then builds the conda
package in the context of the build environment. For example, you may
direct it to download from a Git repo or pull down a tarball from
another source. See the :ref:`source-section` for more information.

What conda-build puts into a package depends on what you put into
the build, host, or run sections. See the :ref:`requirements`
for more information.
Conda-build will use this information to identify dependencies to
link to and identify the run requirements for the package. This allows
conda-build to understand what is needed to install the package.

Building
--------

Once the content is downloaded, conda-build runs the build step.
See the :ref:`meta-build` for more information.
The build step runs a script. It can be one that you provided.
See the :ref:`build-script` section for more information on this topic.

If you do not define the script section, then you can create a
``build.sh`` or a ``bld.bat`` file to be run.


Prefix replacement
------------------
The build environment is created in a placeholder prefix.
When the package is bundled, the prefix is set to a "dummy" prefix.
Once conda is ready to install the package, it rewrites the dummy
prefix with the final one.


Testing
-------

Once a package is built, conda-build has the ability to test it. To do this, it
creates another environment and installs the conda package. The form
of this prefix is::

  <file path to conda>/conda-bld/<package name + string>/_test_env_placeholder…

At this point, conda-build has all of the information from ``meta.yaml`` about
what its runtime dependencies are, so those dependencies are installed
as well. This generates a test runner script with a reference to the
testing ``meta.yaml`` that is created. See the :ref:`meta-test` for
more information. That file is run for testing.

Output metadata
---------------

After the package is built and tested, conda-build cleans up the
environments created during prior steps and outputs the metadata. The recipe for
the package is also added in the output metadata. The metadata directory
is at the top level of the package contents in the ``info`` directory.
The metadata contains information about the dependencies of the
package and a list of where all of the files in the package go when
it is installed. Conda reads that metadata when it needs to install.

Running ``conda install`` causes conda to:

#. Reach out to the repodata containing the dependencies for the package(s) you are installing.
#. Determine the correct dependencies.
#. Install a list of additional packages determined by those dependencies.
#. For each dependency package being installed:
   #. Unpack the tarball to look at the information contained within.
   #. Verify the file based on metadata in the package.
   #. Go through each file in the package and put it in the right location.

For additional information on ``conda install``, please visit the conda documentation `deep dive`_ page on that topic.

.. _`conda packages`: https://conda.io/projects/conda/en/latest/user-guide/concepts/packages.html
.. _`conda-recipes`: https://github.com/continuumio/conda-recipes
.. _`AnacondaRecipes`: https://github.com/AnacondaRecipes/aggregate
.. _`conda-forge`: https://github.com/conda-forge/feedstocks/tree/main/feedstocks
.. _PyPI: https://pypi.python.org/pypi
.. _`deep dive`: https://docs.conda.io/projects/conda/en/stable/dev-guide/deep-dives/install.html
