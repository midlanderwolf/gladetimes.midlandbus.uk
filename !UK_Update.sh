# docker compose exec web uv run ./manage.py

set -o pipefail

run() {
    local n=1
    local max=5
    local delay=10
    while true; do
        "$@" && break || {
            if [[ $n -lt $max ]]; then
                echo "Command failed (attempt $n/$max). Retrying in $delay seconds..."
                sleep "$delay"
                ((n++))
                delay=$((delay * 2))
            else
                echo "Command failed after $max attempts."
                return 1
            fi
        }
    done
}

echo "Downloading NCSD.zip"
cd /root/bustimes.org/data/TNDS
wget https://coach.bus-data.dft.gov.uk/TxC-2.4.zip
mv TxC-2.4.zip NCSD.zip
echo "NCSD.zip download complete"

# echo "Downloading L.zip"
# cd /root/bustimes.org/data/London
# wget https://tfl.gov.uk/tfl/syndication/feeds/journey-planner-timetables.zip
# mv journey-planner-timetables.zip L.zip
# echo "L.zip download complete"
# run docker compose exec web uv run ./manage.py import_transxchange data/TNDS/L.zip
# echo "TfL import complete"


cd /root/bustimes.org

echo "Updating slugs"
run docker compose exec web uv run ./manage.py update_slugs
echo "Slug update complete"

echo "Updating search indexes"
run docker compose exec web uv run ./manage.py update_search_indexes
echo "Search index update complete"

echo "Importing NetEx Fares"
run docker compose exec web uv run ./manage.py import_netex_fares 825ad872cc647ead18d4d67c52485d558ff3f786
echo " NetEx Fares Import complete"

echo "Importing BODS Data Catalogue"
run docker compose exec web uv run ./manage.py import_bods_data_catalogue
echo "BODS Data Catalogue Import complete"

echo "Importing VOSA"
run docker compose exec web uv run ./manage.py import_vosa
echo "VOSA import complete"

echo "Importing NOC"
run docker compose exec web uv run ./manage.py import_noc
echo "NOC import complete"

echo "Importing BODS Timetables"
run docker compose exec web uv run ./manage.py import_bod_timetables 825ad872cc647ead18d4d67c52485d558ff3f786
echo "BODS Timetables import complete"

echo "Importing Ticketer Timetables"
run docker compose exec web uv run ./manage.py import_bod_timetables ticketer
echo "Ticketer Timetables import complete"

echo "Importing Stagecoach Timetables"
run docker compose exec web uv run ./manage.py import_bod_timetables stagecoach
echo "Stagecoach Timetables import complete"

echo "Importing Passenger Timetables"
run docker compose exec web uv run ./manage.py import_passenger
echo "Passenger Timetables import complete"

# echo "Importing Northern Ireland Timeabltes"
# docker compose exec web uv run ./manage.py import_ni
# echo "Northern Ireland Timeabltes import complete"

echo "Importing National Coach Services (BODS)"
run docker compose exec web uv run ./manage.py import_transxchange data/TNDS/NCSD.zip
echo "National Coach Services (BODS) import complete"

echo "Importing Traveline National Dataset"
run docker compose exec web uv run ./manage.py import_tnds itzmxrkomg@icloud.com itzNot@Mxrk0mg
echo "Traveline National Dataset import complete"

echo "UK Import Complete"
