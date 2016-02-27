"""
Little Grafana Influx proxy
Author: Paul Kuiper
Free use
Requires: gevent, bottle, requests
"""

import gevent # micro threading framework
from gevent import monkey
monkey.patch_all()

import sys
import requests # http for humans
from bottle import get, abort, run, request, response, redirect  # micro web framework for humans (use flask if you like)
import regex as re # regular expressions libary

signal_set = set()
pattern = re.compile(r"""
    ^                   # beginning of string
    select\b            # must start with select statement (followed by word boundary)
    \s+                 # 1 or more whitespaces
    (count|min|max|mean|sum|first|last) # an aggragate function                         group 0 (aggregate)
    \(                  # time with opening bracket
    (.*)                # the field name                                                group 1 (field name)
    \)                  # closing bracket
    \s+                 # 1 or more whitespaces

    \bfrom\b            # the from statement should follow (with word boundaries)
    \s+                 # 1 or more whitespaces
    (.*)                # the from content                                              group 2  (measurement)
    \s+                 # 1 or more whitespaces

    \bwhere\b           # the where statement is always present in a grafana query
    (.*)                # the where content                                             group 3  (where clause)

    \bgroup\sby\b       # match group by statement
    \s+                 # 1 or more whitespaces
    time\(              # time with opening bracket
    (\d+)               # minimal 1 digit (does not match 0.1s!)                        group 4  (number of time units)
    ([s|m|h|d|w])       # the group by unit                                             group 5  (time unit)
    \)                  # closing bracket
    \s+                 # 1 or more whitespaces

    \border\sasc        # ordering is always present in a grafana query
    $                   # end of string
    """,  re.VERBOSE | re.I)

@get('/<path:path>') # pass through all other queries
def proxy_influx_query(path):
    """
    Capture the query events comming from Grafana.
    Investegate the query and replace the measurement name with a continuous query measurement name if possible.
    Send out the (modified or unmodified) query to Influx and return the result
    """

    forward_url = r'http://localhost:8086'  # The local influx host

    params = dict(request.query) # get all query parameters
    try:
        params['q'] = modify_query(params['q'], signal_set)
    except Exception as e:
        pass

    headers = request.headers
    cookies = request.cookies
    r = requests.get(url=forward_url + path, params=params, headers=headers, cookies=cookies, stream=True) # get data from influx
    if r.status_code == 200:
        for key, value in dict(r.headers).iteritems():
             response.set_header(key, value)
        for key, value in dict(r.cookies).iteritems():
            response.cookies[key] = value
        pass
    else:
        abort(r.status_code, r.reason) # NOK, return error
    return r.raw


def modify_query(qry, signal_set):
    """
    Grafana will zoom out with the following group by times:

    0.1s, 1s, 5s, 10s, 30s, 1m, 5m, 10m, 30m, 1h, 3h, 12h, 1d, 7d, 30d, 1y

    :param qry: the query to analyse and modify if needed
    :return: the modified query
    """

    try:
        items = pattern.search(qry).groups() # get the content of the different query parts
        table = items[2].strip("'").strip('"')
        measurement = '.'.join((table,'1'+items[5], items[0])) # construct agg. measurement name e.g.: metric.1h.max
        if measurement not in signal_set: # not a known signal (continuous query)
            return qry
        field_name = items[0] + '(' + items[1] + ')'
        group_by = 'time(' + str(items[4]) + items[5] + ')'
        qry = ' '.join(('select',field_name,'from','"'+ measurement + '"','where',items[3],'group by',group_by,'order asc'))
    except Exception as e:
        print e
        pass
    return qry

def get_signals(signal_set):
    params = {  'q' : 'list series',        # get all signals
                'p' : 'GrafanaPassword1!',
                'u' : 'Grafana'}
    while True:
        r = requests.get(r'http://euv-dashboard.eu.asml.com/data//db/db/series', params=params)
        r = r.json()[0]['points']           # convert the json to an object and get the data points
        signal_set =  {sig[1] for sig in r}  # create a set with the signal names only
        gevent.sleep(3600)                  # refresh signal list every hour

if __name__ == '__main__':
    print >> sys.stderr, "Starting signal list updates"
    gevent.spawn(get_signals, signal_set) # start the signal list refresh service
    print >> sys.stderr, "Starting proxy server"
    run(host='0.0.0.0', port=3004, server='gevent') # start the proxy webserver (choose your own port)
