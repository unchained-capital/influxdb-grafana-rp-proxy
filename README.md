# influxdb-grafana-rp-proxy

Proxy inserted between Grafana and InfluxDB which auto-selects an
InfuxDB Retention Policy based on the size of the `GROUP BY` window
specified in queries from Grafana.

Until InfluxDB either

a) intelligently merges data points from different retention policies
at query time (perhaps through layers for RPs?)

b) implements downsampling internally

a proxy such as this one will be required to effectively use Grafana
(and possibly many other tools) with an InfluxDB containing
downsampled data.  Watch the
[GitHub issue](https://github.com/influxdata/influxdb/issues/5750) for
progress.

This proxy:

- works only with InfluxDB auth disabled.
- has only been tested with tag based measurements (~~dotted series names need to be fixed~~).
- assumes series names and tags are consistent across retention policies.
- assumes first part of a dotted series name is *NOT* be the same as some existing retention policy name (Not OK: series="5min.hosts.cpu" RP="5min")

Original code: @PaulKuiper https://github.com/influxdata/influxdb/issues/2625#issuecomment-161716174

## Installation

Ubuntu 14.04 setup:

```
$ sudo apt-get install python-regex python-bottle python-requests python-gevent
```

Or use [`pip`](https://docs.python.org/3.6/installing/index.html) one
you've cloned this repo:

```
$ sudo pip install -r requirements.txt
```

## Configuration

The [default configuration file](default.yml) is simple and thoroughly
documented.  Modify it as you need.

## Usage

Launch the proxy with default settings:

```
$ python proxy.py
```

Or pass in a configuration file:

```
$ python proxy.py config.yml
```

Now configure Grafana to use an "InfluxDB server" at the host and port
of the proxy, instead of the actual InfluxDB server.

## Database Preparation

The following is just an example, written to match the
[default configuration](default.yml).

1) Create Retention Policy for every database.
```
ALTER RETENTION POLICY "default" ON "graphite" DURATION 12h REPLICATION 1 DEFAULT
CREATE RETENTION POLICY "10sec"  ON graphite DURATION 2h   REPLICATION 1
CREATE RETENTION POLICY "30sec"  ON graphite DURATION 6h   REPLICATION 1
CREATE RETENTION POLICY "1min"   ON graphite DURATION 24h  REPLICATION 1
CREATE RETENTION POLICY "5min"   ON graphite DURATION 48h  REPLICATION 1
CREATE RETENTION POLICY "30min"  ON graphite DURATION 7d   REPLICATION 1
CREATE RETENTION POLICY "1hour"  ON graphite DURATION 31d  REPLICATION 1
CREATE RETENTION POLICY "3hour"  ON graphite DURATION 93d  REPLICATION 1
CREATE RETENTION POLICY "12hour" ON graphite DURATION 370d REPLICATION 1
CREATE RETENTION POLICY "24hour" ON graphite DURATION inf  REPLICATION 1
```
2) Create Continuous Queries
```
CREATE CONTINUOUS QUERY graphite_cq_10sec  ON graphite BEGIN SELECT mean(value) as value INTO graphite."10sec".:MEASUREMENT  FROM graphite."default"./.*/ GROUP BY time(10s), * END
CREATE CONTINUOUS QUERY graphite_cq_30sec  ON graphite BEGIN SELECT mean(value) as value INTO graphite."30sec".:MEASUREMENT  FROM graphite."default"./.*/ GROUP BY time(30s), * END
CREATE CONTINUOUS QUERY graphite_cq_1min   ON graphite BEGIN SELECT mean(value) as value INTO graphite."1min".:MEASUREMENT   FROM graphite."10sec"./.*/ GROUP BY time(1m), * END
CREATE CONTINUOUS QUERY graphite_cq_5min   ON graphite BEGIN SELECT mean(value) as value INTO graphite."5min".:MEASUREMENT   FROM graphite."30sec"./.*/ GROUP BY time(5m), * END
CREATE CONTINUOUS QUERY graphite_cq_30min  ON graphite BEGIN SELECT mean(value) as value INTO graphite."30min".:MEASUREMENT  FROM graphite."5min"./.*/ GROUP BY time(30m), * END
CREATE CONTINUOUS QUERY graphite_cq_1hour  ON graphite BEGIN SELECT mean(value) as value INTO graphite."1hour".:MEASUREMENT  FROM graphite."5min"./.*/ GROUP BY time(1h), * END
CREATE CONTINUOUS QUERY graphite_cq_3hour  ON graphite BEGIN SELECT mean(value) as value INTO graphite."3hour".:MEASUREMENT  FROM graphite."5min"./.*/ GROUP BY time(3h), * END
CREATE CONTINUOUS QUERY graphite_cq_12hour ON graphite BEGIN SELECT mean(value) as value INTO graphite."12hour".:MEASUREMENT FROM graphite."1hour"./.*/ GROUP BY time(12h), * END
CREATE CONTINUOUS QUERY graphite_cq_24hour ON graphite BEGIN SELECT mean(value) as value INTO graphite."24hour".:MEASUREMENT FROM graphite."1hour"./.*/ GROUP BY time(24h), * END
```
3) Backfill historical data XX days.(only if needed)
```
SELECT mean(value) as value INTO graphite."10sec".:MEASUREMENT FROM graphite."default"./.*/ WHERE time > now() - XXd GROUP BY time(10s),*
SELECT mean(value) as value INTO graphite."30sec".:MEASUREMENT FROM graphite."default"./.*/ WHERE time > now() - XXd GROUP BY time(30s),*
SELECT mean(value) as value INTO graphite."1min".:MEASUREMENT FROM graphite."10sec"./.*/    WHERE time > now() - XXd GROUP BY time(1m),*
SELECT mean(value) as value INTO graphite."5min".:MEASUREMENT FROM graphite."30sec"./.*/    WHERE time > now() - XXd GROUP BY time(5m),*
SELECT mean(value) as value INTO graphite."30min".:MEASUREMENT FROM graphite."5min"./.*/    WHERE time > now() - XXd GROUP BY time(30m),*
SELECT mean(value) as value INTO graphite."1hour".:MEASUREMENT FROM graphite."5min"./.*/    WHERE time > now() - XXd GROUP BY time(1h),*
SELECT mean(value) as value INTO graphite."3hour".:MEASUREMENT FROM graphite."5min"./.*/    WHERE time > now() - XXd GROUP BY time(3h),*
SELECT mean(value) as value INTO graphite."12hour".:MEASUREMENT FROM graphite."1hour"./.*/  WHERE time > now() - XXd GROUP BY time(12h),*
SELECT mean(value) as value INTO graphite."24hour".:MEASUREMENT FROM graphite."1hour"./.*/  WHERE time > now() - XXd GROUP BY time(24h),*
```
