// Downsampling task (section 2.7.5)
// Every minute: average the 1-s points of the previous minute from solar_raw into
// solar_1m (labelled with the start of the minute, like pandas resample), and
// integrate power into energy per minute [Wh].  Tags (channel, mppt, label) are kept,
// so each series of the dataset stays separate.  String fields (mode, label_truth)
// and the seq counter are not averaged.
option task = {name: "downsample_1m", every: 1m, offset: 10s}

raw = from(bucket: "solar_raw")
    |> range(start: -task.every)
    |> filter(fn: (r) => r._measurement == "electrical" or r._measurement == "tracker" or r._measurement == "sim_truth")
    |> filter(fn: (r) => r._field != "mode" and r._field != "seq" and r._field != "label_truth" and r._field != "lat_ms")

raw
    |> aggregateWindow(every: 1m, fn: mean, createEmpty: false, timeSrc: "_start")
    |> to(bucket: "solar_1m")

raw
    |> filter(fn: (r) => r._measurement != "sim_truth" and (r._field == "p_pv" or r._field == "p_out" or r._field == "p_mot"))
    |> integral(unit: 1h)
    |> map(fn: (r) => ({r with _time: r._start, _measurement: "energy", _field: r._field + "_wh"}))
    |> to(bucket: "solar_1m")
