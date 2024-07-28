# Copyright 2015 The Brotli Authors. All rights reserved.
#
# Distributed under MIT license.
# See file LICENSE for detail or copy at https://opensource.org/licenses/MIT

import os
import platform
import re
import unittest
import logging as logger

try:
    from setuptools import Extension
    from setuptools import setup
except:
    from distutils.core import Extension
    from distutils.core import setup
from distutils.command.build_ext import build_ext
from distutils import errors
from distutils import dep_util
from distutils import log
import pkgconfig


logger.basicConfig(level=logger.INFO)


# MARK: - Check to see if we have a suitable libbrotli libraries installed on the system

BROTLI_REQUIRED_VERSION = '>= 1.0.9'

# Define the `libbrotli` libraries we're going to look for on the system
libs = ['libbrotlidec', 'libbrotlienc', 'libbrotlicommon']

# Default the extension kwargs and ext_kwarg_libraries to an empty dictionary and empty list for us to fill later
ext_kwargs: dict[str, list[str]] = {}
ext_kwarg_libraries: list[str] = []

def _pkgconfig_installed_check(lib: str, version: str = BROTLI_REQUIRED_VERSION, default_installed: bool = False) -> None:
    """Check if the given library is installed on the system."""

    # Assign default value to installed (as False)
    installed = default_installed

    # Check if the library exists on the system
    exists = pkgconfig.exists(lib)
    logger.info(f"Checking if {lib} exists: {exists}")

    # Sanity check the library version
    version = pkgconfig.modversion(lib)
    logger.info(f"Checking lib {lib} version: {version}")

    # Ensure the library is installed on the system
    installed = pkgconfig.installed(lib, version)
    logger.info(f"Checking for {lib} installed: {installed}")

    # If the library is not installed, raise an exception
    if not installed:
      raise Exception(f"Required library {lib} not found")


def _parse_pkg_configs(lib: str) -> None:
  """Parse the pkg-config file for the given library and update the ext_kwarg_libraries list.

  Return the extension kwargs containing the parsed pkg-config file consisting of the names of the
  libraries that the linker needs to look for.

  For example, the list will look like this at the end:
  [`brotlienc`, `brotlidec`, `brotlicommon`]
  """
  parsed_lib = pkgconfig.parse(lib)
  ext_kwarg_libraries.append(parsed_lib)
  logger.info(f"Extension kwargs libs: {ext_kwarg_libraries}")


def _find_system_libraries(libs: list[str]) -> None:
  """Find the system libraries on the system.

  For each library, check if it is installed on the system and extract the parsed pkg-config file
  and update the extension kwargs dict with the parsed pkg-config file so the linker knows what to
  look for.
  """
  for lib in libs:
    # Check if the library is installed on the system
    _pkgconfig_installed_check(lib, BROTLI_REQUIRED_VERSION)

    # Parse the pkg-config file for the given library
    _parse_pkg_configs(lib)


_find_system_libraries(libs)

ext_kwargs['libraries'] = ext_kwarg_libraries
logger.info(f"Extension kwargs: {ext_kwargs}")


CURR_DIR = os.path.abspath(os.path.dirname(os.path.realpath(__file__)))


def read_define(path, macro):
  """ Return macro value from the given file. """
  with open(path, 'r') as f:
    for line in f:
      m = re.match(rf'#define\s{macro}\s+(.+)', line)
      if m:
        return m.group(1)
  return ''


def get_version():
  """ Return library version string from 'common/version.h' file. """
  version_file_path = os.path.join(CURR_DIR, 'c', 'common', 'version.h')
  major = read_define(version_file_path, 'BROTLI_VERSION_MAJOR')
  minor = read_define(version_file_path, 'BROTLI_VERSION_MINOR')
  patch = read_define(version_file_path, 'BROTLI_VERSION_PATCH')
  if not major or not minor or not patch:
    return ''
  return f'{major}.{minor}.{patch}'


def get_test_suite():
  test_loader = unittest.TestLoader()
  test_suite = test_loader.discover('python', pattern='*_test.py')
  return test_suite


class BuildExt(build_ext):

  def get_source_files(self):
    filenames = build_ext.get_source_files(self)
    for ext in self.extensions:
      filenames.extend(ext.depends)
    return filenames

  def build_extension(self, ext):
    if ext.sources is None or not isinstance(ext.sources, (list, tuple)):
      raise errors.DistutilsSetupError(
        "in 'ext_modules' option (extension '%s'), "
        "'sources' must be present and must be "
        "a list of source filenames" % ext.name)

    ext_path = self.get_ext_fullpath(ext.name)
    depends = ext.sources + ext.depends
    if not (self.force or dep_util.newer_group(depends, ext_path, 'newer')):
      log.debug("skipping '%s' extension (up-to-date)", ext.name)
      return
    else:
      log.info("building '%s' extension", ext.name)

    c_sources = []
    for source in ext.sources:
      if source.endswith('.c'):
        c_sources.append(source)
    extra_args = ext.extra_compile_args or []

    objects = []

    macros = ext.define_macros[:]
    if platform.system() == 'Darwin':
      macros.append(('OS_MACOSX', '1'))
    elif self.compiler.compiler_type == 'mingw32':
      # On Windows Python 2.7, pyconfig.h defines "hypot" as "_hypot",
      # This clashes with GCC's cmath, and causes compilation errors when
      # building under MinGW: http://bugs.python.org/issue11566
      macros.append(('_hypot', 'hypot'))
    for undef in ext.undef_macros:
      macros.append((undef,))

    objs = self.compiler.compile(
        c_sources,
        output_dir=self.build_temp,
        macros=macros,
        include_dirs=ext.include_dirs,
        debug=self.debug,
        extra_postargs=extra_args,
        depends=ext.depends)
    objects.extend(objs)

    self._built_objects = objects[:]
    if ext.extra_objects:
      objects.extend(ext.extra_objects)
    extra_args = ext.extra_link_args or []
    # when using GCC on Windows, we statically link libgcc and libstdc++,
    # so that we don't need to package extra DLLs
    if self.compiler.compiler_type == 'mingw32':
        extra_args.extend(['-static-libgcc', '-static-libstdc++'])

    ext_path = self.get_ext_fullpath(ext.name)
    # Detect target language, if not provided
    language = ext.language or self.compiler.detect_language(c_sources)

    self.compiler.link_shared_object(
        objects,
        ext_path,
        libraries=self.get_libraries(ext),
        library_dirs=ext.library_dirs,
        runtime_library_dirs=ext.runtime_library_dirs,
        extra_postargs=extra_args,
        export_symbols=self.get_export_symbols(ext),
        debug=self.debug,
        build_temp=self.build_temp,
        target_lang=language)


NAME = 'Brotli'

VERSION = get_version()

URL = 'https://github.com/google/brotli'

DESCRIPTION = 'Python bindings for the Brotli compression library'

AUTHOR = 'The Brotli Authors'

LICENSE = 'MIT'

PLATFORMS = ['Posix', 'MacOS X', 'Windows']

CLASSIFIERS = [
    'Development Status :: 4 - Beta',
    'Environment :: Console',
    'Intended Audience :: Developers',
    'License :: OSI Approved :: MIT License',
    'Operating System :: MacOS :: MacOS X',
    'Operating System :: Microsoft :: Windows',
    'Operating System :: POSIX :: Linux',
    'Programming Language :: C',
    'Programming Language :: C++',
    'Programming Language :: Python',
    'Programming Language :: Python :: 2',
    'Programming Language :: Python :: 2.7',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 3.3',
    'Programming Language :: Python :: 3.4',
    'Programming Language :: Python :: 3.5',
    'Programming Language :: Unix Shell',
    'Topic :: Software Development :: Libraries',
    'Topic :: Software Development :: Libraries :: Python Modules',
    'Topic :: System :: Archiving',
    'Topic :: System :: Archiving :: Compression',
    'Topic :: Text Processing :: Fonts',
    'Topic :: Utilities',
]

PACKAGE_DIR = {'': 'python'}

PY_MODULES = ['brotli']

EXT_MODULES = [
    Extension(
        '_brotli',
        sources=[
            'python/_brotli.c',
            ],
        **ext_kwargs),
]

TEST_SUITE = 'setup.get_test_suite'

CMD_CLASS = {
    'build_ext': BuildExt,
}

with open("README.md", "r") as f:
    README = f.read()

setup(
    name=NAME,
    description=DESCRIPTION,
    long_description=README,
    long_description_content_type="text/markdown",
    version=VERSION,
    url=URL,
    author=AUTHOR,
    license=LICENSE,
    platforms=PLATFORMS,
    classifiers=CLASSIFIERS,
    package_dir=PACKAGE_DIR,
    py_modules=PY_MODULES,
    ext_modules=EXT_MODULES,
    test_suite=TEST_SUITE,
    cmdclass=CMD_CLASS)
