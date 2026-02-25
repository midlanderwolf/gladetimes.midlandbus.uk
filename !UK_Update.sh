# docker compose exec web uv run ./manage.py

# echo "Downloading NCSD.zip"
# cd /root/bustimes.org/data/TNDS
# wget https://coach.bus-data.dft.gov.uk/TxC-2.4.zip
# mv TxC-2.4.zip NCSD.zip
# echo "NCSD.zip download complete"

# echo "Downloading L.zip"
# cd /root/bustimes.org/data/London
# wget https://tfl.gov.uk/tfl/syndication/feeds/journey-planner-timetables.zip
# mv journey-planner-timetables.zip L.zip
# echo "L.zip download complete"

cd /root/bustimes.org

echo "Updating slugs"
docker compose exec web uv run ./manage.py update_slugs
echo "Slug update complete"

echo "Updating search indexes"
docker compose exec web uv run ./manage.py update_search_indexes
echo "Search index update complete"

# echo "Importing NetEx Fares"
# docker compose exec web uv run ./manage.py import_netex_fares 825ad872cc647ead18d4d67c52485d558ff3f786
# echo " NetEx Fares Import complete"

echo "Importing BODS Data Catalogue"
docker compose exec web uv run ./manage.py import_bods_data_catalogue
echo "BODS Data Catalogue Import complete"

echo "Importing VOSA"
docker compose exec web uv run ./manage.py import_vosa
echo "VOSA import complete"

echo "Importing NOC"
docker compose exec web uv run ./manage.py import_noc
echo "NOC import complete"

echo "Importing BODS Timetables"
docker compose exec web uv run ./manage.py import_bod_timetables 825ad872cc647ead18d4d67c52485d558ff3f786
echo "BODS Timetables import complete"

echo "Importing Ticketer Timetables"
docker compose exec web uv run ./manage.py import_bod_timetables ticketer
echo "Ticketer Timetables import complete"

echo "Importing Stagecoach Timetables"
docker compose exec web uv run ./manage.py import_bod_timetables stagecoach
echo "Stagecoach Timetables import complete"

echo "Importing Passenger Timetables"
docker compose exec web uv run ./manage.py import_passenger
echo "Passenger Timetables import complete"

# echo "Importing Northern Ireland Timeabltes"
# docker compose exec web uv run ./manage.py import_ni
# echo "Northern Ireland Timeabltes import complete"

# echo "Importing Ember Timetables"
# docker compose exec web uv run ./manage.py import_gtfs_ember
# echo "Ember Timetables import complete"

# echo "Importing National Coach Services (BODS)"
# docker compose exec web uv run ./manage.py import_transxchange data/TNDS/NCSD.zip
# echo "National Coach Services (BODS) import complete"

# echo "Importing TfL (BODS)"
# docker compose exec web uv run ./manage.py import_transxchange data/TNDS/L.zip
# echo "TfL import complete"

echo "Importing Traveline National Dataset"
docker compose exec web uv run ./manage.py import_tnds itzmxrkomg@icloud.com itzNot@Mxrk0mg 
echo "Traveline National Dataset import complete"

echo "UK Import Complete"
