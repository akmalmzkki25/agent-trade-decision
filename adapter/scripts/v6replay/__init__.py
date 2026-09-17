"""
V6 tier-0 replay (scripts/v6_replay.py): how often would a packet reach the operator?

For every stored closed M15 bar the replay rebuilds what the live engine would
have seen at that bar's close, using only bars closed by then, and runs the real
tier-0 modules (context builder, feature map, gates, setup registry, exit plans,
sizing and the operator packet builder). Live-only inputs are synthesized in
`synth`; the list of assumptions is `synth.ASSUMPTIONS`.

    data      bars from the ledger (read-only) or from QlipV6_ExportBars CSV files
    synth     replay settings, the synthetic snapshot, breaker status and bar reader
    evaluate  one bar -> BarRecord (insufficient data, gates, candidates, packet)
    runner    the sequential loop with the one-position / trades-per-day simulation
    tally     counts per day, per UTC hour band and in total
    render    the markdown summary
"""
