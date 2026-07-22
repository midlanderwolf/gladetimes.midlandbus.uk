cd /root/bustimes.org

echo "Importing other GTFS feeds"
docker compose exec web python ./manage.py import_gtfs_ember
docker compose exec web python ./manage.py import_gtfs_nevada
docker compose exec web uv run ./manage.py import_gtfs_generic "HSL" "HSL"
docker compose exec web uv run ./manage.py import_gtfs_generic "MARTA" "MARTA" 
echo "Other GTFS feeds import complete"