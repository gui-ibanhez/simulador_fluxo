#!/usr/bin/env fish

set sizes 15 20 29
set types males mixed
# configs: "max_shifts  days_off  normal_duration_hours"
set configs "5 2 8" "6 1 7"

for size in $sizes
    for type in $types
        set roster "test_data/roster_"$size"_"$type".json"

        if not test -f $roster
            echo "SKIP: $roster not found"
            continue
        end

        for cfg in $configs
            set parts (string split " " $cfg)
            set max_shifts    $parts[1]
            set days_off      $parts[2]
            set normal_hours  $parts[3]

            set outfile "results/"$size"_"$type"_"$max_shifts"_"$days_off".txt"

            echo "=== Running: roster=$roster  max_shifts=$max_shifts  days_off=$days_off  normal_hours=$normal_hours ==="
            echo "    Output: $outfile"

            python slot_optimizer.py \
                --demand test_data/demand_sample_hour.csv \
                --roster $roster \
                --year 2025 --month 3 \
                --solver-time-limit 1800 \
                --store-type mall \
                --start-step 60 \
                --min-work-days-per-month 30 \
                --min-work-days-penalty 500 \
                --understaff-penalty 100 \
                --quadratic-excess-penalty 0 \
                --excess-penalty 0 \
                --demand-spread-penalty 0 \
                --special-day-demand-weight 1 \
                --solver-log -vv \
                --max-shifts-per-week $max_shifts \
                --min-days-off-per-week $days_off \
                --max-consecutive-work-days $max_shifts \
                --normal-duration-hours $normal_hours \
                --minimize-off-days-penalty 100 \
                > $outfile

            echo "    Done (exit $status)"
        end
    end
end

echo "=== All runs complete ==="
