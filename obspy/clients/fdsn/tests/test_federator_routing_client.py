#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
:copyright:
    The ObsPy Development Team (devs@obspy.org)
:license:
    GNU Lesser General Public License, Version 3
    (https://www.gnu.org/copyleft/lesser.html)
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
from future.builtins import *  # NOQA

import collections
from distutils.version import LooseVersion
import unittest

import obspy
from obspy.core.compatibility import mock
from obspy.clients.fdsn.routing.federator_routing_client import \
    FederatorRoutingClient


_DummyResponse = collections.namedtuple("_DummyResponse", ["content"])


class FederatorRoutingClientTestCase(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        self.client = FederatorRoutingClient()
        self._cls = ("obspy.clients.fdsn.routing.federator_routing_client."
                     "FederatorRoutingClient")

    def test_get_service_version(self):
        # At the time of test writing the version is 1.1.1. Here we just
        # make sure it is larger.
        self.assertGreaterEqual(
            LooseVersion(self.client.get_service_version()),
            LooseVersion("1.1.1"))


    def test_response_splitting(self):
        data = """

DATACENTER=GEOFON,http://geofon.gfz-potsdam.de
DATASELECTSERVICE=http://geofon.gfz-potsdam1.de/fdsnws/dataselect/1/
STATIONSERVICE=http://geofon.gfz-potsdam2.de/fdsnws/station/1/
AF CER -- BHE 2007-03-15T00:47:00 2599-12-31T23:59:59
AF CER -- BHN 2007-03-15T00:47:00 2599-12-31T23:59:59


DATACENTER=INGV,http://www.ingv.it
DATASELECTSERVICE=http://webservices1.rm.ingv.it/fdsnws/dataselect/1/
STATIONSERVICE=http://webservices2.rm.ingv.it/fdsnws/station/1/
EVENTSERVICE=http://webservices.rm.ingv.it/fdsnws/event/1/
AC PUK -- HHE 2009-05-29T00:00:00 2009-12-22T00:00:00
        """
        self.assertEqual(
            FederatorRoutingClient.split_routing_response(data, "dataselect"),
            {"http://geofon.gfz-potsdam1.de": (
                "AF CER -- BHE 2007-03-15T00:47:00 2599-12-31T23:59:59\n"
                "AF CER -- BHN 2007-03-15T00:47:00 2599-12-31T23:59:59"),
             "http://webservices1.rm.ingv.it": (
                "AC PUK -- HHE 2009-05-29T00:00:00 2009-12-22T00:00:00"
             )
            })
        self.assertEqual(
            FederatorRoutingClient.split_routing_response(data, "station"),
            {"http://geofon.gfz-potsdam2.de": (
                "AF CER -- BHE 2007-03-15T00:47:00 2599-12-31T23:59:59\n"
                "AF CER -- BHN 2007-03-15T00:47:00 2599-12-31T23:59:59"),
                "http://webservices2.rm.ingv.it": (
                    "AC PUK -- HHE 2009-05-29T00:00:00 2009-12-22T00:00:00"
                )
            })

        # Error handling.
        with self.assertRaises(ValueError) as e:
            FederatorRoutingClient.split_routing_response(data, "random")
        self.assertEqual(e.exception.args[0],
                         "Service must be 'dataselect' or 'station'.")

    def test_get_waveforms(self):
        """
        This just dispatches to the get_waveforms_bulk() method - so no need
        to also test it explicitly.
        """
        with mock.patch(self._cls + ".get_waveforms_bulk") as p:
            p.return_value = "1234"
            st = self.client.get_waveforms(
                network="XX", station="XXXXX", location="XX",
                channel="XXX", starttime=obspy.UTCDateTime(2017, 1, 1),
                endtime=obspy.UTCDateTime(2017, 1, 2),
                latitude=1.0, longitude=2.0,
                longestonly=True, minimumlength=2)
        self.assertEqual(st, "1234")
        self.assertEqual(p.call_count, 1)
        self.assertEqual(
            p.call_args[0][0][0],
            ["XX", "XXXXX", "XX", "XXX", obspy.UTCDateTime(2017, 1, 1),
             obspy.UTCDateTime(2017, 1, 2)])
        # SNCLs + times should be filtered out.
        self.assertEqual(p.call_args[1],
                         {"longestonly": True,
                          "minimumlength": 2, "latitude": 1.0,
                          "longitude": 2.0})

        # Don't pass in the SNCLs.
        with mock.patch(self._cls + ".get_waveforms_bulk") as p:
            p.return_value = "1234"
            st = self.client.get_waveforms(
                starttime=obspy.UTCDateTime(2017, 1, 1),
                endtime=obspy.UTCDateTime(2017, 1, 2),
                latitude=1.0, longitude=2.0,
                longestonly=True, minimumlength=2)
        self.assertEqual(st, "1234")
        self.assertEqual(p.call_count, 1)
        self.assertEqual(
            p.call_args[0][0][0],
            ["*", "*", "*", "*", obspy.UTCDateTime(2017, 1, 1),
             obspy.UTCDateTime(2017, 1, 2)])
        self.assertEqual(p.call_args[1],
                         {"longestonly": True,
                          "minimumlength": 2, "latitude": 1.0,
                          "longitude": 2.0})

    def test_get_waveforms_bulk(self):
        # Some mock routing response.
        content = """
DATACENTER=GEOFON,http://geofon.gfz-potsdam.de
DATASELECTSERVICE=http://geofon.gfz-potsdam.de/fdsnws/dataselect/1/
STATIONSERVICE=http://geofon.gfz-potsdam.de/fdsnws/station/1/
AF CER -- LHZ 2017-01-01T00:00:00 2017-01-02T00:00:00
AF CVNA -- LHZ 2017-01-01T00:00:00 2017-01-02T00:00:00

DATACENTER=IRISDMC,http://ds.iris.edu
DATASELECTSERVICE=http://service.iris.edu/fdsnws/dataselect/1/
STATIONSERVICE=http://service.iris.edu/fdsnws/station/1/
EVENTSERVICE=http://service.iris.edu/fdsnws/event/1/
SACPZSERVICE=http://service.iris.edu/irisws/sacpz/1/
RESPSERVICE=http://service.iris.edu/irisws/resp/1/
AF CNG -- LHZ 2017-01-01T00:00:00 2017-01-02T00:00:00
AK CAPN -- LHZ 2017-01-01T00:00:00 2017-01-02T00:00:00
        """
        if hasattr(content, "encode"):
            data = content.encode()

        with mock.patch(self._cls + "._download") as p1, \
                mock.patch(self._cls + "._download_waveforms") as p2:
            p1.return_value = _DummyResponse(content=content)
            p2.return_value = "1234"

            st = self.client.get_waveforms_bulk(
                [["A*", "C*", "", "LHZ", obspy.UTCDateTime(2017, 1, 1),
                  obspy.UTCDateTime(2017, 1, 2)]],
                longestonly=True, minimumlength=2)
        self.assertEqual(st, "1234")

        self.assertEqual(p1.call_count, 1)
        self.assertEqual(p1.call_args[0][0],
                         "http://service.iris.edu/irisws/fedcatalog/1/query")
        self.assertEqual(p1.call_args[1]["data"], (
            b"format=request\n"
            b"A* C* -- LHZ 2017-01-01T00:00:00.000000 "
            b"2017-01-02T00:00:00.000000"))

        self.assertEqual(p2.call_args[0][0], {
            "http://geofon.gfz-potsdam.de": (
                "AF CER -- LHZ 2017-01-01T00:00:00 2017-01-02T00:00:00\n"
                "AF CVNA -- LHZ 2017-01-01T00:00:00 2017-01-02T00:00:00"),
            "http://service.iris.edu": (
                "AF CNG -- LHZ 2017-01-01T00:00:00 2017-01-02T00:00:00\n"
                "AK CAPN -- LHZ 2017-01-01T00:00:00 2017-01-02T00:00:00")})
        self.assertEqual(p2.call_args[1],
                         {"longestonly": True, "minimumlength": 2})


def suite():  # pragma: no cover
    return unittest.makeSuite(FederatorRoutingClientTestCase, 'test')


if __name__ == '__main__':  # pragma: no cover
    unittest.main(defaultTest='suite')
