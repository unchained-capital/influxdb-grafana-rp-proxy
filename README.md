# InfluxDB Grafana Retention Policy Proxy

Proxy inserted between Grafana and InfluxDB which auto-selects an
InfuxDB retention policy based on the size of the `GROUP BY` interval
specified in queries from Grafana.

Until InfluxDB either

a) intelligently merges data points from different retention policies
at query time (perhaps through layers for RPs?)

b) implements downsampling internally

a proxy such as this one will be required to effectively use Grafana
 with an InfluxDB containing downsampled data.  Watch the
 [GitHub issue](https://github.com/influxdata/influxdb/issues/5750)
 for progress.

This proxy

- assumes series names and tags are identical across retention
  policies.
- assumes database and retention policies do not have periods ('.') in
  their names (it's OK if series do).

Original code: @PaulKuiper https://github.com/influxdata/influxdb/issues/2625#issuecomment-161716174

## Installation

Ubuntu 14.04 setup:

```
$ sudo apt-get install python-regex python-requests mitmproxy
```

Or use [`pip`](https://docs.python.org/3.6/installing/index.html) one
you've cloned this repo:

```
$ sudo pip install -r requirements.txt
```

## Configuration

The [default configuration file](default.yml) is simple and
documented.  Modify it as you need.

## Usage

This proxy is intended to be run as a script for the `mitmdump` tool.

```
$ mitmdump --reverse "http:/localhost:8086" --port 3004 --script 'proxy.py default.yml'
```

Now configure Grafana to use an "InfluxDB server" at `localhost:3004`,
instead of the actual InfluxDB server (`localhost:8086` in this
example).

## Database Preparation

The following is just an example, written to match the
[default configuration](default.yml).

1) Create Retention Policy for every database.
```
ALTER RETENTION POLICY "default" ON "graphite" DURATION 1h REPLICATION 1 DEFAULT
CREATE RETENTION POLICY "one_week"  ON graphite DURATION 7d  REPLICATION 1
CREATE RETENTION POLICY "one_month" ON graphite DURATION 30d REPLICATION 1
CREATE RETENTION POLICY "forever"   ON graphite DURATION INF REPLICATION 1
```
2) Create Continuous Queries
```
CREATE CONTINUOUS QUERY graphite_default_to_one_week   ON graphite BEGIN SELECT mean(value) as value INTO graphite."one_week".:MEASUREMENT  FROM graphite."default"./.*/   GROUP BY time(10m), * END
CREATE CONTINUOUS QUERY graphite_one_week_to_one_month ON graphite BEGIN SELECT mean(value) as value INTO graphite."one_month".:MEASUREMENT FROM graphite."one_week"./.*/  GROUP BY time(1h),  * END
CREATE CONTINUOUS QUERY graphite_one_month_to_forever  ON graphite BEGIN SELECT mean(value) as value INTO graphite."forever".:MEASUREMENT   FROM graphite."one_month"./.*/ GROUP BY time(6h),  * END
```
3) Backfill historical data XX days.(only if needed)
```
SELECT mean(value) as value INTO graphite."one_week".:MEASUREMENT  FROM graphite."default"./.*/  WHERE time > now() - ? GROUP BY time(10m)*
SELECT mean(value) as value INTO graphite."one_month".:MEASUREMENT FROM graphite."one_week"./.*/ WHERE time > now() - ? GROUP BY time(1h)*
SELECT mean(value) as value INTO graphite."forever".:MEASUREMENT FROM graphite."one_month"./.*/ WHERE time > now() - ? GROUP BY time(6h)*
```
