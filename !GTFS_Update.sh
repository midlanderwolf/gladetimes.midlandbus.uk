cd /root/bustimes.org

echo "Importing other GTFS feeds"
docker compose exec web python ./manage.py import_gtfs_ember
docker compose exec web python ./manage.py import_gtfs_flixbus
docker compose exec web python ./manage.py import_gtfs_feed rtcsnv
docker compose exec web python ./manage.py import_gtfs_feed go_metro
docker compose exec web python ./manage.py import_gtfs_feed marta
docker compose exec web python ./manage.py import_gtfs_feed hsl
docker compose exec web python ./manage.py import_gtfs_feed capcotentin
docker compose exec web python ./manage.py import_gtfs_feed zditm
docker compose exec web python ./manage.py import_gtfs_feed ztmwarszawa
echo "Other GTFS feeds import complete"