"""
Auto Select RetentionPolicy InfluxDB 0.10 proxy for grafana
Authors: Zollner Robert,
         Paul Kuiper

Free use
Requires: gevent, bottle, requests
"""

import gevent
from gevent import monkey

monkey.patch_all()

import sys
import requests
from bottle import get, abort, run, request, response, redirect
import regex as re

rp_db_map = dict()

CONFIG = {
    'influxdb_http':'http://localhost:8086',

    'bind_host': '0.0.0.0',
    'bind_port': '3004',

    'retention_policy_map' : {
        '0.1s': '"default"',
        '1s' : '"default"',
        '5s' : '"default"',
        '10s': '"10sec"',
        '30s': '"30sec"',
        '1m' : '"1min"',
        '5m' : '"5min"',
        '10m': '"30min"',
        '30m': '"30min"',
        '1h' : '"1hour"',
        '3h' : '"3hour"',
        '12h': '"12hour"',
        '1d' : '"24hour"',
        '7d' : '"24hour"',
        '30d': '"24hour"'
    }
}

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
    .*                  # rest of the request - don't care
    $                   # end of string
    """,  re.VERBOSE | re.I)


@get('/<path:path>')
def proxy_influx_query(path):
    """
    Capture the query events comming from Grafana.
    Investigate the query and replace the measurement name with a Retention Policy measurement name if possible.
    Send out the (modified or unmodified) query to Influx and return the result
    """

    forward_url = CONFIG['influxdb_http']  # The local influx host

    params = dict(request.query) # get all query parameters

    try:
        params['q'] = modify_query(params, rp_db_map)

    except Exception as e:
        print "EXC:", e
        pass
    headers = request.headers
    cookies = request.cookies
    r = requests.get(url=forward_url +'/'+ path, params=params, headers=headers, cookies=cookies, stream=True) # get data from influx

    if r.status_code == 200:
        for key, value in dict(r.headers).iteritems():
             response.set_header(key, value)

        for key, value in dict(r.cookies).iteritems():
            response.cookies[key] = value
        pass
    else:
        abort(r.status_code, r.reason) # NOK, return error

    return r.raw


def modify_query(req, rp_db_map):

    """
    Grafana will zoom out with the following group by times:
    0.1s, 1s, 5s, 10s, 30s, 1m, 5m, 10m, 30m, 1h, 3h, 12h, 1d, 7d, 30d, 1y
    """

    qry = req['q']
    qry_db = req['db']

    try:

        # print qry
        items = pattern.search(qry).groups() # get the content of the different query parts

        q_gtime = ''.join((items[4],items[5]))
        if q_gtime not in CONFIG['retention_policy_map']:
            return qry
            
        q_table = items[2]
        if '.' in q_table:
            q_rp,_,q_table = items[2].partition('.')
            if q_rp in CONFIG['retention_policy_map'].values():
                print 'specific RP requested, ignoring detection: ',q_rp,'-', q_table
                return qry
            else:
                # This is a dotted series name
                q_table = items[2]
         
                
            print q_gtime, q_rp,'-', q_table
            
        new_rp = CONFIG['retention_policy_map'][q_gtime]
        
        measurement = '.'.join((new_rp, q_table))
        new_qry = qry.replace(items[2], measurement)
        
        # Download list of RP for current Influxdb database
        if qry_db not in rp_db_map:
            influx_update_rp( rp_db_map, qry_db, '','');
        
        # Check if auto-calc RP is defined in InfluxDB database
        if new_rp.strip("\"") not in rp_db_map[qry_db]:
            print "[E]: RP [%s] in not defined in Influx database [%s]. skipping.." % (new_rp, qry_db), rp_db_map[qry_db]
            return qry
            
        # print 'old :[', items[2], '] new:[',measurement, "] qry:", new_qry
        return new_qry
        
    except Exception as e:
        print e
        pass
        
    return qry


def influx_update_rp(rp_map, r_db, r_user, r_pass):
    
    params = {  'q' : 'SHOW RETENTION POLICIES ON %s' % r_db,
                'db' : r_db}

    r = requests.get(CONFIG['influxdb_http'] + '/query', params=params)
    try:
        rp_list = { rp[0] for rp in r.json()['results'][0]['series'][0]['values'] }
        rp_map[r_db] = rp_list
        
    except Exception as e:
        print e
        pass
        
if __name__ == '__main__':

    print >> sys.stderr, "Starting proxy server"
    run(host=CONFIG['bind_host'], port=CONFIG['bind_port'], server='gevent')
