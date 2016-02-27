# influxdb-grafana-rp-proxy
Auto select infuxdb 0.10 Retention Policy

Until influxdb fixes this and enables downsampling of data internally this workaround will do.
- works only with Influx Auth disabled
- have only tested it with tag based measurements (dotted series names need to be fixed)

Original code: @PaulKuiper https://github.com/influxdata/influxdb/issues/2625#issuecomment-161716174

Ubuntu 14.04 setup:
```
apt install python-regex python-bottle python-requests python-gevent
```


Prepare influxdb Database:

1) Create Retencion Policy for every database.
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
3) Backfill historical data XX days.
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

4) Edit config settings, and point Grafana DataSources to host:port defined here  
```
CONFIG = {
    'influxdb_http':'http://localhost:8086',

    'bind_host': '0.0.0.0',
    'bind_port': '3004',

    ..
}
```
