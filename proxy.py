"""
Auto Select RetentionPolicy InfluxDB 0.10 proxy for grafana
Authors: Zollner Robert,
         Paul Kuiper,
         Dhruv Bansal

Free use
"""

import gevent
from gevent import monkey

monkey.patch_all()

import sys
import yaml
import logging
import requests
from bottle import get, abort, run, request, response, redirect
import regex as re

RPS_BY_DATABASE = dict()

INFLUXDB_GROUP_BY_QUERY_PATTERN = re.compile(r"""

    ^                   # beginning of string
    select\b            # must start with select statement (followed by word boundary)
    \s+                 # 1 or more whitespaces
    (?:count|min|max|mean|sum|first|last) # an aggragate function
    \(                  # time with opening bracket
    .*                  # the field name
    \)                  # closing bracket
    \s+                 # 1 or more whitespaces
    \bfrom\b            # the from statement should follow (with word boundaries)
    \s+                 # 1 or more whitespaces
    (.*)                # the from content                                              group 1  (measurement)
    \s+                 # 1 or more whitespaces
    \bwhere\b           # the where statement is always present in a grafana query
    .*                  # the where content
    \bgroup\sby\b       # match group by statement
    \s+                 # 1 or more whitespaces
    time\(              # time with opening bracket
    ([\.\d]+)           # minimal 1 digit                                               group 2  (number of time units)
    ([s|m|h|d|w])       # the group by unit                                             group 3  (time unit)
    \)                  # closing bracket
    .*                  # rest of the request - don't care
    $                   # end of string
    """,  re.VERBOSE | re.I)

CONFIG = dict()

def load_config(path):
    CONFIG.update(yaml.load(open(path)))

load_config('default.yml')

LOGGER = None

def build_logger():
    l = logging.getLogger('influxdb-grafana-rp-proxy')
    h = logging.StreamHandler()
    if CONFIG.get('verbose', False):
        l.setLevel(logging.DEBUG)
        h.setLevel(logging.DEBUG)
    else:
        l.setLevel(logging.INFO)
        h.setLevel(logging.INFO)
    f = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    h.setFormatter(f)
    l.addHandler(h)
    return l
    
@get('/<path:path>')
def proxy_influx_query(path):
    """
    Capture the query events comming from Grafana.
    Investigate the query and replace the measurement name with a Retention Policy measurement name if possible.
    Send out the (modified or unmodified) query to Influx and return the result
    """
    params = dict(request.query)
    
    try:
        LOGGER.debug(params.get('q', None))
        params['q'] = modify_queries(params)
        LOGGER.debug(params.get('q', None))
    except Exception as e:
        LOGGER.exception("Error (%s) proxying query: %s", type(e).__class__, e.message, exc_info=True)
        pass
    headers = request.headers
    cookies = request.cookies
    r = requests.get(url=CONFIG['influxdb_url'] +'/'+ path, params=params, headers=headers, cookies=cookies, stream=True)
    for key, value in dict(r.headers).iteritems():
        if key == 'Content-Length':
            response.set_header('Content-Length', len(r.text)) # FIXME
        elif key == 'Content-Encoding':
            continue
        else:
            response.set_header(key, value)
    for key, value in dict(r.cookies).iteritems():
        response.cookies[key] = value
        
    response.status = r.status_code
    return r.text
    
def modify_queries(req):

    """
    Grafana will zoom out with the following group by times:
    0.1s, 1s, 5s, 10s, 30s, 1m, 5m, 10m, 30m, 1h, 3h, 12h, 1d, 7d, 30d, 1y
    """

    query_string = req.get('q')
    if query_string is None:
        return None
    try:
        database = req.get('db')
        if database is None:
            return query_string
        if database not in RPS_BY_DATABASE:
            update_rp_cache(database)
        return "\n".join([modify_query(database, line) for line in query_string.split("\n")])
    except Exception as e:
        LOGGER.exception("Error (%s) modifying query: %s", type(e).__class__, e.message, exc_info=True)
        return query_string

def modify_query(database, query):
    match = INFLUXDB_GROUP_BY_QUERY_PATTERN.search(query)
    if match is None:
        LOGGER.debug("Cannot parse query")
        return query
    original_measurement, interval_magnitude, interval_units = match.groups()

    interval = ''.join((interval_magnitude,interval_units))
    if database in CONFIG['rps_by_interval']:
        rps_by_interval = CONFIG['rps_by_interval'][database]
    else:
        if '_default_' in CONFIG['rps_by_interval']:
            rps_by_interval = CONFIG['rps_by_interval']['_default_']
        else:
            LOGGER.debug("No _default_ interval to retention policy mapping was configured!")
            return query
    if interval not in rps_by_interval:
        LOGGER.debug("Unknown interval [%s] for database [%s]", interval, database)
        return query
    rp = rps_by_interval[interval]
    if rp not in RPS_BY_DATABASE[database]:
        LOGGER.debug("Unknown retention policy [%s] for database [%s]", rp, database)
        return query

    if '.' in original_measurement:
        parts = original_measurement.split('.')
        if parts[0].strip('"') in RPS_BY_DATABASE[database]:
            Logger.debug("Requested specific retention policy [%s]", parts[0].strip('"'))
            return query
        elif len(parts) > 1 and parts[1].strip('"') in RPS_BY_DATABASE[database]:
            Logger.debug("Requested specific retention policy [%s]", parts[1].strip('"'))
            return query
        else:
            # This is a just a dotted series name
            pass
    else:
        # Nothing fancy here
        pass
    new_measurement  = '"{}"."{}".{}'.format(database, rp, original_measurement)
    new_query = query.replace(original_measurement, new_measurement)
    return new_query
    
def update_rp_cache(database):
    params = {
        'q'  : 'SHOW RETENTION POLICIES ON %s' % database,
        'db' : database
    }
    r = requests.get(CONFIG['influxdb_url'] + '/query', params=params)
    try:
        RPS_BY_DATABASE[database] = { rp[0] for rp in r.json()['results'][0]['series'][0]['values'] }
    except Exception as e:
        LOGGER.exception("Error (%s) fetching retention policies on database [%s] from InfluxDB: %s", type(e).__class__, database, e.message, exc_info=True)
        pass
        
if __name__ == '__main__':
    if len(sys.argv) > 1:
        load_config(sys.argv[1])
    LOGGER = build_logger()
    LOGGER.info("Starting proxy server on %s:%s", CONFIG['bind']['address'], CONFIG['bind']['port'])
    run(host=CONFIG['bind']['address'], port=CONFIG['bind']['port'], server='gevent')
