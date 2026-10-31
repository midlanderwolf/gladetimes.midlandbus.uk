cd /root/bustimes.org

echo "Importing other GTFS feeds"
docker compose exec web python ./manage.py import_gtfs_ember
docker compose exec web python ./manage.py import_gtfs_flixbus
docker compose exec web python ./manage.py import_gtfs_nevada
docker compose exec web python ./manage.py import_gtfs_go_metro
docker compose exec web python ./manage.py import_gtfs_nl
docker compose exec web python ./manage.py import_gtfs_marta
docker compose exec web python ./manage.py import_gtfs_hsl
docker compose exec web python ./manage.py import_gtfs_generic "Cap Cotentin" --url "https://www.data.gouv.fr/api/1/datasets/r/2e97c9b3-a59f-42dd-9b9e-a232fa771f21" --filename capcotentin_gtfs.zip --stop-prefix capcotentin --region FR --town-in-stop-names
docker compose exec web python ./manage.py import_gtfs_generic "ZDiTM Szczecin" --url "https://www.zditm.szczecin.pl/storage/gtfs/gtfs.zip" --filename zditm_gtfs.zip --stop-prefix zditm --region PL
echo "Other GTFS feeds import complete"