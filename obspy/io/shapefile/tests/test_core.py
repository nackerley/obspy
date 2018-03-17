# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
from future.builtins import *  # NOQA

import filecmp
import os
import unittest

from obspy import read_events, read_inventory
from obspy.core.util.misc import TemporaryWorkingDirectory
from obspy.io.shapefile.core import _write_shapefile, HAS_PYSHP


SHAPEFILE_SUFFIXES = (".shp", ".shx", ".dbf", ".prj")


@unittest.skipIf(not HAS_PYSHP, 'pyshp not installed')
class ShapefileTestCase(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 'data')
        self.catalog_shape_basename = os.path.join(self.path, 'catalog')
        self.inventory_shape_basename = os.path.join(self.path, 'inventory')

    def test_write_catalog_shapefile(self):
        cat = read_events()
        with TemporaryWorkingDirectory():
            _write_shapefile(cat, "catalog.shp")
            for suffix in SHAPEFILE_SUFFIXES:
                self.assertTrue(os.path.isfile("catalog" + suffix))
                self.assertTrue(
                    filecmp.cmp(
                        "catalog" + suffix,
                        self.catalog_shape_basename + suffix),
                    msg="%s not binary equal." % ("catalog" + suffix))

    def test_write_catalog_shapefile_via_plugin(self):
        cat = read_events()
        with TemporaryWorkingDirectory():
            cat.write("catalog.shp", "SHAPEFILE")
            for suffix in SHAPEFILE_SUFFIXES:
                self.assertTrue(os.path.isfile("catalog" + suffix))
                self.assertTrue(
                    filecmp.cmp(
                        "catalog" + suffix,
                        self.catalog_shape_basename + suffix),
                    msg="%s not binary equal." % ("catalog" + suffix))

    def test_write_inventory_shapefile(self):
        inv = read_inventory()
        with TemporaryWorkingDirectory():
            _write_shapefile(inv, "inventory.shp")
            for suffix in SHAPEFILE_SUFFIXES:
                self.assertTrue(os.path.isfile("inventory" + suffix))
                self.assertTrue(
                    filecmp.cmp(
                        "inventory" + suffix,
                        self.inventory_shape_basename + suffix),
                    msg="%s not binary equal." % ("inventory" + suffix))

    def test_write_inventory_shapefile_via_plugin(self):
        inv = read_inventory()
        with TemporaryWorkingDirectory():
            inv.write("inventory.shp", "SHAPEFILE")
            for suffix in SHAPEFILE_SUFFIXES:
                self.assertTrue(os.path.isfile("inventory" + suffix))
                self.assertTrue(
                    filecmp.cmp(
                        "inventory" + suffix,
                        self.inventory_shape_basename + suffix),
                    msg="%s not binary equal." % ("inventory" + suffix))


def suite():
    return unittest.makeSuite(ShapefileTestCase, 'test')


if __name__ == '__main__':
    unittest.main(defaultTest='suite')
