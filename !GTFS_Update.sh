cd /root/bustimes.org

echo "Importing other GTFS feeds"
docker compose exec web python ./manage.py import_gtfs_ember
docker compose exec web python ./manage.py import_gtfs_flixbus
docker compose exec web python ./manage.py import_gtfs_nevada
docker compose exec web python ./manage.py import_gtfs_go_metro
docker compose exec web python ./manage.py import_gtfs_nl
docker compose exec web python ./manage.py import_gtfs_marta
docker compose exec web python ./manage.py import_gtfs_hsl
echo "Other GTFS feeds import complete"