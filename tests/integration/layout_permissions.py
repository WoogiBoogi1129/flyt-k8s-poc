#!/usr/bin/env python3
"""Run with FLYT_GUEST_LIBRARY pointing to a freshly built guest library.

Exercises the shared layout reader used by both Guest and Worker, including
the actual fsGroup permission change observed on a Kubernetes local PVC.
"""
import ctypes
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'runtime/shm/control'))
from provision import make_layout


class LayoutPermissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = ctypes.CDLL(os.environ['FLYT_GUEST_LIBRARY'])
        cls.library.flyt_layout_read.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        cls.library.flyt_layout_read.restype = ctypes.c_int

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'layout.bin'
        layout, _ = make_layout('1' * 32, '2' * 32, ['3' * 32])
        self.path.write_bytes(layout)

    def read(self, mode):
        self.path.chmod(mode)
        return self.library.flyt_layout_read(os.fsencode(self.path), ctypes.create_string_buffer(4096))

    def test_original_private_group_readable_layout(self):
        self.assertEqual(self.read(0o640), 0)

    def test_fsgroup_same_owner_and_group(self):
        self.assertEqual(self.read(0o660), 0)

    def test_world_writable_rejected(self):
        self.assertNotEqual(self.read(0o666), 0)

    @unittest.skipUnless(os.geteuid() == 0, 'requires root in the isolated build container')
    def test_group_writable_foreign_group_rejected(self):
        os.chown(self.path, os.geteuid(), os.getegid() + 1)
        self.assertNotEqual(self.read(0o660), 0)

    @unittest.skipUnless(os.geteuid() == 0, 'requires root in the isolated build container')
    def test_group_writable_foreign_owner_rejected(self):
        os.chown(self.path, os.geteuid() + 1, os.getegid())
        self.assertNotEqual(self.read(0o660), 0)

    def test_symlink_rejected(self):
        target = self.path.with_name('target')
        self.path.rename(target)
        self.path.symlink_to(target)
        self.assertNotEqual(self.read(0o640), 0)


if __name__ == '__main__':
    unittest.main()
