import yaml
import regex
from datetime import datetime

import requests
import mitmproxy

RPS_BY_DATABASE = dict()

INFLUXDB_GROUP_BY_QUERY_PATTERN = regex.compile(r"""
    ^                                     # beginning of string
    select\b                              # must start with select statement (followed by word boundary)
    \s+                                   # 1 or more whitespaces
    (?:count|min|max|mean|sum|first|last) # an aggregate function
    \(                                    # time with opening bracket
    .*                                    # the field name
    \)                                    # closing bracket
    \s+                                   # 1 or more whitespaces
    \bfrom\b                              # the from statement should follow (with word boundaries)
    \s+                                   # 1 or more whitespaces
    (.*)                                  # the from content                                              group 1  (measurement)
    \s+                                   # 1 or more whitespaces
    \bwhere\b                             # the where statement is always present in a grafana query
    \s+                                   # 1 or more whitespaces
    .*?                                   # any where content before the time filters
    (time.+?)                             # 1st time filter                                               group 2 (1st time filter)
    (and\s+time.+?)?                       # optional 2nd time filter                                      group 3 (2nd time filter)            [optional]
    (?:and.*)?                            # any where content after the time filters
    \bgroup\sby\b                         # match group by statement
    \s+                                   # 1 or more whitespaces
    time\(                                # time with opening bracket
    ([\.\d]+)                             # the magnitude                                                 group 4  (interval magnitude)
    ([s|m|h|d|w|y])                       # the unit                                                      group 5  (interval unit)
    \)                                    # closing bracket
    .*                                    # rest of the request - don't care
    $                                     # end of string
    """,  regex.VERBOSE | regex.I)

INFLUXDB_RELATIVE_TIME_FILTER_PATTERN = regex.compile(r"""
    ^                                     # beginning of string
    time                                  # time field selector
    \s+                                   # 1 or more whitespaces
    >                                     # comparator
    \s+                                   # 1 or more whitespaces
    now()                                 # current time
    \s+                                   # 1 or more whitespaces
    -                                     # less
    \s+                                   # 1 or more whitespaces
    ([\.\d]+)                             # the magnitude                                                 group 1  (interval magnitude)
    ([s|m|h|d|w|y])                       # the unit                                                      group 2  (interval unit)
    .*                                    # rest of the filter - don't care
    $                                     # end of string
    """, regex.VERBOSE | regex.I)

INFLUXDB_ABSOLUTE_TIME_FILTER_PATTERN = regex.compile(r"""
    ^                                     # beginning of string
    (?:                                   # beginning of optional AND token
    and                                   # and token
    \s+                                   # 1 or more whitespaces
    )?                                    # end of optional AND token
    time                                  # time field selector
    \s+                                   # 1 or more whitespaces
    ([<>])                                # comparator                                                    group 1 (comparator)
    \s+                                   # 1 or more whitespaces
    ([\.\d]+)                             # the timestamp                                                 group 2  (timestamp)
    s                                     # the timestamp unit
    .*                                    # rest of the filter - don't care
    $                                     # end of string
    """, regex.VERBOSE | regex.I)

CONFIG = dict()

def load_config(path):
    CONFIG.update(yaml.load(open(path)))

def check_config(ctx):
    if CONFIG.get('influxdb_url') is None:
        ctx.log("ERROR No 'influxdb_url' specified in configuration.")
        
    if CONFIG.get('retention_policies') is None:
        ctx.log("ERROR No 'retention_policies' specified in configuration.")
    if not isinstance(CONFIG.get('retention_policies'), dict):
        ctx.log("ERROR 'retention_policies' specified in configuration should be a dict.")
    if '_default_' not in CONFIG.get('retention_policies') == 0:
        ctx.log("ERROR 'retention_policies' specified in configuration should contain an entry '_default_'.")
    for database, rps in CONFIG.get('retention_policies').iteritems():
        if not isinstance(rps, list):
            ctx.log("ERROR 'retention_policies.{}' specified in configuration should be a list.".format(database))
        if len(rps) == 0:
            ctx.log("ERROR 'retention_policies.{}' specified in configuration should not be empty.".format(database))
        for rp in rps:
            if rp.get('name') is None:
                ctx.log("ERROR All 'retention_policies.{}' specified in configuration should have a 'name'.".format(database))
            if rp.get('interval') is None:
                ctx.log("ERROR All 'retention_policies.{}' specified in configuration should have an 'interval'.".format(database))
            if rp.get('retention') is None:
                ctx.log("ERROR All 'retention_policies{}' specified in configuration should have a 'retention'.".format(database))

load_config('default.yml')

def modify_queries(ctx, queries, databases):
    """
    Grafana will zoom out with the following group by times:
    0.1s, 1s, 5s, 10s, 30s, 1m, 5m, 10m, 30m, 1h, 3h, 12h, 1d, 7d, 30d, 1y
    """
    try:
        database = databases[0]
        if database not in RPS_BY_DATABASE:
            if not update_rp_cache(ctx, database):
                return queries
        return [modify_query(ctx, query, database) for query in queries]
    except Exception as e:
        ctx.log("ERROR Could not modify queries ({}): {}".format(type(e).__name__, e.message))
        return queries

def modify_query(ctx, query, database):
    match = INFLUXDB_GROUP_BY_QUERY_PATTERN.search(query)
    if match is None:
        if CONFIG.get('debug'): ctx.log("DEBUG -> Not a Grafana GROUP BY query")
        return query
    if CONFIG.get('debug'): ctx.log("DEBUG Considering '{}'".format(query))
    original_measurement, time_filter_1, time_filter_2, interval_magnitude, interval_unit = match.groups()

    if explicit_retention_policy(ctx, original_measurement, database):
        if CONFIG.get('debug'): ctx.log("DEBUG -> Explicit retention policy")
        return query

    rp = rp_for_query(ctx, time_filter_1, time_filter_2, interval_magnitude, interval_unit, database)
    if rp is None:
        if CONFIG.get('debug'): ctx.log("DEBUG -> No configured retention policy for interval '{}{}'".format(interval_magnitude, interval_unit))
        return query
        
    if rp not in RPS_BY_DATABASE[database]:
        ctx.log("ERROR -> Interval '{}{}' configured to match a retention policy '{}' which doesn't exist on database {}".format(interval_magnitude, interval_unit, rp, database))
        return query

    new_measurement  = '"{}"."{}".{}'.format(database, rp, original_measurement)
    new_query = query.replace(original_measurement, new_measurement)
    if CONFIG.get('debug'): ctx.log("DEBUG -> Rewrite to '{}'".format(new_query))
    return new_query

def explicit_retention_policy(ctx, measurement, database):
    if '.' in measurement:
        parts = measurement.split('.')
        if parts[0].strip('"') in RPS_BY_DATABASE[database]:
            # first part of the measurement is an RP
            return True
        elif len(parts) > 1 and parts[1].strip('"') in RPS_BY_DATABASE[database]:
            # second part of the measurement is an RP
            return True
        else:
            # just a dotted series name
            return False
    else:
            # just a series name
            return False

def rp_for_query(ctx, time_filter_1, time_filter_2, interval_magnitude, interval_unit, database):
    if database in CONFIG['retention_policies']:
        retention_policies = CONFIG['retention_policies'][database]
    else:
        if '_default_' in CONFIG['retention_policies']:
            retention_policies = CONFIG['retention_policies']['_default_']
        else:
            ctx.log("WARN No _default_ retention policy mapping!")
            return None
            
    lookback = parse_time_filters(ctx, time_filter_1, time_filter_2)
    if lookback is None:
        if CONFIG['debug']: ctx.log("DEBUG Could not parse time filters: {} ... {}".format(time_filter_1, time_filter_2))
        return None
        
    interval = parse_interval(ctx, interval_magnitude, interval_unit)
    if interval is None:
        if CONFIG['debug']: ctx.log("DEBUG Could not parse group by interval: {}{}".format(interval_magnitude, interval_unit))
        return None
        
    retention_policies_with_data                  = [rp for rp in retention_policies           if float(rp['retention']) >= lookback]
    retention_policies_with_data_and_finer_points = [rp for rp in retention_policies_with_data if rp['interval']         <= interval]

    # From the retention policies with data in the query's (full) time
    # range AND points with variation finer than the query's group by
    # interval...
    if len(retention_policies_with_data_and_finer_points) > 0:
        # ... choose the retention policy with the *highest* interval
        # so as to minimize work done by InfluxDB
        return retention_policies_with_data_and_finer_points[-1]['name']
        
    # If there are no such retention policies, then fall back to
    # retention polices with data in the query's full time range...
    elif len(retention_policies_with_data) > 0:
        # ... choose the retention policy with the *lowest* interval
        # so as to approximate as closely as possible the intended
        # result
        return retention_policies_with_data[0]['name']

    # If there are no retention policies with data then bail
    else:
        return None
        
def parse_time_filters(ctx, tf1, tf2):
    if tf2 is None:
        # match1 = INFLUXDB_RELATIVE_TIME_FILTER_PATTERN.search(tf1)
        # if match1:
        #     interval_magnitude, interval_units = match1.groups()
        #     interval = parse_interval(ctx, interval_magnitude, interval_units)
        #     return 0
        return 0
    else:
        # Grafana always puts the '>' in the 1st time filter, but we
        # check both anyway.
        match1 = INFLUXDB_ABSOLUTE_TIME_FILTER_PATTERN.search(tf1)
        match2 = INFLUXDB_ABSOLUTE_TIME_FILTER_PATTERN.search(tf2)
        if match1 and match2:
            comparator1, timestamp1 = match1.groups()
            comparator2, timestamp2 = match2.groups()
            if comparator1 == '>':
                lookback_timestamp = timestamp1
            elif comprator2 == '>':
                lookback_timestamp = timestamp2
            else:
                return None
            lookback = (datetime.now() - datetime.fromtimestamp(int(lookback_timestamp))).total_seconds()
            return lookback
        else:
            return None

def parse_interval(ctx, magnitude, unit):
    if unit == 's':
        return float(magnitude)
    elif unit == 'm':
        return float(magnitude) * 60
    elif unit == 'h':
        return float(magnitude) * 3600
    elif unit == 'd':
        return float(magnitude) * 86400
    elif unit == 'w':
        return float(magnitude) * 604800
    elif unit == 'y':
        return float(magnitude) * 31536000
    else:
        return None
        
def update_rp_cache(ctx, database):
    params = {
        'q'  : 'SHOW RETENTION POLICIES ON {}'.format(database),
        'db' : database
    }
    ctx.log("INFO Requesting retention policies for InfluxDB database '{}' at {}".format(database, CONFIG['influxdb_url']))
    try:
        r = requests.get(CONFIG['influxdb_url'] + '/query', params=params)
        try:
            RPS_BY_DATABASE[database] = { rp[0] for rp in r.json()['results'][0]['series'][0]['values'] }
            return True
        except Exception as pe:
            ctx.log("ERROR Could not parse InfluxDB database '{}' retention policies response ({}): '{}'".format(database, type(pe).__name__, pe.message))
            return False
    except Exception as e:
        ctx.log("ERROR Could not make InfluxDB database '{}' retention policies request ({}): '{}'".format(database, type(e).__name__, e.message))
        return False

def start(ctx, args):
    if len(args) > 1:
        load_config(args[1])
        check_config(ctx)
    ctx.log("INFO InfluxDB Grafana retention policy proxy booting (via mitm).  Relaying requests to {}.".format(CONFIG['influxdb_url']))

def request(ctx, flow):
    params = flow.request.query
    if params and 'q' in params and 'db' in params:
        new_queries = modify_queries(ctx, params['q'], params['db'])
        params['q'] = new_queries
        flow.request.query = params
