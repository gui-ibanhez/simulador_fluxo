"""
uv run python slot_optimizer.py \
                                                                --demand test_data/demand_sample_hour_barra.csv \
                                                                --roster test_data/roster_20_mixed.json \
                                                                --year 2025 --month 3 \
                                                                --close-time 22:00 \
                                                                --solver-time-limit 300 \
                                                                --store-type mall \
                                                                --start-step 60 \
                                                                --start-window 10:00 15:00 \
                                                                --normal-duration-hours 8 \
                                                                --special-duration-hours 7 \
                                                                --max-shifts-per-week 5 \
                                                                --min-days-off-per-week 2 \
                                                                --max-consecutive-work-days 5 \
                                                                --sequence-constraint 2 2 0 6 6 0 \
                                                                --no-compensation \
                                                                --start-var-strategy CHOOSE_MIN_DOMAIN_SIZE \
                                                                --start-val-strategy SELECT_MIN_VALUE \
                                                                --works-var-strategy CHOOSE_MIN_DOMAIN_SIZE \
                                                                --works-val-strategy SELECT_MAX_VALUE \
                                                                --excess-penalty 0 \
                                                                --quadratic-excess-penalty 0 \
                                                                --minimize-off-days-penalty 100 \
                                                                --min-work-days-penalty 0 \
                                                                --spread-shifts-penalty 0 \
                                                                --demand-spread-penalty 0 \
                                                                --spread-sunday-shifts-penalty 100 \
                                                                --spread-shifts-penalty 200 \
                                                                --solver-log -vv \
                                                                --show-full-period \
                                                                --max-consecutive-off-days 2 \
                                                                --exact-work-days 30 \
                                                                --exact-work-days-scope full \
                                                                --understaff-penalty 500 \
                                                                > ./results/barra_mixed_5x2_2.txt 2>&1


for n in (seq 16 35)
                                                                set out "./results/n_$n.txt"
                                                                echo "Testing n=$n ..."

                                                                if uv run python slot_optimizer.py \
                                                                                --demand test_data/demand_sample_hour_barra.csv \
                                                                                --roster test_data/roster_barra_mixed.json \
                                                                                --year 2025 --month 3 \
                                                                                --close-time 22:00 \
                                                                                --solver-time-limit 300 \
                                                                                --store-type mall \
                                                                                --no-relax-cover \
                                                                                --min-employees $n --max-employees $n \
                                                                                --start-step 60 \
                                                                                --start-window 10:00 15:00 \
                                                                                --normal-duration-hours 8 \
                                                                                --special-duration-hours 7 \
                                                                                --max-shifts-per-week 6 \
                                                                                --min-days-off-per-week 1 \
                                                                                --max-consecutive-work-days 6 \
                                                                                --sequence-constraint 2 2 0 6 6 0 \
                                                                                --no-compensation \
                                                                                --no-search-strategy \
                                                                                --excess-penalty 0 \
                                                                                --quadratic-excess-penalty 0 \
                                                                                --minimize-off-days-penalty 0 \
                                                                                --min-work-days-penalty 0 \
                                                                                --spread-shifts-penalty 0 \
                                                                                --demand-spread-penalty 0 \
                                                                                --spread-sunday-shifts-penalty 20 \
                                                                                --spread-shifts-penalty 200 \
                                                                                --solver-log -vv \
                                                                                --show-full-period \
                                                                                --max-consecutive-off-days 2 \
                                                                                > $out 2>&1

                                                                    echo "First roster that fully covers demand: n=$n"
                                                                    echo "Log: $out"
                                                                    break
                                                                else
                                                                    echo "n=$n infeasible (or no solution within time limit)"
                                                                end
                                                            end
"""