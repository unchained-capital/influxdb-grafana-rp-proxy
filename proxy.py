import yaml
import regex

import requests
import mitmproxy

RPS_BY_DATABASE = dict()

INFLUXDB_GROUP_BY_QUERY_PATTERN = regex.compile(r"""

    ^                                     # beginning of string
    select\b                              # must start with select statement (followed by word boundary)
    \s+                                   # 1 or more whitespaces
    (?:count|min|max|mean|sum|first|last) # an aggragate function
    \(                                    # time with opening bracket
    .*                                    # the field name
    \)                                    # closing bracket
    \s+                                   # 1 or more whitespaces
    \bfrom\b                              # the from statement should follow (with word boundaries)
    \s+                                   # 1 or more whitespaces
    (.*)                                  # the from content                                              group 1  (measurement)
    \s+                                   # 1 or more whitespaces
    \bwhere\b                             # the where statement is always present in a grafana query
    .*                                    # the where content
    \bgroup\sby\b                         # match group by statement
    \s+                                   # 1 or more whitespaces
    time\(                                # time with opening bracket
    ([\.\d]+)                             # the magnitude                                                 group 2  (interval magnitude)
    ([s|m|h|d|w|y])                       # the unit                                                      group 3  (interval unit)
    \)                                    # closing bracket
    .*                                    # rest of the request - don't care
    $                                     # end of string
    """,  regex.VERBOSE | regex.I)

CONFIG = dict()

def load_config(path):
    CONFIG.update(yaml.load(open(path)))

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
        return query
    # ctx.log("DEBUG Considering '{}'".format(query))
    original_measurement, interval_magnitude, interval_unit = match.groups()

    if explicit_retention_policy(original_measurement, database):
        # ctx.log("DEBUG -> Explicit retention policy")
        return query

    rp = rp_for_interval(interval_magnitude, interval_unit, database)
    if rp is None:
        # ctx.log("DEBUG -> No configured retention policy for interval '{}{}'".format(interval_magnitude, interval_unit))
        return query
        
    if rp not in RPS_BY_DATABASE[database]:
        ctx.log("ERROR -> Interval '{}{}' configured to match a retention policy '{}' which doesn't exist on database {}".format(interval_magnitude, interval_unit, rp, database))
        return query

    new_measurement  = '"{}"."{}".{}'.format(database, rp, original_measurement)
    new_query = query.replace(original_measurement, new_measurement)
    # ctx.log("DEBUG -> Rewrite to '{}'".format(new_query))
    return new_query

def explicit_retention_policy(measurement, database):
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

def rp_for_interval(interval_magnitude, interval_unit, database):
    if database in CONFIG['rps_by_limit']:
        rps_by_limit = CONFIG['rps_by_limit'][database]
    else:
        if '_default_' in CONFIG['rps_by_limit']:
            rps_by_limit = CONFIG['rps_by_limit']['_default_']
        else:
            mitmproxy.ctx.log("WARN No _default_ interval to retention policy mapping was configured!")
            return None
    interval = parse_interval(interval_magnitude, interval_unit)
    last_matching_rp = None
    for limit, rp in rps_by_limit:
        if interval >= limit:
            last_matching_rp = rp
        else:
            if last_matching_rp: return last_matching_rp
    return rps_by_limit[-1][-1]

def parse_interval(magnitude, unit):
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
        # FIXME shouldn't ever get here?
        return float(magnitude)
        
def update_rp_cache(ctx, database):
    params = {
        'q'  : 'SHOW RETENTION POLICIES ON {}'.format(database),
        'db' : database
    }
    ctx.log("INFO Requesting retention policies for InfluxDB database {} at {}".format(database, CONFIG['influxdb_url']))
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
    ctx.log("INFO InfluxDB Grafana retention policy proxy booting (via mitmproxy).  Relaying requests to {}.".format(CONFIG['influxdb_url']))

def request(ctx, flow):
    params = flow.request.query
    if 'q' in params and 'db' in params:
        new_queries = modify_queries(ctx, params['q'], params['db'])
        params['q'] = new_queries
        flow.request.query = params
