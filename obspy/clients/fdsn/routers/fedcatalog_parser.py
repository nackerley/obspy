#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fedcatalog_response_parser conatains the FederatedRoute class, with the
supporting parsing routines
"""
from __future__ import print_function

class RoutingResponse(object):
    """base for all routed routes"""

    def __init__(self, provider_id, raw_requests=None):
        """
        :type provider_id: str
        :param provider_id: provider_id for the data provider
        :type raw_requests: iterable
        :param raw_requests: requests to be interpreted and passed to provider
        """
        self.provider_id = provider_id
        self.request_lines = raw_requests

    def __len__(self):
        if self.request_lines:
            return len(self.request_lines)
        return 0

    def __str__(self):
        if len(self) != 1:
            line_or_lines = " lines"
        else:
            line_or_lines = " line"
        return self.provider_id + ", with " + str(len(self)) + line_or_lines

    def add_request(self, line):
        """
        override this
        """
        raise NotImplementedError("RoutingResponse.add_request()")


class FederatedRoute(RoutingResponse):
    """
    >>> fed_resp = FederatedRoute("IRISDMC")
    >>> fed_resp.add_query_param(["lat=50","lon=20","level=cha"])
    >>> fed_resp.add_service("STATIONSERVICE","http://service.iris.edu/fdsnws/station/1/")
    >>> fed_resp.add_request("AI ORCD -- BHZ 2015-01-01T00:00:00 2016-01-02T00:00:00")
    >>> fed_resp.add_request("AI ORCD 04 BHZ 2015-01-01T00:00:00 2016-01-02T00:00:00")
    >>> print(fed_resp("STATIONSERVICE"))
    level=cha
    AI ORCD -- BHZ 2015-01-01T00:00:00 2016-01-02T00:00:00
    AI ORCD 04 BHZ 2015-01-01T00:00:00 2016-01-02T00:00:00
    """

    # TODO maybe see which parameters are supported by specific service (?)
    # for example. at this exact moment in time, SoCal's dataselect won't accept quality
    pass_through_params = {
        "DATASELECTSERVICE": ["longestonly", "quality", "minimumlength"],
        "STATIONSERVICE": [
            "level", "matchtimeseries", "includeavailability",
            "includerestricted", "format"
        ]
    }

    def __init__(self, provider_id):
        """
        initialize a FederatedRoute
        :type provider_id: str
        :param provider_id: provider_id for the data provider
        """
        RoutingResponse.__init__(self, provider_id, raw_requests=[])
        self.parameters = []
        self.services = {}

    def add_service(self, service_name, service_url):
        """add a service url to this response
        :param service_name: name such as STATIONSERVICE, DATASELECTSERVICE, or DATACENTER
        :param service_url: url of service, like http://service.iris.edu/fdsnws/station/1/
        """
        self.services[service_name] = service_url

    def add_query_param(self, parameters):
        """
        add parameters to list that may be prepended to a request
        :param parameters: strings of the form "param=value"
        >>> fedresp = FederatedRoute("ABC")
        >>> fedresp.add_query_param(["level=station","quality=D"])
        >>> fedresp.add_query_param("onceuponatime=now")
        >>> fedresp.add_query_param(RequestLine("testing=true"))
        >>> fedresp.add_query_param([RequestLine("black=white"),RequestLine("this=that")])
        >>> print(",".join(fedresp.parameters))
        level=station,quality=D,onceuponatime=now,testing=true,black=white,this=that
        """
        if isinstance(parameters, str):
            self.parameters.append(parameters)
        elif isinstance(parameters, RequestLine):
            self.parameters.append(str(parameters))
        else:
            self.parameters.extend([str(p) for p in parameters])

    def add_request(self, lines):
        """append a single request to the list of requests
        :param request_lines: string or RequestLine that looks something like:
        NET STA LOC CHA yyyy-mm-ddTHH:MM:SS yyyy-mm-ddTHH:MM:SS
        """
        if isinstance(lines, (str, RequestLine)):
            self.request_lines.append(str(lines))  # no returns expected!
        elif isinstance(lines, (list, tuple)):
            self.request_lines.extend([str(line) for line in lines])

    def matched_and_unmatched(self, templates, level):
        """
        returns tuple of lines that (match, do_not_match) templates, to a level of detail specified
        by the level
        :param templates: containers containing strings NET.STA.LOC.CHA, NET.STA, or NET
        :param level: one of 'channel', 'response', 'station', 'network'

        >>> fedresp = FederatedRoute("ABC")
        >>> fedresp.add_request(["AB STA1 00 BHZ 2005-01-01 2005-03-24",
        ...                      "AB STA2 00 BHZ 2005-01-01 2005-03-24",
        ...                      "AB STA1 00 EHZ 2005-01-01 2005-03-24"])
        >>> print(fedresp.text("STATIONSERVICE"))
        AB STA1 00 BHZ 2005-01-01 2005-03-24
        AB STA2 00 BHZ 2005-01-01 2005-03-24
        AB STA1 00 EHZ 2005-01-01 2005-03-24
        >>> m, unm = fedresp.matched_and_unmatched(["AB.STA1"], "station")
        >>> print(m)
        ['AB STA1 00 BHZ 2005-01-01 2005-03-24', 'AB STA1 00 EHZ 2005-01-01 2005-03-24']
        >>> print(unm)
        ['AB STA2 00 BHZ 2005-01-01 2005-03-24']
        >>> m, _ = fedresp.matched_and_unmatched(["AB.STA1.00.BHZ", "AB.STA2.00.BHZ"], "channel")
        >>> print(m)
        ['AB STA1 00 BHZ 2005-01-01 2005-03-24', 'AB STA2 00 BHZ 2005-01-01 2005-03-24']
        """
        converter_choices = {
            "channel": lambda x: ".".join(x[0:4]),
            "response": lambda x: ".".join(x[0:4]),
            "station": lambda x: ".".join(x[0:2]),
            "network": lambda x: x[0]
        }
        converter = converter_choices[level]
        matched = [
            line for line in self.request_lines
            if converter(line.split()) in templates
        ]
        unmatched = [
            line for line in self.request_lines if line not in matched
        ]
        return matched, unmatched

    def text(self, target_service):
        """
        Return a string suitable for posting to a target service
        :param target_service: string name of target service, like 'DATASELECTSERVICE'
        """
        reply = self.selected_common_parameters(target_service)
        reply.extend(self.request_lines)
        return "\n".join(reply)

    def selected_common_parameters(self, target_service):
        """Return common parameters, targeted for a specific service
        This effecively filters out parameters that don't belong in a request.
        for example, STATIONSERVICE can accept level=xxx ,
        while DATASELECTSERVICE can accept longestonly=xxx
        :param target_service: string containing either 'DATASELECTSERVICE' or 'STATIONSERVICE'
        """
        reply = []
        for good in FederatedRoute.pass_through_params[target_service]:
            reply.extend(
                [c for c in self.parameters if c.startswith(good + "=")])
        return reply

    def __str__(self):
        if len(self) != 1:
            line_or_lines = " lines"
        else:
            line_or_lines = " line"
        return self.provider_id + ", with " + str(len(self)) + line_or_lines

class RequestLine(object):
    """line from federated catalog source that provides additional tests

    >>> fed_text = '''minlat=34.0
    ...
    ... DATACENTER=GEOFON,http://geofon.gfz-potsdam.de
    ... DATASELECTSERVICE=http://geofon.gfz-potsdam.de/fdsnws/dataselect/1/
    ... CK ASHT -- HHZ 2015-01-01T00:00:00 2016-01-02T00:00:00
    ...
    ... DATACENTER=INGV,http://www.ingv.it
    ... STATIONSERVICE=http://webservices.rm.ingv.it/fdsnws/station/1/
    ... HL ARG -- BHZ 2015-01-01T00:00:00 2016-01-02T00:00:00
    ... HL ARG -- VHZ 2015-01-01T00:00:00 2016-01-02T00:00:00'''

    >>> for y in fed_text.splitlines():
    ...    x = RequestLine(y)
    ...    edprs = [x.is_empty(), x.is_datacenter(), x.is_param(),
    ...             x.is_request(), x.is_service()]
    ...    print("\\n".join([str(edprs)]))
    [False, False, True, False, False]
    [True, False, False, False, False]
    [False, True, True, False, False]
    [False, False, True, False, True]
    [False, False, False, True, False]
    [True, False, False, False, False]
    [False, True, True, False, False]
    [False, False, True, False, True]
    [False, False, False, True, False]
    [False, False, False, True, False]
    """

    def is_empty(self):
        'true if self is empty'
        return self.line == ""

    def is_datacenter(self):
        'true if self contains datacenter details'
        return self.line.startswith('DATACENTER=')

    def is_param(self):
        'true if self could be a param=value'
        # true for provider, services, and parameter_list
        return '=' in self.line

    def is_request(self):
        'true if self might be in proper request format for posting to web services'
        return len(self.line.split()) == 6  # and test field values?

    def is_service(self):
        'true if a parameter that might be pointing to a service'
        return self.is_param() and self.line.split(
            "=")[0].isupper() and not self.is_datacenter()

    def __init__(self, line):
        self.line = line.strip()

    def __repr__(self):
        return self.line

    def __str__(self):
        return self.line


class ParserState(object):
    """
    Parsers leverage the known structure of Fedcatalog's response

    PREPARSE -> [PARAMLIST | EMPTY_LINE | DATACENTER]
    PARAMLIST -> [PARAMLIST | EMPTY_LINE]
    EMPTY_LINE -> [EMPTY_LINE | DATACENTER | DONE]
    DATACENTER -> [SERVICE]
    SERVICE -> [SERVICE | REQUEST]
    REQUEST -> [REQUEST | EMPTY_LINE | DONE ]
    """

    @staticmethod
    def parse(line, this_response):
        """abstract"""
        raise NotImplementedError("ParserState.parse()")

    @staticmethod
    def next(line):
        """abstract"""
        raise NotImplementedError("ParserState.next()")


class PreParse(ParserState):
    """Initial ParserState"""

    @staticmethod
    def parse(line, this_response):
        return this_response

    @staticmethod
    def next(line):
        if line.is_empty():
            return EmptyItem
        elif line.is_datacenter():
            return DatacenterItem
        elif line.is_param():
            return ParameterItem
        else:
            return ParserState


class ParameterItem(ParserState):
    """handle a parameter"""

    @staticmethod
    def parse(line, this_response):
        """Parse: param=value"""
        this_response.add_query_param(line)
        return this_response

    @staticmethod
    def next(line):
        if line.is_empty():
            return EmptyItem
        elif line.is_param():
            return ParameterItem
        else:
            raise RuntimeError(
                "Parameter should be followed by another parameter or an empty line"
            )


class EmptyItem(ParserState):
    """handle an empty line"""

    @staticmethod
    def parse(line, this_response):
        return this_response

    @staticmethod
    def next(line):
        if line.is_empty():
            return EmptyItem
        elif line.is_datacenter():
            return DatacenterItem
        else:
            raise RuntimeError(
                "expected either a DATACENTER or another empty line [" +
                str(line) + "]")


class DatacenterItem(ParserState):
    """handle data center"""

    @staticmethod
    def parse(line, this_response):
        """Parse: DATACENTER=id,http://url..."""
        _, rest = str(line).split('=')
        active_id, url = rest.split(',')
        this_response = FederatedRoute(active_id)
        this_response.add_service("DATACENTER", url)
        return this_response

    @staticmethod
    def next(line):
        if line.is_service():
            return ServiceItem
        else:
            raise RuntimeError(
                "DATACENTER line should be followed by a service")


class ServiceItem(ParserState):
    """handle service description"""

    @staticmethod
    def parse(line, this_response):
        """Parse: SERICENAME=http://service.url/"""
        svc_name, url = str(line).split('=')
        this_response.add_service(svc_name, url)
        return this_response

    @staticmethod
    def next(line):
        if line.is_service():
            return ServiceItem
        elif line.is_request():
            return RequestItem
        else:
            raise RuntimeError(
                "Service desc. should be followed by a request or another service"
            )


class RequestItem(ParserState):
    """handle request lines"""

    @staticmethod
    def parse(line, this_response):
        """Parse: NT STA LC CHA YYYY-MM-DDThh:mm:ss YY-MM-DDThh:mm:ss"""
        this_response.add_request(line)
        return this_response

    @staticmethod
    def next(line):
        if line.is_request():
            return RequestItem
        elif line.is_empty():
            return EmptyItem
        else:
            raise RuntimeError(
                "Requests should be followed by another request or an empty line"
            )


# main function
if __name__ == '__main__':
    import doctest
    doctest.testmod(exclude_empty=True)
"""
    import requests
    url = 'https://service.iris.edu/irisws/fedcatalog/1/'
    params = {"net":"A*", "sta":"OK*", "cha":"*HZ"}
    r = requests.get(url + "query", params=params, verify=False)

    frp = FederatedRoutingManager(r.text)
    for n in frp:
        print(n.request("STATIONSERVICE"))
"""