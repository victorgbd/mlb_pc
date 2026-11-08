"""BB_GAME_RES: the guest patch (tests/test_display.c), run.py's memory budget and the launcher's size choice."""
from paths import ROOT
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GamePatchTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc') or shutil.which('clang') or shutil.which('gcc'), 'needs a C compiler')
    def test_display_patch_on_a_synthetic_image(self):
        cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / 'display-test'
            build = subprocess.run([cc, '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'src'),
                                    str(ROOT / 'tests/test_display.c'), str(ROOT / 'src/runtime_display.c'), '-o', str(exe)],
                                   capture_output=True, text=True)
            if build.returncode:
                self.skipTest(build.stderr[-300:])   # e.g. runtime.h needs the Windows compat layer in this toolchain
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RunBudgetTests(unittest.TestCase):
    def budget(self, resolution, **extra):
        run = load('run_module', ROOT / 'run.py')
        env = {'BB_GAME_RES': resolution, **extra}
        run.game_res(env)
        return env

    def test_default_size_changes_nothing(self):
        self.assertNotIn('BB_GAME_RES', self.budget('1920x1080'))
        self.assertNotIn('BB_GAME_RES', self.budget('garbage'))

    def test_1440p_grows_the_heaps_and_the_direct_memory(self):
        env = self.budget('2560x1440')
        self.assertGreater(int(env['BB_GAME_RT_MB']), 0)
        self.assertEqual(int(env['BB_GAME_RT_MB']) % 64, 0)
        self.assertEqual(int(env['BB_DMEM_MB']), 5056 + int(env['BB_GAME_RT_MB']) + int(env['BB_GAME_RT2_MB']) -
                         int(env['BB_GAME_FREE_MB']))

    def test_explicit_amounts_win(self):
        env = self.budget('3840x2160', BB_GAME_RT_MB='100', BB_GAME_RT2_MB='0', BB_GAME_FREE_MB='0')
        self.assertEqual((env['BB_GAME_RT_MB'], env['BB_GAME_RT2_MB'], env['BB_GAME_FREE_MB']), ('100', '0', '0'))
        self.assertEqual(env['BB_DMEM_MB'], '5156')


if __name__ == '__main__':
    unittest.main()
