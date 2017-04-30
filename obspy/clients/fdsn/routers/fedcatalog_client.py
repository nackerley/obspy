#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FDSN Web service client for ObsPy.

:class:`~obspy.clients.fdsn.routers.FedcatalogProviders` contains data center
(provider) details retrieved from the fedcatalog service

:class:`~obspy.clients.fdsn.routers.FederatedClient` is the FDSN Web service request
client. The end user will work almost exclusively with this class, which
has methods similar to :class:`~obspy.clients.fdsn.Client`

:class:`~obspy.clients.fdsn.routers.FederatedRoutingManager` provides parsing
capabilities, and helps the FederatedClient make requests to each individual
provider's service

:func:`distribute_args()` helps determine what parameters belong to the routing
service and which belong to the data provider's client service

:func:`get_bulk_string()` helps turn text and parameters into a valid bulk
request text block.

:func:`data_to_request()` helper function to convert
:class:`~obspy.core.inventory.inventory.Inventory` or :class:`~obpsy.core.Stream`
into FDSNBulkRequests. Useful for comparing what has been retrieved with what was
requested.

:copyright:
    The ObsPy Development Team (devs@obspy.org)
    Celso G Reyes, 2017
    IRIS-DMC
:license:
    GNU Lesser General Public License, Version 3
    (https://www.gnu.org/copyleft/lesser.html)
"""
from __future__ import print_function
import sys
import collections
# from collections import OrderedDict
from threading import Lock
#import warnings
import os
import requests
from future.utils import native_str
# from requests.exceptions import (HTTPError, Timeout)
#from obspy.core import UTCDateTime
from obspy.core.inventory import Inventory
from obspy.core import Stream
from obspy.clients.fdsn.client import convert_to_string
from obspy.clients.fdsn.header import (FDSNException, FDSNNoDataException)
from obspy.clients.fdsn.routers.routing_client import (RoutingClient,
                                                       RoutingManager, ROUTING_LOGGER)
from obspy.clients.fdsn.routers import (FederatedRoute,)
from obspy.clients.fdsn.routers.fedcatalog_parser import (PreParse,
                                                          FedcatResponseLine,
                                                          DatacenterItem,
                                                          inventory_to_bulkrequests,
                                                          stream_to_bulkrequests)


# IRIS uses different codes for datacenters than obspy.
#         (iris_name , obspy_name)
REMAPS = (("IRISDMC", "IRIS"),
          ("GEOFON", "GFZ"),
          ("SED", "ETH"),
          ("USPC", "USP"))

FEDCATALOG_URL = 'https://service.iris.edu/irisws/fedcatalog/1/'

def distribute_args(argdict):
    """
    divide a dictionary's keys between fedcatalog and provider's service

    When the FederatedClient is called with a bunch of keyword arguments,
    it should call the Fedcatalog service with a large subset of these.
    Most will be incorporated into the bulk data requests that will be
    sent to the client's service. However a few of these are not allowed
    to be passed in this way.  These are prohibited, and will be removed
    from the fedcat_kwargs.

    The client's service will not need most of these keywords, since
    they are included in the bulk request.  However, some keywords are
    required by the Client class, so they are allowed through.

    :type argdict: dict
    :param argdict: keyword arugments that were passed to the FederatedClient
    :rtype: tuple(dict() , dict())
    :returns: tuple of dictionaries fedcat_kwargs, fdsn_kwargs
    """

    fedcatalog_prohibited_params = ('filename', 'attach_response', 'user', 'password', 'base_url')
    service_params = ('user', 'password', 'attach_response', 'filename')

    # fedrequest gets almost all arguments, except for some
    fed_argdict = argdict.copy()
    for key in fedcatalog_prohibited_params:
        if key in fed_argdict:
            del fed_argdict[key]

    # services get practically no arguments, since they're provided by the bulk request
    service_args = dict()
    for key in service_params:
        if key in argdict:
            service_args[key] = argdict[key]
    return fed_argdict, service_args


def get_bulk_string(bulk, arguments):
    """
    simplified version of get_bulk_string used for bulk requests

    This was mostly pulled from the :class:`~obspy.clients.fdsn.Client`,
    because it does not need to be associated with the client class.

    :type bulk: string, file
    :param bulk:
    :type arguments: dict
    :param arguments: key-value pairs to be added to the bulk request
    :rtype: str
    :returns: bulk request string suitable for sending to a client's get... service
    """
    # If its an iterable, we build up the query string from it
    # StringIO objects also have __iter__ so check for 'read' as well

    if arguments is not None:
        args = ["%s=%s" % (key, convert_to_string(value))
                for key, value in arguments.items() if value is not None]
    else:
        args = None

    # bulk might be tuple of strings...
    if isinstance(bulk, (str, native_str)):
        tmp = bulk
    elif isinstance(bulk, collections.Iterable) \
        and not hasattr(bulk, "read"):
        raise NotImplementedError("fedcatalog's get_bulk_string cannot handle vectors.")
    else:
        # if it has a read method, read data from there
        if hasattr(bulk, "read"):
            tmp = bulk.read()
        elif isinstance(bulk, (str, native_str)):
            # check if bulk is a local file
            if "\n" not in bulk and os.path.isfile(bulk):
                with open(bulk, 'r') as fh:
                    tmp = fh.read()
            # just use bulk as input data
            else:
                tmp = bulk
        else:
            msg = ("Unrecognized input for 'bulk' argument. Please "
                   "contact developers if you think this is a bug.")
            raise NotImplementedError(msg)
    if args:
        args = '\n'.join(args)
        bulk = '\n'.join((args, tmp))
    else:
        bulk = tmp
    assert isinstance(bulk, (str, native_str))
    return bulk

def get_existing_route(existing_routes):
    if isinstance(existing_routes, FederatedRoutingManager):
        frm = existing_routes
    elif isinstance(existing_routes, (str, native_str, FederatedRoute)):
        frm = FederatedRoutingManager(existing_routes)
    else:
        NotImplementedError("usure how to convert {} into FederatedRoutingManager")
    return frm
class FedcatalogProviders(object):
    """
    Class containing datacenter details retrieved from the fedcatalog service

    keys: name, website, lastupdate, serviceURLs {servicename:url,...},
    location, description

    >>> prov = FedcatalogProviders()
    >>> print(prov.pretty('IRISDMC'))  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
    IRISDMC:The IRIS Data Management Center, Seattle, WA, USA WEB:http://ds.iris.edu  LastUpdate...M

    """

    def __init__(self):
        """
        Initializer for FedcatalogProviders 
        """
        self._providers = dict()
        self._lock = Lock()
        self._failed_refreshes = 0
        self.refresh()

    def __iter__(self):
        """
        iterate through each provider name

        >>> fcp=FedcatalogProviders()
        >>> print(sorted([fcp.get(k,'name') for k in fcp]))  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        ['BGR',..., 'USPSC']
        """
        if not self._providers:
            self.refresh()
        return self._providers.__iter__()

    @property
    def names(self):
        """
        get names of datacenters

        >>> fcp=FedcatalogProviders()
        >>> print(sorted(fcp.names))  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        ['BGR',..., 'USPSC']

        :rtype:
        :returns: 
        """
        if not self._providers:
            self.refresh()
        return self._providers.keys()

    def get(self, name, detail=None):
        """
        get a datacenter property

        >>> fcp = FedcatalogProviders()
        >>> fcp.get('ORFEUS','description')
        'The ORFEUS Data Center'

        :type name: str
        :param name: provider name. such as IRISDMC, ORFEUS, etc.
        :type detail: str
        :param detail: property of interest.  eg, one of ('name', 'website',
        'lastupdate', 'serviceURLs', 'location', 'description').
        :rtype: str or dict()
        :returns: if no detail is provided, then the entire dict for the requested provider
        will be returned
        """
        if not self._providers:
            self.refresh()
        if not name in self._providers:
            return ""
        else:
            if detail:
                return self._providers[name][detail]
            else:
                return self._providers[name]

    def refresh(self, force=False):
        """
        retrieve provider profile from fedcatalog service

        >>> providers = FedcatalogProviders()
        >>> # providers.refresh(force=True)
        >>> providers.names #doctest: +ELLIPSIS
        dict_keys(['...'])

        :type force: bool
        :param force: attempt to retrieve data even if it already exists
        or if too many attempts have failed
        """
        if  self._providers and not force:
            return
        if self._lock.locked():
            return
        with self._lock:
            ROUTING_LOGGER.debug("Refreshing Provider List")
            if self._failed_refreshes > 3 and not force:
                ROUTING_LOGGER.error(
                    "Unable to retrieve provider profiles from fedcatalog service after {0} attempts"
                    % (self._failed_refreshes))

            try:
                url = 'https://service.iris.edu/irisws/fedcatalog/1/datacenters'
                r = requests.get(url, verify=False)
                self._providers = {v['name']: v for v in r.json()}
                self._failed_refreshes = 0
            except:
                ROUTING_LOGGER.error(
                    "Unable to update provider profiles from fedcatalog service")
                self._failed_refreshes += 1
            else:
                for iris_name, obspy_name in REMAPS:
                    if iris_name in self._providers:
                        self._providers[obspy_name] = self._providers[iris_name]

    def pretty(self, name):
        """
        return nice text representation of service without too much details

        >>> providers = FedcatalogProviders()
        >>> print(providers.pretty("ORFEUS"))  #doctest: +ELLIPSIS
        ORFEUS:The ORFEUS Data Center, de Bilt, the Netherlands WEB:http://www.orfeus-eu.org  LastUpdate:...M
        >>> print(providers.pretty("IRIS") == providers.pretty("IRISDMC"))
        True

        :type name: str
        :param name: identifier provider (provider_id)
        :rtype: str
        :returns: formatted details about this provider
        """
        if not self._providers:
            self.refresh()
        if not name in self._providers:
            return ""
        return "{name}:{description}, {location} WEB:{website}  LastUpdate:{lastUpdate}".format(**self._providers[name])
        #fields = ("name", "description", "location", "website", "lastUpdate")
        #return '\n'.join(self._providers[name][k] for k in fields) + '\n'


PROVIDERS = FedcatalogProviders()

class FederatedClient(RoutingClient):
    """
    FDSN Web service request client.

    For details see the :meth:`~obspy.clients.fdsn.client.Client.__init__()`
    method.

    >>> from requests.packages.urllib3.exceptions import InsecureRequestWarning
    >>> requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    >>> client = FederatedClient()
    >>> print(client)  #doctest: +ELLIPSIS
    Federated Catalog Routing Client

    >>> inv = client.get_stations(network="I?", station="AN*", channel="*HZ")
    ...                           #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
    >>> print(inv)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
    Inventory created at ...Z
    	Created by: IRIS WEB SERVICE: fdsnws-station | version: 1...
    		    http://service.iris.edu/fdsnws/station/1/query
    	Sending institution: IRIS-DMC (IRIS-DMC)
    	Contains:
    		Networks (1):
    			IU
    		Stations (10):
    			IU.ANMO (Albuquerque, New Mexico, USA) (6x)
    			IU.ANTO (Ankara, Turkey) (4x)
    		Channels (0):
    <BLANKLINE>

    >>> inv = client.get_stations(network="I?", station="AN*", channel="*HZ", filename=sys.stderr)
    ...                           #doctest: +SKIP

    .. Warning: if output is sent directly to a file, then the success
                status will not be checked beyond gross failures, such as
                no data, no response, or a timeout
    """

    # TODO read back from a file containing the fedcatalog's raw response

    def __init__(self, **kwargs):
        """
        initializer for FederatedClient

        :type **kwargs: keyword arguments
        :param **kwargs: arguments destined for either Fedcatalog or Client
        """
        RoutingClient.__init__(self, **kwargs)
        PROVIDERS.refresh()

    def __str__(self):
        """
        String representation for FederatedClient

        :rtype: str
        :returns: string represention of the FederatedClient
        """
        # TODO: Make this more specific
        ret = "Federated Catalog Routing Client"
        return ret

    # -------------------------------------------------
    # FederatedClient.get_routing() and FederatedClient.get_routing_bulk()
    # communicate directly with the fedcatalog service
    # -------------------------------------------------
    def get_routing(self, routing_file=None, **kwargs):
        """
        send query to the fedcatalog service as param=value pairs (GET)

        Retrieves and parses routing details from the fedcatalog service,
        which takes a query, determines which datacenters/providers hold
        the appropriate data, and then sends back information about the holdings

        :type routing_file: str
        :param routing_file: filename used to write out raw fedcatalog response
        :type **kwargs: various
        :param **kwargs: arguments that will be passed to the fedcatalog service
        as GET parameters.  eg ... http://..../query?param1=val1&param2=val2&...
        :rtype: :class:`~obspy.clients.fdsn.routers.FederatedRoutingManager`
        :returns: parsed response from the FedCatalog service

        >>> client = FederatedClient()
        >>> params = {"station":"ANTO", "includeoverlaps":"true"}
        >>> frm = client.get_routing(**params)
        >>> for f in frm:
        ...   print(f)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        FederatedRoute for IRIS containing 0 query parameters and ... request items
        FederatedRoute for ORFEUS containing 0 query parameters and ... request items

        """
        assert 'bulk' not in kwargs, "To post a bulk request, use get_routing_bulk"
        resp = requests.get(FEDCATALOG_URL + "query", params=kwargs, verify=False)
        resp.raise_for_status()
        if routing_file:
            # TODO: implement the writing raw response data out to a file
            pass #write out to file

        frm = FederatedRoutingManager(resp.text)
        return frm

    def get_routing_bulk(self, bulk, routing_file=None, **kwargs):
        """
        send query to the fedcatalog service as a POST.

        Retrieves and parses routing details from the fedcatalog service,
        which takes a bulk request, determines which datacenters/providers hold
        the appropriate data, and then sends back information about the holdings

        :type bulk:
        :param bulk:
        :type routing_file:
        :param routing_file: file to write out raw fedcatalog response
        :type **kwargs: other parameters
        :param **kwargs: only kwargs that should go to fedcatalog
        :rtype: :class:`~obspy.clients.fdsn.routers.FederatedRoutingManager`
        :returns: parsed response from the FedCatalog service

        >>> client = FederatedClient()
        >>> params={"includeoverlaps":"true"}
        >>> frm = client.get_routing_bulk(bulk="* ANTO * * * *", **params)
        >>> for f in frm:
        ...   print(f)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        FederatedRoute for IRIS containing 0 query parameters and ... request items
        FederatedRoute for ORFEUS containing 0 query parameters and ... request items

        """
        if not isinstance(bulk, (str, native_str)) and isinstance(bulk, collections.Iterable):
            print(bulk, file=sys.stderr)
            bulk = get_bulk_string(bulk=bulk, arguments=kwargs)
        elif isinstance(bulk, (str, native_str)) and kwargs:
            bulk = get_bulk_string(bulk=bulk, arguments=kwargs)

        assert bulk, "Bulk is empty after homogenizing it via get_bulk_string"
        assert isinstance(bulk, (str, native_str)), \
            "Bulk should be a string, but is a " + bulk.__class__.__name__

        resp = requests.post(FEDCATALOG_URL + "query", data=bulk, verify=False)
        resp.raise_for_status()
        frm = FederatedRoutingManager(resp.text)
        return frm

    # -------------------------------------------------
    # The next routines will interface with the "regular" client
    # FederatedClient._request() : overloads the RoutingClient, called by
    #                              the query method 
    # FederatedClient.get_stations_bulk(): user facing method
    # FederatedClient.get_stations(): user facing method
    # FederatedClient.get_waveforms_bulk(): user facing method
    # FederatedClient.get_waveforms(): user facing method
    #
    # communicate directly with the obspy.fdsn.Client's service: eg. dataselect, station
    # -------------------------------------------------

    def _request(self, client=None, service=None, route=None, output=None,
                 passed=None, failed=None, filename=None, **kwargs):
        """
        function used to query FDSN webservice

        This is being called from one of the "...query_machine" methods
        of the RoutingClient.

        :meth:`~obspy.clients.fdsn.client.Client.get_waveforms_bulk` or
        :meth:`~obspy.clients.fdsn.client.Client.get_stations_bulk`

        :type client: :class:`~obspy.clients.fdsn.Client`
        :param client: client, associated with a datacenter
        :type service: str
        :param service: name of service, "DATASELECTSERVICE", "STATIONSERVICE"
        :type route: :class:`~obspy.clients.fdsn.route.FederatedRoute`
        :param route: used to provide
        :type output: container accepting "put"
        :param output: place where retrieved data go. Unused if data is sent to file
        :type failed: container accepting "put"
        :param failed: place where list of unretrieved bulk request lines go
        :type filename: str or open file handle
        :param filename: filename for streaming data from service
        :type **kwargs: various
        :param **kwargs: keyword arguments passed directly to the client's
        get_waveform_bulk() or get_stations_bulk() method.
        """

        bulk_services = {"DATASELECTSERVICE": client.get_waveforms_bulk,
                         "STATIONSERVICE": client.get_stations_bulk}

        # communicate via queues or similar. Therefore, make containers exist,
        # and have the 'put' routine
        assert service in bulk_services, "couldn't find {0}\n".format(service)
        assert route is not None, "missing route"
        assert filename or output is not None, "missing container for storing output [output]"
        assert filename or hasattr(output, 'put'), "'output' does not have a 'put' routine"
        assert passed is not None, "missing container for storing successful requests [passed]"
        assert hasattr(passed, 'put'), "'passed' does not have a 'put' routine"
        assert failed is not None, "missing container for storing failed requests [failed]"
        assert hasattr(failed, 'put'), "'failed' does not have a 'put' routine"


        try:
            # get_bulk is the client's "get_xxx_bulk" function.
            get_bulk = bulk_services.get(service)
        except ValueError:
            valid_services = '"' + ', '.join(bulk_services.keys)
            raise ValueError("Expected one of " + valid_services + " but got {0}",
                             service)

        try:
            if isinstance(filename, (str, native_str)):
                base_name = os.path.basename(filename)
                path_name = os.path.dirname(filename)
                base_name = '-'.join((route.provider_id, base_name))
                filename = os.path.join(path_name, base_name)
                ROUTING_LOGGER.info("sending file to :" + filename)
            if filename:
                get_bulk(bulk=route.text(service), filename=filename, **kwargs)
            else:
                data = get_bulk(bulk=route.text(service), filename=filename, **kwargs)
                req_details = data_to_request(data)
                ROUTING_LOGGER.info("Retrieved %d items from %s",
                                    len(req_details), route.provider_id)
                ROUTING_LOGGER.info('\n'+ str(req_details))
                output.put(data)
                passed.put(req_details)

        except FDSNNoDataException:
            failed.put(route.request_items)
            ROUTING_LOGGER.info("The provider %s could provide no data", route.provider_id)

        except FDSNException as ex:
            failed.put(route.request_items)
            print("Failed to retrieve data from: {0}", route.provider_id)
            print(ex)
            raise

    def get_waveforms_bulk(self, bulk, quality=None, minimumlength=None,
                           longestonly=None, filename=None, includeoverlaps=False,
                           reroute=False, existing_routes=None, **kwargs):
        """
        retrieve waveforms from data providers via POST request to the Fedcatalog service

        :type includeoverlaps: boolean
        :param includeoverlaps: retrieve same information from multiple sources
        (not recommended)
        :type reroute: boolean
        :param reroute: if data doesn't arrive from provider , see if it is available elsewhere
        :type existing_routes: str
        :param existing_routes: will skip initial query to fedcatalog service, instead
        using the information from here to make the queries.
        other parameters as seen in :meth:`~obspy.fdsn.clients.Client.get_waveforms_bulk`
        and :meth:`~obspy.fdsn.clients.Client.get_stations_bulk`
        :rtype: :class:`~obspy.core.stream.Stream`
        :returns: one or more traces in a stream

        >>> client = FederatedClient()
        >>> bulkreq = "IU ANMO * ?HZ 2010-02-27T06:30:00 2010-02-27T06:33:00"
        >>> tr = client.get_waveforms_bulk(bulk=bulkreq)
        ...        #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        >>> print(tr)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        6 Trace(s) in Stream:
        IU.ANMO.00.BHZ | 2010-02-27T06:30... | 20.0 Hz, 3600 samples
        IU.ANMO.00.LHZ | 2010-02-27T06:30... | 1.0 Hz, 180 samples
        ...
        IU.ANMO.10.VHZ | 2010-02-27T06:30... | 0.1 Hz, 18 samples

        """

        fed_kwargs, svc_kwargs = distribute_args(kwargs)
        fed_kwargs["includeoverlaps"] = includeoverlaps

        frm = self.get_routing_bulk(bulk=bulk, **fed_kwargs)\
                  if not existing_routes else get_existing_route(existing_routes)
        data, passed, failed = self.query(frm, "DATASELECTSERVICE", **svc_kwargs)

        if reroute and failed:
            ROUTING_LOGGER.info(str(len(failed)) + " items were not retrieved, trying again," +
                        " but from any provider (while still honoring include/exclude)")
            fed_kwargs["includeoverlaps"] = True
            frm = self.get_routing_bulk(bulk=str(failed), **fed_kwargs)
            more_data, passed, failed = self.query(frm, "DATASELECTSERVICE",
                                                   keep_unique=True, **svc_kwargs)
            if more_data:
                ROUTING_LOGGER.info("Retrieved %d additional items", len(passed))
                if data:
                    data += more_data
                else:
                    data = more_data
            if failed:
                ROUTING_LOGGER.info("Unable to retrieve %d items:", len(failed))
                ROUTING_LOGGER.info('\n'+ str(failed))

        return data

    def get_waveforms(self, network, station, location, channel, starttime, endtime,
                      includeoverlaps=False, reroute=False, existing_routes=None,
                      **kwargs):
        """
        retrieve waveforms from data providers via GET request to the Fedcatalog service

        :type includeoverlaps: boolean
        :param includeoverlaps: retrieve same information from multiple sources
        (not recommended)
        :type reroute: boolean
        :param reroute: if data doesn't arrive from provider , see if it is available elsewhere
        :type existing_routes: str
        :param existing_routes: will skip initial query to fedcatalog service, instead
        other parameters as seen in :meth:`~obspy.fdsn.clients.Client.get_waveforms`
        :rtype: :class:`~obspy.core.stream.Stream`
        :returns: one or more traces in a stream

        >>> from requests.packages.urllib3.exceptions import InsecureRequestWarning
        >>> requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
        >>> client = FederatedClient()
        >>> from obspy.core import  UTCDateTime
        >>> t_st = UTCDateTime("2010-02-27T06:30:00")
        >>> t_ed = UTCDateTime("2010-02-27T06:33:00")
        >>> tr = client.get_waveforms('IU', 'ANMO', '*', 'BHZ', t_st, t_ed)
        ...                           #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        >>> print(tr)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        2 Trace(s) in Stream:
        IU.ANMO.00.BHZ | 2010-02-27T06:30:00... 20.0 Hz, 3600 samples
        IU.ANMO.10.BHZ | 2010-02-27T06:30:00... 40.0 Hz, 7200 samples
        """

        fed_kwargs, svc_kwargs = distribute_args(kwargs)
        fed_kwargs["includeoverlaps"] = includeoverlaps
        assert "bulk" not in fed_kwargs, \
               "Bulk request should be sent to get_waveforms_bulk, not get_waveforms"

        frm = self.get_routing(network=network, station=station,
                               location=location, channel=channel,
                               starttime=starttime, endtime=endtime,
                               **fed_kwargs) if not existing_routes \
                                             else get_existing_route(existing_routes)

        data, passed, failed = self.query(frm, "DATASELECTSERVICE", **svc_kwargs)

        if reroute and failed:
            ROUTING_LOGGER.info(str(len(failed)) + " items were not retrieved, trying again," +
                        " but from any provider (while still honoring include/exclude)")
            fed_kwargs["includeoverlaps"] = True
            frm = self.get_routing_bulk(bulk=str(failed), **fed_kwargs)
            more_data, passed, failed = self.query(frm, "DATASELECTSERVICE",
                                                   keep_unique=True, **svc_kwargs)
            if more_data:
                ROUTING_LOGGER.info("Retrieved {} additional items".format(len(passed)))
                if data:
                    data += more_data
                else:
                    data = more_data
            if failed:
                ROUTING_LOGGER.info("Unable to retrieve {} items:".format(len(failed)))
                ROUTING_LOGGER.info('\n'+ str(failed))
        return data

    def get_stations_bulk(self, bulk, includeoverlaps=False, reroute=False, existing_routes=None,
                          **kwargs):
        """
        retrieve station metadata from data providers via POST request to the Fedcatalog service

        :type bulk: text (bulk request formatted)
        :param bulk: text containing request to send to router
        :type includeoverlaps: boolean
        :param includeoverlaps: retrieve same information from multiple sources
        (not recommended)
        :type reroute: boolean
        :param reroute: if data doesn't arrive from provider , see if it is available elsewhere
        :type existing_routes: str
        :param existing_routes: will skip initial query to fedcatalog service, instead
        To use an existing route, set bulk to "none"
        :rtype: :class:`~obspy.core.inventory.inventory.Inventory`
        :returns: an inventory tree containing network/station/channel metadata

        other parameters as seen in :meth:`~obspy.fdsn.clients.Client.get_stations_bulk`

        >>> from requests.packages.urllib3.exceptions import InsecureRequestWarning
        >>> requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
        >>> client = FederatedClient()
        >>> bulktxt = "level=channel\\nA? OKS? * ?HZ * *"
        >>> INV = client.get_stations_bulk(bulktxt)  #doctest: +ELLIPSIS
        >>> print(INV)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        Inventory created at 2...Z
            Created by: IRIS WEB SERVICE: fdsnws-station | version: 1...
                    http://service.iris.edu/fdsnws/station/1/query
            Sending institution: IRIS-DMC (IRIS-DMC)
            Contains:
                Networks (1):
                    AV
                Stations (2):
                    AV.OKSO (South, Okmok Caldera, Alaska)
                    AV.OKSP (Steeple Point, Okmok Caldera, Alaska)
                Channels (5):
                    AV.OKSO..BHZ, AV.OKSP..EHZ (4x)
        """


        fed_kwargs, svc_kwargs = distribute_args(kwargs)
        fed_kwargs["includeoverlaps"] = includeoverlaps

        frm = self.get_routing_bulk(bulk=bulk, **fed_kwargs) if not existing_routes \
                                                        else get_existing_route(existing_routes)

        # frm = self.get_routing_bulk(bulk=bulk, **fed_kwargs)
        inv, passed, failed = self.query(frm, "STATIONSERVICE", **svc_kwargs)

        if reroute and failed:
            ROUTING_LOGGER.info("%d items were not retrieved, trying again," +
                                " but from any provider (while still honoring include/exclude)",
                                len(failed))
            fed_kwargs["includeoverlaps"] = True
            frm = self.get_routing_bulk(bulk=str(failed), **fed_kwargs)
            more_inv, passed, failed = self.query(frm, "STATIONSERVICE",
                                                  keep_unique=True, **svc_kwargs)
            if more_inv:
                ROUTING_LOGGER.info("Retrieved %d additional items", len(passed))
                if inv:
                    inv += more_inv
                else:
                    inv = more_inv
            if failed:
                ROUTING_LOGGER.info("Unable to retrieve {} items:".format(len(failed)))
                ROUTING_LOGGER.info('\n'+ str(failed))

        return inv

    def get_stations(self,
                     includeoverlaps=False, reroute=False, existing_routes=None,
                     **kwargs):
        """
        retrieve station metadata from data providers via GET request to the Fedcatalog service

        :type includeoverlaps: boolean
        :param includeoverlaps: retrieve same information from multiple sources
        (not recommended)
        :type reroute: boolean
        :param reroute: if data doesn't arrive from provider , see if it is available elsewhere
        other parameters as seen in :meth:`~obspy.fdsn.clients.Client.get_stations`
        :type existing_routes: str
        :param existing_routes: will skip initial query to fedcatalog service, instead
        using the information from here to make the queries.
        :rtype: :class:`~obspy.core.inventory.inventory.Inventory`
        :returns: an inventory tree containing network/station/channel metadata

        >>> from requests.packages.urllib3.exceptions import InsecureRequestWarning
        >>> requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
        >>> fclient = FederatedClient()
        >>> INV = fclient.get_stations(network="A?", station="OK*",
        ...                           channel="?HZ", level="station",
        ...                           endtime="2016-12-31")  #doctest: +ELLIPSIS
        >>> print(INV)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        Inventory created at 2...Z
            Created by: IRIS WEB SERVICE: fdsnws-station | version: 1...
                    http://service.iris.edu/fdsnws/station/1/query
            Sending institution: IRIS-DMC (IRIS-DMC)
            Contains:
                Networks (1):
                    AV
                Stations (14):
                    AV.OKAK (Cape Aslik 2, Okmok Caldera, Alaska)
                    ...
                    AV.OKWR (West Rim, Okmok Caldera, Alaska)
                Channels (0):
        <BLANKLINE>

        Exclude a provider from being queried

        >>> keep_out = ["IRISDMC","IRIS","IRIS-DMC"]
        >>> fclient.exclude_provider = keep_out
        >>> INV2 = fclient.get_stations(network="I?", station="A*",
        ...                           level="network")
        ...                           #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        >>> print(INV2)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        Inventory created at ...Z
            Created by: ObsPy ...
                    https://www.obspy.org
            Sending institution: SeisComP3,SeisNet-mysql (GFZ,INGV-CNT,ODC)
            Contains:
                Networks (6):
                    IA, IB, II, IQ, IS, IV
                Stations (0):
        <BLANKLINE>
                Channels (0):
        <BLANKLINE>

        >>> fclient = FederatedClient(use_parallel=True)

        parallel request, but only one provider

        >>> INV = fclient.get_stations(network="A?", station="OK*",
        ...                           channel="?HZ", level="station",
        ...                           endtime="2016-12-31")  #doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
        >>> print(INV)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        Inventory created at 2...Z
            Created by: IRIS WEB SERVICE: fdsnws-station | version: 1...
                    http://service.iris.edu/fdsnws/station/1/query
            Sending institution: IRIS-DMC (IRIS-DMC)
            Contains:
                Networks (1):
                    AV
                Stations (14):
                    AV.OKAK (Cape Aslik 2, Okmok Caldera, Alaska)
                    ...
                    AV.OKWR (West Rim, Okmok Caldera, Alaska)
                Channels (0):
        <BLANKLINE>

        another parallel request, this time with several providers

        >>> INV2 = fclient.get_stations(network="I?", station="AN*",
        ...                           level="network", includeoverlaps="true")
        >>> print(INV2)  #doctest: +ELLIPSIS, +NORMALIZE_WHITESPACE
        Inventory created at ...Z
            Created by: ObsPy ...
                    https://www.obspy.org
            Sending institution: IRIS-DMC,SeisComP3 (IRIS-DMC,ODC)
            Contains:
                Networks (...):
                    IU (...)
                Stations (0):
        <BLANKLINE>
                Channels (0):
        <BLANKLINE>
        """

        fed_kwargs, svc_kwargs = distribute_args(kwargs)
        fed_kwargs["includeoverlaps"] = includeoverlaps

        assert "bulk" not in fed_kwargs, \
               "Bulk request should be sent to get_stations_bulk, not get_stations"

        frm = self.get_routing(**fed_kwargs) if not existing_routes \
                                             else get_existing_route(existing_routes)

        # query queries all providers
        inv, passed, failed = self.query(frm, "STATIONSERVICE", **svc_kwargs)

        if reroute and failed:
            ROUTING_LOGGER.info("%d items were not retrieved, trying again,"\
                                 + " but from any provider (while still honoring include/exclude)",
                                len(failed))
            fed_kwargs["includeoverlaps"] = True
            frm = self.get_routing_bulk(bulk=str(failed), **fed_kwargs)
            more_inv, passed, failed = self.query(frm, "STATIONSERVICE",
                                                  keep_unique=True, **svc_kwargs)
            if more_inv:
                ROUTING_LOGGER.info("Retrieved {} additional items".format(len(passed)))
                if inv:
                    inv += more_inv
                else:
                    inv = more_inv
            if failed:
                ROUTING_LOGGER.info("Unable to retrieve {} items:".format(len(failed)))
                ROUTING_LOGGER.info('\n'+ str(failed))

        return inv


class FederatedRoutingManager(RoutingManager):
    """
    This class wraps the response given by the federated catalog.  Its primary
    purpose is to divide the response into parcels, each being a
    FederatedRoute containing the information required for a single request.

    Input would be the response from the federated catalog, or a similar text
    file. Output is a list of FederatedRoute objects

    >>> from obspy.clients.fdsn import Client
    >>> url = 'https://service.iris.edu/irisws/fedcatalog/1/'
    >>> params = {"net":"A*", "sta":"OK*", "cha":"*HZ"}
    >>> r = requests.get(url + "query", params=params, verify=False)
    >>> frm = FederatedRoutingManager(r.text)
    >>> print(frm)
    FederatedRoutingManager with 1 items:
    FederatedRoute for IRIS containing 0 query parameters and 26 request items
    """

    def __init__(self, data):
        """
        initialize a FederatedRoutingManager object
        :type data: str
        :param data: text block
        :
        """
        RoutingManager.__init__(self, data, provider_details=PROVIDERS)  # removed kwargs

    def parse_routing(self, block_text):
        """
        create a list of FederatedRoute objects, one for each provider in response

        :type block_text:
        :param block_text:
        :rtype:
        :returns:

        >>> fed_text = '''minlat=34.0
        ... level=network
        ...
        ... DATACENTER=GEOFON,http://geofon.gfz-potsdam.de
        ... DATASELECTSERVICE=http://geofon.gfz-potsdam.de/fdsnws/dataselect/1/
        ... CK ASHT -- HHZ 2015-01-01T00:00:00 2016-01-02T00:00:00
        ...
        ... DATACENTER=INGV,http://www.ingv.it
        ... STATIONSERVICE=http://webservices.rm.ingv.it/fdsnws/station/1/
        ... HL ARG -- BHZ 2015-01-01T00:00:00 2016-01-02T00:00:00
        ... HL ARG -- VHZ 2015-01-01T00:00:00 2016-01-02T00:00:00'''
        >>> fr = FederatedRoutingManager(fed_text)
        >>> for f in fr:
        ...    print(f.provider_id + "\\n" + f.text('STATIONSERVICE'))
        GFZ
        level=network
        CK ASHT -- HHZ 2015-01-01T00:00:00.000 2016-01-02T00:00:00.000
        INGV
        level=network
        HL ARG -- BHZ 2015-01-01T00:00:00.000 2016-01-02T00:00:00.000
        HL ARG -- VHZ 2015-01-01T00:00:00.000 2016-01-02T00:00:00.000

        Here's an example parsing from the actual service:
        >>> import requests
        >>> from requests.packages.urllib3.exceptions import InsecureRequestWarning
        >>> requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
        >>> url = 'https://service.iris.edu/irisws/fedcatalog/1/'
        >>> r = requests.get(url + "query", params={"net":"IU", "sta":"ANTO", "cha":"BHZ",
        ...                  "endafter":"2013-01-01","includeoverlaps":"true",
        ...                  "level":"station"}, verify=False)
        >>> frp = FederatedRoutingManager(r.text)
        >>> for n in frp:
        ...     print(n.services["STATIONSERVICE"])
        ...     print(n.text("STATIONSERVICE"))
        http://service.iris.edu/fdsnws/station/1/
        level=station
        IU ANTO 00 BHZ 2010-11-10T21:42:00.000 2016-06-22T00:00:00.000
        IU ANTO 00 BHZ 2016-06-22T00:00:00.000 2599-12-31T23:59:59.000
        IU ANTO 10 BHZ 2010-11-11T09:23:59.000 2599-12-31T23:59:59.000
        http://www.orfeus-eu.org/fdsnws/station/1/
        level=station
        IU ANTO 00 BHZ 2010-11-10T21:42:00.000 2599-12-31T23:59:59.000
        IU ANTO 10 BHZ 2010-11-11T09:23:59.000 2599-12-31T23:59:59.000

        """

        fed_resp = []
        provider = FederatedRoute("EMPTY_EMPTY_EMPTY")
        parameters = None
        state = PreParse

        for raw_line in block_text.splitlines():
            line = FedcatResponseLine(raw_line)  # use a smarter, trimmed line
            state = state.next(line)
            if state == DatacenterItem:
                if provider.provider_id == "EMPTY_EMPTY_EMPTY":
                    parameters = provider.parameters
                provider = state.parse(line, provider)
                provider.parameters = parameters
                fed_resp.append(provider)
            else:
                state.parse(line, provider)
        if len(fed_resp) > 0 and (not fed_resp[-1].request_items):
            del fed_resp[-1]
        # TODO see if remap belongs in the FederatedClient instead
        remap = {
            "IRISDMC": "IRIS",
            "GEOFON": "GFZ",
            "SED": "ETH",
            "USPSC": "USP"
        }

        # remap provider codes because IRIS codes differ from OBSPY codes
        for dc in fed_resp:
            if dc.provider_id in remap:
                dc.provider_id = remap[dc.provider_id]
        return fed_resp

def data_to_request(data):
    """
    convert either station metadata or waveform data to a FDSNBulkRequests object

    :rtype: :class:`~obspy.clients.fdsn.routers.FDSNBulkRequests`
    :returns: representation of the data
    """
    if isinstance(data, Inventory):
        return inventory_to_bulkrequests(data)
    elif isinstance(data, Stream):
        return stream_to_bulkrequests(data)


if __name__ == '__main__':
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    import doctest

    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    doctest.testmod(exclude_empty=True, verbose=False)
