"""Offline build provenance comes from the object tree, not the current SDK default."""
from pathlib import Path
import plistlib
import os
import shutil
import subprocess
import tempfile
import unittest
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from release_backend import DISABLED_FEATURES, compiler_provenance, darwin_configure_options


class ReaderToolchainTests(unittest.TestCase):
    def test_disabled_features_match_darwin_configure_options(self):
        options = darwin_configure_options(Path('/root'), Path('/work'), Path('/boost'))
        self.assertTrue(DISABLED_FEATURES)
        for name in DISABLED_FEATURES:
            self.assertIn(f'-DBUILD_WITH_{name}=OFF', options)

    def test_compiler_and_selected_target_sdk_are_recorded_without_host_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            meta=base/'CMakeFiles/4.0/CMakeCXXCompiler.cmake';meta.parent.mkdir(parents=True)
            meta.write_text('set(CMAKE_CXX_COMPILER_ID "AppleClang")\nset(CMAKE_CXX_COMPILER_VERSION "18.1.0")\n')
            sdk=base/'selected fixture.sdk';sdk.mkdir()
            (sdk/'SDKSettings.plist').write_bytes(plistlib.dumps(dict(Version='15.4')))
            flags=base/'server/CMakeFiles/snapserver.dir/flags.make';flags.parent.mkdir(parents=True)
            flags.write_text(f'CXX_FLAGS = -O3 -isysroot "{sdk}"\n')
            p=compiler_provenance(base)
            self.assertEqual((p['compiler'],p['compiler_version'],p['sdk_version']),('AppleClang','18.1.0','15.4'))
            self.assertEqual(len(p['compiler_metadata_sha256']),64);self.assertEqual(len(p['target_flags_sha256']),64)
            self.assertNotIn(tmp,str(p))
            flags.write_text('CXX_FLAGS = -O3\n')
            with self.assertRaisesRegex(ValueError,'exact target SDK'):compiler_provenance(base)

    @unittest.skipUnless(sys.platform=='darwin' and shutil.which('cmake'),'Apple SDK and CMake required')
    def test_actual_release_configuration_selects_the_compiler_sdk(self):
        # No prewritten compiler metadata, target flags or cache. Generate with
        # the actual release options; independently ask Apple's installed tools.
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'source';(src/'server').mkdir(parents=True);build=d/'build'
            (src/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.20)\nproject(SDKCheck LANGUAGES CXX)\nadd_subdirectory(server)\n')
            (src/'server/CMakeLists.txt').write_text('add_executable(snapserver main.cpp)\n')
            (src/'server/main.cpp').write_text('int main(){return 0;}\n')
            env=dict(os.environ);env.pop('SDKROOT',None);env.pop('CMAKE_OSX_SYSROOT',None)
            subprocess.run(['cmake','-G','Unix Makefiles','-S',str(src),'-B',str(build)]+darwin_configure_options(d,d,d),
                           capture_output=True,check=True,timeout=30,env=env)
            expected=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-version'],text=True).strip()
            self.assertEqual(compiler_provenance(build)['sdk_version'],expected)
